package com.clipvault.app.otp

import android.content.Context
import android.os.Build
import android.os.SystemClock
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.AtomicFile
import org.json.JSONObject
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.DataInputStream
import java.io.DataOutputStream
import java.io.File
import java.io.IOException
import java.security.KeyStore
import java.security.MessageDigest
import java.util.Base64
import java.util.UUID
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

private const val OTP_PAIR_KEY_ALIAS = "clipvault_android_otp_pair_v1"
private const val OTP_PAIR_FILE = "otp_pair_v1.bin"
private const val OTP_PAIR_FRAME_VERSION = 1
private const val OTP_PAIR_NONCE_CAPACITY = 4_096
private const val OTP_PAIR_RESPONSE_MAX_BYTES = 1_024
private val OTP_PAIR_FILE_MAGIC = byteArrayOf('C'.code.toByte(), 'V'.code.toByte(), 'A'.code.toByte(), 'K'.code.toByte())
private val OTP_PAIR_STATE_MAGIC = byteArrayOf('C'.code.toByte(), 'V'.code.toByte(), 'P'.code.toByte(), 'S'.code.toByte())
private val OTP_PAIR_FILE_AAD = "ClipVault Android OTP Pair v1".toByteArray(Charsets.UTF_8)

class OtpPairSummary(
    val sessionEpoch: String,
    val senderDeviceId: String,
    val targetDeviceId: String,
    val highSequence: Long,
) {
    override fun toString(): String = "<OtpPairSummary redacted sequence=$highSequence>"
}

private class OwnedPairState(
    val sessionEpoch: String,
    val senderDeviceId: String,
    val targetDeviceId: String,
    val verifier: ByteArray,
    var highSequence: Long,
    val nonceDigests: MutableList<ByteArray>,
) : AutoCloseable {
    override fun close() {
        verifier.wipe()
        nonceDigests.forEach(ByteArray::wipe)
        nonceDigests.clear()
    }
}

/**
 * The verifier, sequence and nonce history are one Keystore-sealed atomic record in
 * no-backup storage. Sequence/nonce reservation is committed before a lease escapes.
 */
class AndroidOtpPairStore(context: Context) : OtpPairMaterialPort {
    private val appContext = context.applicationContext
    private val atomicFile = AtomicFile(File(appContext.noBackupFilesDir, OTP_PAIR_FILE))
    private val lock = Any()

    fun summary(): OtpPairSummary? = synchronized(lock) {
        val state = readStateLocked() ?: return@synchronized null
        state.use {
            OtpPairSummary(it.sessionEpoch, it.senderDeviceId, it.targetDeviceId, it.highSequence)
        }
    }

    /** The response buffer is ownership-transferred and wiped on every path. */
    fun importPairResponse(ownedResponse: ByteArray, expectedSenderDeviceId: String): Boolean {
        var verifier: ByteArray? = null
        try {
            if (ownedResponse.isEmpty() || ownedResponse.size > OTP_PAIR_RESPONSE_MAX_BYTES) return false
            canonicalDevice(expectedSenderDeviceId, "local OTP sender")
            val text = ownedResponse.toString(Charsets.UTF_8)
            if (!hasExactFlatResponseKeys(text)) return false
            val body = JSONObject(text)
            val version = body.opt("version")
            if (version !is Int || version != 1) return false
            val session = body.opt("session_epoch") as? String ?: return false
            val sender = body.opt("sender_device_id") as? String ?: return false
            val target = body.opt("target_device_id") as? String ?: return false
            val encodedVerifier = body.opt("verifier") as? String ?: return false
            canonicalUuid4(session, "pair response session")
            canonicalDevice(sender, "pair response sender")
            canonicalDevice(target, "pair response target")
            if (sender != expectedSenderDeviceId || sender == target) return false
            if (!Regex("^[A-Za-z0-9_-]{43}$").matches(encodedVerifier)) return false
            verifier = try { Base64.getUrlDecoder().decode(encodedVerifier) }
            catch (_: IllegalArgumentException) { return false }
            if (verifier.size != OTP_PAIR_VERIFIER_BYTES) return false
            if (Base64.getUrlEncoder().withoutPadding().encodeToString(verifier) != encodedVerifier) return false

            synchronized(lock) {
                if (atomicFile.baseFile.exists()) return false
                val state = OwnedPairState(session, sender, target, verifier.copyOf(), 0L, mutableListOf())
                state.use { writeStateLocked(it) }
            }
            return true
        } catch (_: Exception) {
            return false
        } finally {
            verifier?.wipe()
            ownedResponse.wipe()
        }
    }

