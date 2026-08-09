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
    val pairState: OtpPairState,
    val repairRequired: Boolean,
    val activeGrant: Boolean,
    val userConsentSessionActive: Boolean,
    val highSequence: Long,
) {
    override fun toString(): String =
        "<OtpRuntimeStatus redacted pairState=$pairState active=$activeGrant consent=$userConsentSessionActive>"
}

private class OneShotCapturePort(
    override val source: OtpCaptureSource,
    private val grantId: String,
    private val targetDeviceId: String,
    ownedCharacters: CharArray,
    private val authorizationCheck: (OtpCaptureAuthorization) -> Boolean,
) : OtpCapturePort {
    private var characters: CharArray? = ownedCharacters

    @Synchronized
    override fun capture(authorization: OtpCaptureAuthorization): IsolatedOtpCandidate? {
        if (!authorizationCheck(authorization)) {
            characters?.wipe(); characters = null
            return null
        }
        val value = characters ?: return null
        characters = null
        return IsolatedOtpCandidate(source, grantId, targetDeviceId, value)
    }

    override fun isAuthorizationCurrent(authorization: OtpCaptureAuthorization): Boolean =
        authorizationCheck(authorization)

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
    @Volatile private var processRepairRequired = false
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
        processRepairRequired = false
        // A prior-process opt-in never fabricates a fresh in-memory grant.
        try { settings.captureOptIn = false } catch (_: Exception) { }
        registerSecurityReceiver()
        initialized = true
    }

    fun status(context: Context): OtpRuntimeStatus {
        ensureInitialized(context)
        val inspection = effectivePairInspection()
        val summary = inspection.summary
        return OtpRuntimeStatus(
            smsCaptureIncluded = BuildConfig.OTP_SMS_CAPTURE_INCLUDED,
            smsPermissionGranted = hasSmsPermission(),
            paired = inspection.state != OtpPairState.UNPAIRED,
            pairState = inspection.state,
            repairRequired = inspection.repairRequired,
            activeGrant = inspection.state == OtpPairState.READY &&
                settings.captureOptIn && grants.current() != null,
            userConsentSessionActive = userConsentSessions.current() != null,
            highSequence = summary?.highSequence ?: 0L,
        )
    }

    fun pair(context: Context): OtpPairingStatus {
        ensureInitialized(context)
        if (effectivePairInspection().state == OtpPairState.ROTATION_REQUIRED) {
            return OtpPairingStatus.REPAIR_REQUIRED
        }
        return OtpPairingClient(appContext, pairStore, settings).pair()
    }

    fun authorizeApprovedSms(
        context: Context,
        ttlMs: Long = OTP_CAPTURE_GRANT_MAX_TTL_MS,
    ): Boolean {
        ensureInitialized(context)
        if (!BuildConfig.OTP_SMS_CAPTURE_INCLUDED || !hasSmsPermission() || isDeviceLocked()) return false
        val inspection = effectivePairInspection()
        if (inspection.state != OtpPairState.READY) return false
        val pair = inspection.summary ?: return false
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
        val inspection = effectivePairInspection()
        if (inspection.state != OtpPairState.READY) return null
        val pair = inspection.summary ?: return null
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
            val inspection = effectivePairInspection()
            val pair = when (inspection.state) {
                OtpPairState.READY -> inspection.summary ?: return OtpRelaySendStatus.TRANSIENT_FAILURE
                OtpPairState.UNPAIRED -> return OtpRelaySendStatus.UNPAIRED
                OtpPairState.ROTATION_REQUIRED -> return OtpRelaySendStatus.ROTATION_REQUIRED
                OtpPairState.UNAVAILABLE -> return OtpRelaySendStatus.TRANSIENT_FAILURE
            }
            if (
                pair.sessionEpoch != session.pairSessionEpoch ||
                pair.senderDeviceId != session.senderDeviceId ||
                pair.targetDeviceId != session.targetDeviceId
            ) return OtpRelaySendStatus.MISMATCH
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
                    grants::isCurrent,
                )
                OtpJcaRelayProducer(
                    capturePort = capture,
                    pairMaterialPort = pairStore,
                    transport = SecureOtpHttpOnlineTransport(Settings(appContext)),
                ).use { producer ->
                    val result = producer.captureAndRelay(
                        grant,
                        explicitUserAction = true,
                    )
                    observeRelayResult(grant.sessionEpoch, result)
                    return result
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
                    grants::isCurrent,
                )
                OtpJcaRelayProducer(
                    capturePort = capture,
                    pairMaterialPort = pairStore,
                    transport = SecureOtpHttpOnlineTransport(Settings(appContext)),
                ).use { producer ->
                    val result = producer.captureAndRelay(grant, explicitUserAction = false)
                    observeRelayResult(grant.sessionEpoch, result)
                    return result
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
        if (!pairStore.clear()) return false
        return try {
            settings.pairRepairRequired = false
            processRepairRequired = false
            true
        } catch (_: Exception) {
            false
        }
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

    private fun effectivePairInspection(): OtpPairInspection {
        val inspection = pairStore.inspect()
        return if (processRepairRequired || settings.pairRepairRequired) {
            OtpPairInspection(OtpPairState.ROTATION_REQUIRED, inspection.summary)
        } else {
            inspection
        }
    }

    private fun observeRelayResult(
        expectedSessionEpoch: String,
        result: OtpRelaySendStatus,
    ) {
        if (
            result != OtpRelaySendStatus.ROTATION_REQUIRED &&
            result != OtpRelaySendStatus.UNPAIRED &&
            result != OtpRelaySendStatus.MISMATCH &&
            result != OtpRelaySendStatus.AUTH_REQUIRED &&
            result != OtpRelaySendStatus.AUTH_REJECTED &&
            result != OtpRelaySendStatus.POLICY_REJECTED
        ) return

        if (
            result == OtpRelaySendStatus.ROTATION_REQUIRED ||
            result == OtpRelaySendStatus.UNPAIRED ||
            result == OtpRelaySendStatus.MISMATCH
        ) {
            val current = pairStore.inspect()
            val currentSession = current.summary?.sessionEpoch
            if (
                current.state == OtpPairState.UNAVAILABLE ||
                currentSession == expectedSessionEpoch
            ) {
                processRepairRequired = true
                try { settings.pairRepairRequired = true } catch (_: Exception) { }
            }
        }
        // Even if the pair changed concurrently, the old capture/session must
        // never remain armed after a terminal server response.
        revokeCapture()
    }

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
