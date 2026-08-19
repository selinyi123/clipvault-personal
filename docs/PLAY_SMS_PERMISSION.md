# Google Play SMS permission gate for OTP Relay

> Status: Owner/platform-review gate; not approved and not enabled in the production manifest
> (2026-08-01).

ClipVault OTP Relay needs plaintext only when the user explicitly enables automatic cross-device relay.
This capability belongs to the separately installed Companion/Runtime APK. The networkless IME APK must
never request `READ_SMS`, `RECEIVE_SMS`, notification-listener access or any network permission.

## Minimum requested capability

- Prefer system Inline Autofill when the goal is filling on the phone; the IME hosts the protected view
  and cannot read or relay its plaintext.
- For newly arriving generic third-party SMS OTPs, request only `RECEIVE_SMS` if Google Play approves the
  connected-device/cross-device-transfer use. Do not request historical `READ_SMS` merely for convenience.
- Use SMS User Consent for a per-message confirmed fallback. SMS Retriever is useful only when the sender
  format/hash is controlled by the participating application; it is not a generic bank/social OTP reader.
- A trusted `CompanionDeviceManager` association and a notification listener are separate grants. Neither
  silently grants SMS access, and OEM/RCS notification behavior is not guaranteed.

## Required product and review evidence

The Owner may submit a restricted-permission declaration only after all rows below have inspectable evidence:

1. Store listing and onboarding present phone-to-computer OTP/SMS transfer as a visible core feature.
2. The user pairs one specific computer, opts in to OTP capture separately and can revoke it immediately.
3. A review video shows grant, one live relay, Windows presentation/direct TSF insertion, expiry, revocation
   and the absence of clipboard/history/database entries.
4. Privacy policy and Play Data safety disclosure cover SMS processing, the selected target device,
   online-only E2EE transfer, TTL and no application-level persistence.
5. Review instructions include a usable test path. Test credentials must be dedicated review credentials,
   never a production user's secret.
6. Android 13, 15 and 17 device evidence covers permission denial, notification redaction, lock/restart,
   SMS/RCS variants and the connected-device exemption. Failure falls back safely; it never forwards a
   three-hour-old OTP as if it were live.
7. The permission is removed from every release flavor until approval exists. Sideload or internal testing
   does not count as Google Play approval.

## Build-flavor rule

```text
ime-app
  no INTERNET / SMS / notification listener in every flavor

runtime default
  no restricted SMS permission; Inline Autofill and explicit fallback only

runtime otpSmsRelay
  RECEIVE_SMS only after Owner enables the approved release lane
```

The capture component receives the full SMS only long enough to parse a bounded OTP candidate. It forwards
only the normalized code plus minimal authenticated metadata into the in-memory OTP pipeline; it never sends
the complete message body or sender address to the IME, relay, clipboard, Room, SQLite, ordinary outbox,
analytics or crash reports.

## Authoritative references

- [Google Play SMS/Call Log permitted uses and exceptions](https://support.google.com/googleplay/android-developer/answer/10208820)
- [Google Play permission declaration process](https://support.google.com/googleplay/android-developer/answer/9214102)
- [Android 15 OTP notification redaction](https://developer.android.com/about/versions/15/behavior-changes-all#otp-redaction)
- [Android 17 OTP protection](https://developer.android.com/about/versions/17/behavior-changes-17#otp-protection)

Approval is controlled by Google Play and the Owner account. Passing repository tests cannot turn this gate
green or justify shipping the restricted permission.
