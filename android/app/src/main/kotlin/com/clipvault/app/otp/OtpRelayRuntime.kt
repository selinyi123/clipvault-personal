package com.clipvault.app.otp

import android.Manifest
import android.app.KeyguardManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import androidx.core.content.ContextCompat
import com.clipvault.app.BuildConfig
import com.clipvault.app.sync.OtpHttpOnlineTransport as SecureOtpHttpOnlineTransport
import com.clipvault.app.sync.Settings

class OtpRuntimeStatus(
    val smsCaptureIncluded: Boolean,
    val smsPermissionGranted: Boolean,
    val paired: Boolean,
    val activeGrant: Boolean,
    val userConsentSessionActive: Boolean,
    val highSequence: Long,
) {
    override fun toString(): String =
        "<OtpRuntimeStatus redacted paired=$paired active=$activeGrant consent=$userConsentSessionActive>"
}

private class OneShotCapturePort(
    override val source: OtpCaptureSource,
    private val grantId: String,
    private val targetDeviceId: String,
    ownedCharacters: CharArray,
) : OtpCapturePort {
    private var characters: CharArray? = ownedCharacters

    @Synchronized
    override fun capture(authorization: OtpCaptureAuthorization): IsolatedOtpCandidate? {
        val value = characters ?: return null
        characters = null
        return IsolatedOtpCandidate(source, grantId, targetDeviceId, value)
    }

    @Synchronized
    override fun close() {
        characters?.wipe(); characters = null
    }
}

/** Process singleton. Only metadata/pair state persist; capture grants and OTPs do not. */
object OtpRelayRuntime {
    private val initializationLock = Any()
    @Volatile private var initialized = false
    private lateinit var appContext: Context
    private lateinit var pairStore: AndroidOtpPairStore
    private lateinit var settings: OtpRuntimeSettings
    private lateinit var grants: OtpCaptureGrantController
    private lateinit var userConsentSessions: OtpUserConsentSessionController
    private val handler = Handler(Looper.getMainLooper())
    private val expiryRunnable = Runnable { revokeCapture() }
    private val userConsentExpiryRunnable = Runnable { cancelUserConsentSessionInternal() }

    fun initialize(context: Context) = synchronized(initializationLock) {
        if (initialized) return
        appContext = context.applicationContext
        pairStore = AndroidOtpPairStore(appContext)
        settings = OtpRuntimeSettings(appContext)
        grants = OtpCaptureGrantController(SystemClock::elapsedRealtime)
        userConsentSessions = OtpUserConsentSessionController(SystemClock::elapsedRealtime)
        // A prior-process opt-in never fabricates a fresh in-memory grant.
        try { settings.captureOptIn = false } catch (_: Exception) { }
        registerSecurityReceiver()
        initialized = true
    }

    fun status(context: Context): OtpRuntimeStatus {
        ensureInitialized(context)
        val summary = pairStore.summary()
        return OtpRuntimeStatus(
            smsCaptureIncluded = BuildConfig.OTP_SMS_CAPTURE_INCLUDED,
            smsPermissionGranted = hasSmsPermission(),
            paired = summary != null,
            activeGrant = settings.captureOptIn && grants.current() != null,
            userConsentSessionActive = userConsentSessions.current() != null,
            highSequence = summary?.highSequence ?: 0L,
        )
    }

    fun pair(context: Context): OtpPairingStatus {
        ensureInitialized(context)
        return OtpPairingClient(appContext, pairStore, settings).pair()
    }

    fun authorizeApprovedSms(
        context: Context,
        ttlMs: Long = OTP_CAPTURE_GRANT_MAX_TTL_MS,
    ): Boolean {
        ensureInitialized(context)
        if (!BuildConfig.OTP_SMS_CAPTURE_INCLUDED || !hasSmsPermission() || isDeviceLocked()) return false
        val pair = pairStore.summary() ?: return false
        val grant = grants.authorize(
            pair = pair,
            source = OtpCaptureSource.APPROVED_SMS_PERMISSION,
            platformGranted = true,
            automaticCapture = true,
            ttlMs = ttlMs,
        ) ?: return false
        return try {
            settings.captureOptIn = true
            handler.removeCallbacks(expiryRunnable)
            handler.postDelayed(expiryRunnable, grant.expiresAtMonotonicMs - SystemClock.elapsedRealtime())
            true
        } catch (_: Exception) {
            grants.revoke(); false
        }
    }

    fun beginUserConsentSession(
        context: Context,
        ttlMs: Long = OTP_USER_CONSENT_DEFAULT_TTL_MS,
    ): OtpUserConsentSession? {
        ensureInitialized(context)
        if (isDeviceLocked() || grants.current() != null) return null
        val pair = pairStore.summary() ?: return null
        val session = userConsentSessions.begin(pair, ttlMs) ?: return null
        return try {
            handler.removeCallbacks(userConsentExpiryRunnable)
            handler.postDelayed(
                userConsentExpiryRunnable,
                session.expiresAtMonotonicMs - SystemClock.elapsedRealtime(),
            )
            session
        } catch (_: Exception) {
            userConsentSessions.cancel(session.sessionId)
            null
        }
    }

    fun isUserConsentSessionCurrent(
        context: Context,
        sessionId: String,
        targetDeviceId: String,
    ): Boolean {
        ensureInitialized(context)
        if (isDeviceLocked()) {
            cancelUserConsentSessionInternal()
            return false
        }
        val current = userConsentSessions.current() ?: return false
        return current.sessionId == sessionId && current.targetDeviceId == targetDeviceId
    }