    override fun acquire(
        authorization: OtpCaptureAuthorization,
        nonce: ByteArray,
    ): OtpPairMaterialLease? = synchronized(lock) {
        if (nonce.size != OTP_NONCE_BYTES) return@synchronized null
        if (SystemClock.elapsedRealtime() >= authorization.expiresAtMonotonicMs) return@synchronized null
        val state = readStateLocked() ?: return@synchronized null
        state.use {
            if (
                it.sessionEpoch != authorization.sessionEpoch ||
                it.senderDeviceId != authorization.senderDeviceId ||
                it.targetDeviceId != authorization.targetDeviceId ||
                it.highSequence == Long.MAX_VALUE ||
                it.nonceDigests.size >= OTP_PAIR_NONCE_CAPACITY
            ) return@synchronized null

            val digest = MessageDigest.getInstance("SHA-256").digest(nonce)
            try {
                if (it.nonceDigests.any { existing -> MessageDigest.isEqual(existing, digest) }) {
                    return@synchronized null
                }
                it.highSequence += 1L
                it.nonceDigests += digest.copyOf()
                // A crash or failed HTTP request consumes the reservation. Never roll it back.
                writeStateLocked(it)
                OtpPairMaterialLease(
                    it.sessionEpoch,
                    it.senderDeviceId,
                    it.targetDeviceId,
                    it.highSequence,
                    it.verifier.copyOf(),
                )
            } finally {
                digest.wipe()
            }
        }
    }

    fun clear(): Boolean = synchronized(lock) {
        var success = true
        try {
            atomicFile.delete()
        } catch (_: Exception) {
            success = false
        }
        try {
            val keyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
            if (keyStore.containsAlias(OTP_PAIR_KEY_ALIAS)) keyStore.deleteEntry(OTP_PAIR_KEY_ALIAS)
        } catch (_: Exception) {
            success = false
        }
        success
    }

    override fun close() = Unit
    override fun toString(): String = "<AndroidOtpPairStore redacted>"

    private fun hasExactFlatResponseKeys(text: String): Boolean {
        val expected = setOf("version", "session_epoch", "sender_device_id", "target_device_id", "verifier")
        val keys = Regex("\\\"([^\\\"\\\\]+)\\\"\\s*:").findAll(text).map { it.groupValues[1] }.toList()
        return keys.size == expected.size && keys.toSet() == expected
    }

