package com.clipvault.app.otp

import android.content.Context
import android.os.Build
import android.os.SystemClock
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyPermanentlyInvalidatedException
import android.security.keystore.KeyProperties
import android.util.AtomicFile
import org.json.JSONObject
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.DataInputStream
import java.io.DataOutputStream
import java.io.File
import java.io.FileNotFoundException
import java.io.IOException
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.charset.CharacterCodingException
import java.nio.charset.CodingErrorAction
import java.security.KeyStore
import java.security.MessageDigest
import java.util.Base64
import java.util.UUID
import javax.crypto.AEADBadTagException
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

private const val OTP_PAIR_KEY_ALIAS = "clipvault_android_otp_pair_v1"
private const val OTP_PAIR_FILE = "otp_pair_v1.bin"
private const val OTP_PAIR_FRAME_VERSION = 1
private const val OTP_PAIR_RESPONSE_MAX_BYTES = 1_024
private const val OTP_PAIR_FRAME_OVERHEAD_BYTES = 25
private const val OTP_PAIR_CIPHERTEXT_MIN_BYTES = 32
private const val OTP_PAIR_CIPHERTEXT_MAX_BYTES = 256 * 1_024
private const val OTP_PAIR_FILE_MIN_BYTES =
    OTP_PAIR_FRAME_OVERHEAD_BYTES + OTP_PAIR_CIPHERTEXT_MIN_BYTES
private const val OTP_PAIR_FILE_MAX_BYTES =
    OTP_PAIR_FRAME_OVERHEAD_BYTES + OTP_PAIR_CIPHERTEXT_MAX_BYTES
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

enum class OtpPairState {
    UNPAIRED,
    READY,
    ROTATION_REQUIRED,
    UNAVAILABLE,
}

enum class OtpPairImportResult {
    IMPORTED,
    CONFLICT,
    UNAVAILABLE,
    REJECTED,
}