    fun cancelUserConsentSession(context: Context, sessionId: String? = null) {
        ensureInitialized(context)
        handler.removeCallbacks(userConsentExpiryRunnable)
        userConsentSessions.cancel(sessionId)
    }

    fun relayUserConsentedMessage(
        context: Context,
        sessionId: String,
        targetDeviceId: String,
        ownedMessageBody: CharArray,
    ): OtpRelaySendStatus {
        ensureInitialized(context)
        handler.removeCallbacks(userConsentExpiryRunnable)
        val session = userConsentSessions.consume(sessionId, targetDeviceId)
        try {
            if (session == null || isDeviceLocked()) return OtpRelaySendStatus.DISABLED
            val pair = pairStore.summary() ?: return OtpRelaySendStatus.DISABLED
            if (
                pair.sessionEpoch != session.pairSessionEpoch ||
                pair.senderDeviceId != session.senderDeviceId ||
                pair.targetDeviceId != session.targetDeviceId
            ) return OtpRelaySendStatus.DISABLED
            val remaining = session.expiresAtMonotonicMs - SystemClock.elapsedRealtime()
            if (remaining <= 0L) return OtpRelaySendStatus.DISABLED
            val grant = grants.authorize(
                pair = pair,
                source = OtpCaptureSource.SMS_USER_CONSENT,
                platformGranted = true,
                automaticCapture = false,
                ttlMs = remaining.coerceAtMost(30_000L),
            ) ?: return OtpRelaySendStatus.DISABLED
            val nowWallMs = System.currentTimeMillis()
            val parsed = OtpSmsParser.parse(
                ownedMessageBody,
                USER_CONSENT_SOURCE_LABEL,
                nowWallMs,
                nowWallMs,
            ) ?: return OtpRelaySendStatus.DROPPED
            parsed.use {
                val capture = OneShotCapturePort(
                    grant.source,
                    grant.grantId,
                    grant.targetDeviceId,
                    it.take(),
                )
                OtpJcaRelayProducer(
                    capturePort = capture,
                    pairMaterialPort = pairStore,
                    transport = SecureOtpHttpOnlineTransport(Settings(appContext)),
                ).use { producer ->
                    return producer.captureAndRelay(grant, explicitUserAction = true)
                }
            }
        } finally {
            ownedMessageBody.wipe()
            grants.revoke()
        }
    }

    fun relayApprovedSms(
        context: Context,
        ownedMessageBody: CharArray,
        senderAddress: String?,
        receivedAtWallMs: Long,
    ): OtpRelaySendStatus {
        ensureInitialized(context)
        try {
            if (
                !BuildConfig.OTP_SMS_CAPTURE_INCLUDED ||
                !settings.captureOptIn ||
                !hasSmsPermission() ||
                isDeviceLocked()
            ) {
                if (isDeviceLocked()) revokeCapture()
                return OtpRelaySendStatus.DISABLED
            }
            val grant = grants.current(OtpCaptureSource.APPROVED_SMS_PERMISSION)
                ?: return OtpRelaySendStatus.DISABLED
            val parsed = OtpSmsParser.parse(
                ownedMessageBody, senderAddress, receivedAtWallMs, System.currentTimeMillis(),
            ) ?: return OtpRelaySendStatus.DROPPED
            parsed.use {
                val characters = it.take()
                val capture = OneShotCapturePort(
                    grant.source, grant.grantId, grant.targetDeviceId, characters,
                )
                OtpJcaRelayProducer(
                    capturePort = capture,
                    pairMaterialPort = pairStore,
                    transport = SecureOtpHttpOnlineTransport(Settings(appContext)),
                ).use { producer ->
                    return producer.captureAndRelay(grant, explicitUserAction = false)
                }
            }
        } finally {
            ownedMessageBody.wipe()
        }
    }

    fun revokeCapture() {
        if (!initialized) return
        handler.removeCallbacks(expiryRunnable)
        handler.removeCallbacks(userConsentExpiryRunnable)
        grants.revoke()
        userConsentSessions.cancel()
        try { settings.captureOptIn = false } catch (_: Exception) { }
    }

    fun forgetPair(): Boolean {
        if (!initialized) return false
        revokeCapture()
        return pairStore.clear()
    }

    fun onSevereMemoryPressure() = revokeCapture()

    private fun ensureInitialized(context: Context) {
        if (!initialized) initialize(context)
    }

    private fun hasSmsPermission(): Boolean =
        ContextCompat.checkSelfPermission(appContext, Manifest.permission.RECEIVE_SMS) ==
            PackageManager.PERMISSION_GRANTED

    private fun isDeviceLocked(): Boolean =
        (appContext.getSystemService(Context.KEYGUARD_SERVICE) as? KeyguardManager)?.isDeviceLocked != false

    private fun registerSecurityReceiver() {
        val receiver = object : BroadcastReceiver() {
            override fun onReceive(context: Context, intent: Intent) {
                if (intent.action == Intent.ACTION_SCREEN_OFF || intent.action == Intent.ACTION_SHUTDOWN) {
                    revokeCapture()
                }
            }
        }
        val filter = IntentFilter().apply {
            addAction(Intent.ACTION_SCREEN_OFF)
            addAction(Intent.ACTION_SHUTDOWN)
        }
        if (Build.VERSION.SDK_INT >= 33) appContext.registerReceiver(receiver, filter, Context.RECEIVER_NOT_EXPORTED)
        else @Suppress("DEPRECATION") appContext.registerReceiver(receiver, filter)
    }

    private fun cancelUserConsentSessionInternal() {
        if (!initialized) return
        handler.removeCallbacks(userConsentExpiryRunnable)
        userConsentSessions.cancel()
    }

    private const val USER_CONSENT_SOURCE_LABEL = "user-consent"
}