    private fun readStateLocked(): OwnedPairState? {
        if (!atomicFile.baseFile.exists()) return null
        var raw: ByteArray? = null
        var magic: ByteArray? = null
        var iv: ByteArray? = null
        var encrypted: ByteArray? = null
        var plaintext: ByteArray? = null
        try {
            val rawBytes = atomicFile.openRead().use { it.readBytes() }.also { raw = it }
            DataInputStream(ByteArrayInputStream(rawBytes)).use { input ->
                val frameMagic = ByteArray(4).also(input::readFully).also { magic = it }
                if (!frameMagic.contentEquals(OTP_PAIR_FILE_MAGIC)) throw IOException("invalid OTP pair frame")
                if (input.readUnsignedByte() != OTP_PAIR_FRAME_VERSION) throw IOException("invalid OTP pair version")
                if (input.readUnsignedByte() != 0 || input.readUnsignedByte() != 0 || input.readUnsignedByte() != 0) {
                    throw IOException("invalid OTP pair reserved bytes")
                }
                val ivLength = input.readUnsignedByte()
                if (ivLength != 12) throw IOException("invalid OTP pair IV")
                val frameIv = ByteArray(ivLength).also(input::readFully).also { iv = it }
                val encryptedLength = input.readInt()
                if (encryptedLength !in 32..(256 * 1024)) throw IOException("invalid OTP pair ciphertext")
                encrypted = ByteArray(encryptedLength).also(input::readFully)
                if (input.read() != -1) throw IOException("trailing OTP pair bytes")
                val cipher = Cipher.getInstance("AES/GCM/NoPadding")
                cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, frameIv))
                cipher.updateAAD(OTP_PAIR_FILE_AAD)
                plaintext = cipher.doFinal(encrypted)
            }
            return decodeState(plaintext ?: throw IOException("empty OTP pair state"))
        } catch (_: Exception) {
            // Corruption or key invalidation is terminal for this pair. It cannot fall back to plaintext.
            try { atomicFile.delete() } catch (_: Exception) { }
            return null
        } finally {
            raw?.wipe(); magic?.wipe(); iv?.wipe(); encrypted?.wipe(); plaintext?.wipe()
        }
    }

    private fun writeStateLocked(state: OwnedPairState) {
        val plaintext = encodeState(state)
        var encrypted: ByteArray? = null
        var frame: ByteArray? = null
        try {
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.ENCRYPT_MODE, key())
            cipher.updateAAD(OTP_PAIR_FILE_AAD)
            encrypted = cipher.doFinal(plaintext)
            val output = ByteArrayOutputStream(encrypted.size + 32)
            DataOutputStream(output).use { data ->
                data.write(OTP_PAIR_FILE_MAGIC)
                data.writeByte(OTP_PAIR_FRAME_VERSION)
                data.write(byteArrayOf(0, 0, 0))
                data.writeByte(cipher.iv.size)
                data.write(cipher.iv)
                data.writeInt(encrypted.size)
                data.write(encrypted)
            }
            frame = output.toByteArray()
            val stream = atomicFile.startWrite()
            try {
                stream.write(frame)
                stream.fd.sync()
                atomicFile.finishWrite(stream)
            } catch (exc: Exception) {
                atomicFile.failWrite(stream)
                throw exc
            }
        } finally {
            plaintext.wipe(); encrypted?.wipe(); frame?.wipe()
        }
    }

    private fun encodeState(state: OwnedPairState): ByteArray {
        val output = ByteArrayOutputStream(128 + state.nonceDigests.size * 32)
        DataOutputStream(output).use { data ->
            data.write(OTP_PAIR_STATE_MAGIC)
            data.writeByte(OTP_PAIR_FRAME_VERSION)
            data.write(byteArrayOf(0, 0, 0))
            data.write(uuidBytes(state.sessionEpoch))
            data.write(uuidBytes(state.senderDeviceId.removePrefix("device:")))
            data.write(uuidBytes(state.targetDeviceId.removePrefix("device:")))
            data.writeLong(state.highSequence)
            data.write(state.verifier)
            data.writeInt(state.nonceDigests.size)
            state.nonceDigests.forEach {
                if (it.size != 32) throw IOException("invalid nonce digest")
                data.write(it)
            }
        }
        return output.toByteArray()
    }

    private fun decodeState(plaintext: ByteArray): OwnedPairState {
        DataInputStream(ByteArrayInputStream(plaintext)).use { input ->
            val magic = ByteArray(4).also(input::readFully)
            if (!magic.contentEquals(OTP_PAIR_STATE_MAGIC)) throw IOException("invalid OTP pair state")
            if (input.readUnsignedByte() != OTP_PAIR_FRAME_VERSION) throw IOException("invalid OTP pair state version")
            if (input.readUnsignedByte() != 0 || input.readUnsignedByte() != 0 || input.readUnsignedByte() != 0) {
                throw IOException("invalid OTP pair state reserved bytes")
            }
            val session = readUuid(input)
            val sender = "device:${readUuid(input)}"
            val target = "device:${readUuid(input)}"
            val sequence = input.readLong()
            if (sequence < 0L) throw IOException("invalid OTP pair sequence")
            val verifier = ByteArray(OTP_PAIR_VERIFIER_BYTES).also(input::readFully)
            val count = input.readInt()
            if (count !in 0..OTP_PAIR_NONCE_CAPACITY) {
                verifier.wipe(); throw IOException("invalid OTP nonce history")
            }
            val nonces = mutableListOf<ByteArray>()
            try {
                repeat(count) { nonces += ByteArray(32).also(input::readFully) }
                if (input.read() != -1) throw IOException("trailing OTP pair state")
                canonicalUuid4(session, "stored session")
                canonicalDevice(sender, "stored sender")
                canonicalDevice(target, "stored target")
                if (sender == target) throw IOException("invalid stored devices")
                return OwnedPairState(session, sender, target, verifier, sequence, nonces)
            } catch (exc: Exception) {
                verifier.wipe(); nonces.forEach(ByteArray::wipe); throw exc
            }
        }
    }

    private fun uuidBytes(value: String): ByteArray {
        val uuid = canonicalUuid4(value, "stored UUID")
        return ByteArrayOutputStream(16).also { output ->
            DataOutputStream(output).use { data ->
                data.writeLong(uuid.mostSignificantBits); data.writeLong(uuid.leastSignificantBits)
            }
        }.toByteArray()
    }

    private fun readUuid(input: DataInputStream): String = UUID(input.readLong(), input.readLong()).toString()

    private fun key(): SecretKey {
        val keyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        if (!keyStore.containsAlias(OTP_PAIR_KEY_ALIAS)) {
            val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
            val builder = KeyGenParameterSpec.Builder(
                OTP_PAIR_KEY_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setRandomizedEncryptionRequired(true)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) builder.setUnlockedDeviceRequired(true)
            generator.init(builder.build())
            generator.generateKey()
        }
        return (keyStore.getEntry(OTP_PAIR_KEY_ALIAS, null) as KeyStore.SecretKeyEntry).secretKey
    }
}