class OtpPairInspection internal constructor(
    val state: OtpPairState,
    val summary: OtpPairSummary?,
) {
    val repairRequired: Boolean
        get() = state == OtpPairState.ROTATION_REQUIRED

    override fun toString(): String = "<OtpPairInspection redacted state=$state>"
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

private sealed class StoredPairReadResult {
    class Available(val state: OwnedPairState) : StoredPairReadResult()
    object Unpaired : StoredPairReadResult()
    object Unavailable : StoredPairReadResult()
}

private enum class AtomicRecordPresence {
    ABSENT,
    PRESENT,
    UNAVAILABLE,
}

private class OtpPairStateCorruptException(cause: Throwable? = null) :
    IOException("invalid OTP pair state", cause)

private class OtpPairKeyMissingException : Exception("OTP pair key is missing")

/**
 * The verifier, sequence and nonce history are one Keystore-sealed atomic record in
 * no-backup storage. Sequence/nonce reservation is committed before a lease escapes.
 */
class AndroidOtpPairStore(context: Context) : OtpPairMaterialPort {
    private val appContext = context.applicationContext
    private val pairFile = File(appContext.noBackupFilesDir, OTP_PAIR_FILE)
    private val atomicFile = AtomicFile(pairFile)
    private val legacyBackupFile = File(pairFile.path + ".bak")
    private val pendingNewFile = File(pairFile.path + ".new")
    private val lock = Any()

    fun inspect(): OtpPairInspection = synchronized(lock) {
        when (val stored = readStateLocked()) {
            is StoredPairReadResult.Available -> stored.state.use {
                val summary = OtpPairSummary(
                    it.sessionEpoch,
                    it.senderDeviceId,
                    it.targetDeviceId,
                    it.highSequence,
                )
                val state = if (
                    it.highSequence >= OTP_PAIR_NONCE_HISTORY_CAPACITY ||
                    it.nonceDigests.size >= OTP_PAIR_NONCE_HISTORY_CAPACITY
                ) {
                    OtpPairState.ROTATION_REQUIRED
                } else {
                    OtpPairState.READY
                }
                OtpPairInspection(state, summary)
            }
            StoredPairReadResult.Unpaired -> OtpPairInspection(OtpPairState.UNPAIRED, null)
            StoredPairReadResult.Unavailable -> OtpPairInspection(OtpPairState.UNAVAILABLE, null)
        }
    }

    fun summary(): OtpPairSummary? = inspect().summary

    /** The response buffer is ownership-transferred and wiped on every path. */
    fun importPairResponse(
        ownedResponse: ByteArray,
        expectedSenderDeviceId: String,
    ): OtpPairImportResult {
        var verifier: ByteArray? = null
        try {
            if (ownedResponse.isEmpty() || ownedResponse.size > OTP_PAIR_RESPONSE_MAX_BYTES) {
                return OtpPairImportResult.REJECTED
            }
            canonicalDevice(expectedSenderDeviceId, "local OTP sender")
            val text = decodeUtf8Strict(ownedResponse)
            if (!hasExactFlatResponseKeys(text)) return OtpPairImportResult.REJECTED
            val body = JSONObject(text)
            val version = body.opt("version")
            if (version !is Int || version != 1) return OtpPairImportResult.REJECTED
            val session = body.opt("session_epoch") as? String
                ?: return OtpPairImportResult.REJECTED
            val sender = body.opt("sender_device_id") as? String
                ?: return OtpPairImportResult.REJECTED
            val target = body.opt("target_device_id") as? String
                ?: return OtpPairImportResult.REJECTED
            val encodedVerifier = body.opt("verifier") as? String
                ?: return OtpPairImportResult.REJECTED
            canonicalUuid4(session, "pair response session")
            canonicalDevice(sender, "pair response sender")
            canonicalDevice(target, "pair response target")
            if (sender != expectedSenderDeviceId || sender == target) {
                return OtpPairImportResult.REJECTED
            }
            if (!Regex("^[A-Za-z0-9_-]{43}$").matches(encodedVerifier)) {
                return OtpPairImportResult.REJECTED
            }
            verifier = try { Base64.getUrlDecoder().decode(encodedVerifier) }
            catch (_: IllegalArgumentException) { return OtpPairImportResult.REJECTED }
            if (verifier.size != OTP_PAIR_VERIFIER_BYTES) return OtpPairImportResult.REJECTED
            val canonicalEncoding = Base64.getUrlEncoder().withoutPadding().encode(verifier)
            val suppliedEncoding = encodedVerifier.toByteArray(Charsets.US_ASCII)
            try {
                if (!MessageDigest.isEqual(canonicalEncoding, suppliedEncoding)) {
                    return OtpPairImportResult.REJECTED
                }
            } finally {
                canonicalEncoding.wipe()
                suppliedEncoding.wipe()
            }

            synchronized(lock) {
                when (atomicRecordPresenceLocked()) {
                    AtomicRecordPresence.PRESENT -> return OtpPairImportResult.CONFLICT
                    AtomicRecordPresence.UNAVAILABLE -> return OtpPairImportResult.UNAVAILABLE
                    AtomicRecordPresence.ABSENT -> Unit
                }
                val state = OwnedPairState(session, sender, target, verifier.copyOf(), 0L, mutableListOf())
                try {
                    state.use { writeStateLocked(it) }
                } catch (_: Exception) {
                    return OtpPairImportResult.UNAVAILABLE
                }
            }
            return OtpPairImportResult.IMPORTED
        } catch (_: Exception) {
            return OtpPairImportResult.REJECTED
        } finally {
            verifier?.wipe()
            ownedResponse.wipe()
        }
    }

    override fun acquire(
        authorization: OtpCaptureAuthorization,
        nonce: ByteArray,
    ): OtpPairMaterialAcquireResult = synchronized(lock) {
        if (nonce.size != OTP_NONCE_BYTES) {
            return@synchronized OtpPairMaterialAcquireResult.Mismatch
        }
        if (SystemClock.elapsedRealtime() >= authorization.expiresAtMonotonicMs) {
            return@synchronized OtpPairMaterialAcquireResult.Mismatch
        }
        val stored = when (val result = readStateLocked()) {
            is StoredPairReadResult.Available -> result.state
            StoredPairReadResult.Unpaired -> return@synchronized OtpPairMaterialAcquireResult.Unpaired
            StoredPairReadResult.Unavailable -> {
                return@synchronized OtpPairMaterialAcquireResult.Unavailable
            }
        }
        stored.use {
            if (
                it.sessionEpoch != authorization.sessionEpoch ||
                it.senderDeviceId != authorization.senderDeviceId ||
                it.targetDeviceId != authorization.targetDeviceId
            ) return@synchronized OtpPairMaterialAcquireResult.Mismatch
            if (
                it.highSequence >= OTP_PAIR_NONCE_HISTORY_CAPACITY ||
                it.nonceDigests.size >= OTP_PAIR_NONCE_HISTORY_CAPACITY
            ) return@synchronized OtpPairMaterialAcquireResult.RotationRequired

            val digest = MessageDigest.getInstance("SHA-256").digest(nonce)
            try {
                if (it.nonceDigests.any { existing -> MessageDigest.isEqual(existing, digest) }) {
                    return@synchronized OtpPairMaterialAcquireResult.Mismatch
                }
                it.highSequence += 1L
                it.nonceDigests += digest.copyOf()
                // A crash or failed HTTP request consumes the reservation. Never roll it back.
                try {
                    writeStateLocked(it)
                    OtpPairMaterialAcquireResult.Acquired(
                        OtpPairMaterialLease(
                            it.sessionEpoch,
                            it.senderDeviceId,
                            it.targetDeviceId,
                            it.highSequence,
                            it.verifier.copyOf(),
                        ),
                    )
                } catch (_: Exception) {
                    OtpPairMaterialAcquireResult.Unavailable
                }
            } finally {
                digest.wipe()
            }
        }
    }

    fun clear(): Boolean = synchronized(lock) {
        // Delete the sealing key first. If Keystore is temporarily unavailable,
        // retain the sealed record so status remains retryable instead of
        // claiming UNPAIRED while an orphaned (possibly invalid) alias remains.
        try {
            val keyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
            if (keyStore.containsAlias(OTP_PAIR_KEY_ALIAS)) keyStore.deleteEntry(OTP_PAIR_KEY_ALIAS)
        } catch (_: Exception) {
            return@synchronized false
        }
        try {
            atomicFile.delete()
        } catch (_: Exception) {
            return@synchronized false
        }
        atomicRecordPresenceLocked() == AtomicRecordPresence.ABSENT
    }

    override fun close() = Unit
    override fun toString(): String = "<AndroidOtpPairStore redacted>"

    private fun hasExactFlatResponseKeys(text: String): Boolean {
        val expected = setOf("version", "session_epoch", "sender_device_id", "target_device_id", "verifier")
        val keys = Regex("\\\"([^\\\"\\\\]+)\\\"\\s*:").findAll(text).map { it.groupValues[1] }.toList()
        return keys.size == expected.size && keys.toSet() == expected
    }

    private fun decodeUtf8Strict(value: ByteArray): String = try {
        Charsets.UTF_8.newDecoder()
            .onMalformedInput(CodingErrorAction.REPORT)
            .onUnmappableCharacter(CodingErrorAction.REPORT)
            .decode(ByteBuffer.wrap(value))
            .toString()
    } catch (exc: CharacterCodingException) {
        throw IOException("invalid OTP pair response encoding", exc)
    }

    private fun atomicResidueExistsLocked(): Boolean =
        pairFile.exists() || legacyBackupFile.exists() || pendingNewFile.exists()

    /** openRead applies AtomicFile's backup recovery before reporting presence. */
    private fun atomicRecordPresenceLocked(): AtomicRecordPresence = try {
        atomicFile.openRead().use { }
        AtomicRecordPresence.PRESENT
    } catch (_: FileNotFoundException) {
        if (atomicResidueExistsLocked()) {
            AtomicRecordPresence.UNAVAILABLE
        } else {
            AtomicRecordPresence.ABSENT
        }
    } catch (_: Exception) {
        AtomicRecordPresence.UNAVAILABLE
    }

    private fun readStateLocked(): StoredPairReadResult {
        var raw: ByteArray? = null
        var magic: ByteArray? = null
        var iv: ByteArray? = null
        var encrypted: ByteArray? = null
        var plaintext: ByteArray? = null
        try {
            val input = try {
                atomicFile.openRead()
            } catch (_: FileNotFoundException) {
                return if (atomicResidueExistsLocked()) {
                    StoredPairReadResult.Unavailable
                } else {
                    StoredPairReadResult.Unpaired
                }
            } catch (_: Exception) {
                return StoredPairReadResult.Unavailable
            }
            val rawBytes = try {
                input.use {
                    val length = input.channel.size()
                    if (length !in OTP_PAIR_FILE_MIN_BYTES.toLong()..OTP_PAIR_FILE_MAX_BYTES.toLong()) {
                        throw OtpPairStateCorruptException()
                    }
                    ByteArray(length.toInt()).also { bytes ->
                        DataInputStream(input).readFully(bytes)
                        if (input.read() != -1) throw OtpPairStateCorruptException()
                        raw = bytes
                    }
                }
            } catch (exc: OtpPairStateCorruptException) {
                throw exc
            } catch (_: Exception) {
                return StoredPairReadResult.Unavailable
            }
            try {
                DataInputStream(ByteArrayInputStream(rawBytes)).use { input ->
                    val frameMagic = ByteArray(4).also(input::readFully).also { magic = it }
                    if (!frameMagic.contentEquals(OTP_PAIR_FILE_MAGIC)) throw IOException("invalid OTP pair frame")
                    if (input.readUnsignedByte() != OTP_PAIR_FRAME_VERSION) throw IOException("invalid OTP pair version")
                    if (input.readUnsignedByte() != 0 || input.readUnsignedByte() != 0 || input.readUnsignedByte() != 0) {
                        throw IOException("invalid OTP pair reserved bytes")
                    }
                    val ivLength = input.readUnsignedByte()
                    if (ivLength != 12) throw IOException("invalid OTP pair IV")
                    ByteArray(ivLength).also(input::readFully).also { iv = it }
                    val encryptedLength = input.readInt()
                    if (encryptedLength !in OTP_PAIR_CIPHERTEXT_MIN_BYTES..OTP_PAIR_CIPHERTEXT_MAX_BYTES) {
                        throw IOException("invalid OTP pair ciphertext")
                    }
                    encrypted = ByteArray(encryptedLength).also(input::readFully)
                    if (input.read() != -1) throw IOException("trailing OTP pair bytes")
                }
            } catch (exc: Exception) {
                throw OtpPairStateCorruptException(exc)
            }

            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(
                Cipher.DECRYPT_MODE,
                key(createIfMissing = false),
                GCMParameterSpec(128, requireNotNull(iv)),
            )
            cipher.updateAAD(OTP_PAIR_FILE_AAD)
            plaintext = cipher.doFinal(requireNotNull(encrypted))
            val decoded = try {
                decodeState(plaintext ?: throw IOException("empty OTP pair state"))
            } catch (exc: Exception) {
                throw OtpPairStateCorruptException(exc)
            }
            return StoredPairReadResult.Available(decoded)
        } catch (_: OtpPairStateCorruptException) {
            return discardTerminalStateLocked(deleteKey = false)
        } catch (_: OtpPairKeyMissingException) {
            return discardTerminalStateLocked(deleteKey = true)
        } catch (_: KeyPermanentlyInvalidatedException) {
            return discardTerminalStateLocked(deleteKey = true)
        } catch (_: AEADBadTagException) {
            return discardTerminalStateLocked(deleteKey = false)
        } catch (_: Exception) {
            // Locked-device Keystore failures and transient provider/I/O errors
            // must never destroy a still-valid sealed pair record.
            return StoredPairReadResult.Unavailable
        } finally {
            raw?.wipe(); magic?.wipe(); iv?.wipe(); encrypted?.wipe(); plaintext?.wipe()
        }
    }

    private fun discardTerminalStateLocked(deleteKey: Boolean): StoredPairReadResult {
        if (deleteKey) {
            try {
                val keyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
                if (keyStore.containsAlias(OTP_PAIR_KEY_ALIAS)) {
                    keyStore.deleteEntry(OTP_PAIR_KEY_ALIAS)
                }
            } catch (_: Exception) {
                // Keep the sealed record so a later unlocked read can retry the
                // terminal cleanup instead of claiming that re-pair is ready.
                return StoredPairReadResult.Unavailable
            }
        }
        try {
            atomicFile.delete()
        } catch (_: Exception) {
            return StoredPairReadResult.Unavailable
        }
        return when (atomicRecordPresenceLocked()) {
            AtomicRecordPresence.ABSENT -> StoredPairReadResult.Unpaired
            AtomicRecordPresence.PRESENT,
            AtomicRecordPresence.UNAVAILABLE -> StoredPairReadResult.Unavailable
        }
    }

    private fun writeStateLocked(state: OwnedPairState) {
        val plaintext = encodeState(state)
        var encrypted: ByteArray? = null
        var frame: ByteArray? = null
        try {
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.ENCRYPT_MODE, key(createIfMissing = true))
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
        val encoded = ByteArray(100 + state.nonceDigests.size * 32)
        try {
            val output = ByteBuffer.wrap(encoded).order(ByteOrder.BIG_ENDIAN)
            output.put(OTP_PAIR_STATE_MAGIC)
            output.put(OTP_PAIR_FRAME_VERSION.toByte())
            output.put(byteArrayOf(0, 0, 0))
            putUuid(output, state.sessionEpoch)
            putUuid(output, state.senderDeviceId.removePrefix("device:"))
            putUuid(output, state.targetDeviceId.removePrefix("device:"))
            output.putLong(state.highSequence)
            output.put(state.verifier)
            output.putInt(state.nonceDigests.size)
            state.nonceDigests.forEach {
                if (it.size != 32) throw IOException("invalid nonce digest")
                output.put(it)
            }
            return encoded
        } catch (exc: Exception) {
            encoded.wipe()
            throw exc
        }
    }

    private fun putUuid(output: ByteBuffer, value: String) {
        val uuid = canonicalUuid4(value, "stored UUID")
        output.putLong(uuid.mostSignificantBits)
        output.putLong(uuid.leastSignificantBits)
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
            if (sequence !in 0L..OTP_PAIR_NONCE_HISTORY_CAPACITY.toLong()) {
                throw IOException("invalid OTP pair sequence")
            }
            val verifier = ByteArray(OTP_PAIR_VERIFIER_BYTES).also(input::readFully)
            val count = input.readInt()
            if (count !in 0..OTP_PAIR_NONCE_HISTORY_CAPACITY) {
                verifier.wipe(); throw IOException("invalid OTP nonce history")
            }
            if (sequence != count.toLong()) {
                verifier.wipe(); throw IOException("OTP pair sequence/history mismatch")
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

    private fun readUuid(input: DataInputStream): String = UUID(input.readLong(), input.readLong()).toString()

    private fun key(createIfMissing: Boolean): SecretKey {
        val keyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        if (!keyStore.containsAlias(OTP_PAIR_KEY_ALIAS)) {
            if (!createIfMissing) throw OtpPairKeyMissingException()
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
        val entry = keyStore.getEntry(OTP_PAIR_KEY_ALIAS, null) as? KeyStore.SecretKeyEntry
            ?: throw OtpPairKeyMissingException()
        return entry.secretKey
    }
}
