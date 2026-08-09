#pragma once

#include "otp_broker_core.h"

#include <windows.h>

#include <cstdint>
#include <string>

namespace clipvault::otp::authority {

inline constexpr std::size_t kPairCredentialBytes = 96;
inline constexpr wchar_t kPairCredentialTargetPrefix[] =
    L"ClipVault/OTP/Pair/v1/";

struct PairCredential final {
  broker::PairSession session;
  std::uint64_t high_sequence = 0;

  PairCredential() = default;
  PairCredential(const PairCredential&) = delete;
  PairCredential& operator=(const PairCredential&) = delete;
  PairCredential(PairCredential&& other) noexcept;
  PairCredential& operator=(PairCredential&& other) noexcept;
  ~PairCredential();
  void Clear() noexcept;
};

// Credential Manager and the per-session mutation mutex can fail for
// fundamentally different reasons. Broker callers must not treat a transient
// provider/mutex failure as evidence that a still-valid pair was revoked.
enum class CredentialAcquireStatus {
  kAcquired,
  kUnavailable,
  kInvalid,
};

// Holds the per-session cross-process mutation mutex while exposing one
// decoded credential.  Keeping this lease alive across Broker Arm/Consume
// closes the credential-delete TOCTOU window: revoke cannot complete until a
// plaintext handoff that already linearized under the same mutex finishes.
class PairCredentialLease final {
 public:
  PairCredentialLease() = default;
  PairCredentialLease(const PairCredentialLease&) = delete;
  PairCredentialLease& operator=(const PairCredentialLease&) = delete;
  ~PairCredentialLease();

  [[nodiscard]] const PairCredential* get() const noexcept;
  void Reset() noexcept;

 private:
  friend class PairCredentialAuthority;
  HANDLE mutex_ = nullptr;
  PairCredential credential_;
  bool loaded_ = false;
};

// Current-user Generic Credential authority for the frozen 96-byte CVPK v1
// record. This class is linked only into ClipVaultOtpBroker.exe and its native
// tests. Neither the TSF DLL nor the IME Host can read pair verifiers.
class PairCredentialAuthority final
    : public broker::PersistentSequenceAuthority {
 public:
  PairCredentialAuthority() = default;
  PairCredentialAuthority(const PairCredentialAuthority&) = delete;
  PairCredentialAuthority& operator=(const PairCredentialAuthority&) = delete;

  bool Load(const crypto::UuidBytes& session_epoch,
            PairCredential* credential) noexcept;
  CredentialAcquireStatus AcquireDetailed(
      const crypto::UuidBytes& session_epoch, PairCredentialLease* lease,
      DWORD mutex_budget_milliseconds = 100) noexcept;
  bool Acquire(const crypto::UuidBytes& session_epoch,
               PairCredentialLease* lease,
               DWORD mutex_budget_milliseconds = 100) noexcept;
  // Atomically remove the durable CVPK under the same per-session mutex used
  // by Acquire/AdvanceHighSequence.  Missing credentials are already
  // revoked and therefore count as success.
  bool Revoke(const crypto::UuidBytes& session_epoch,
              DWORD mutex_budget_milliseconds = 100) noexcept;
  bool AdvanceHighSequence(const broker::PairSession& session,
                           std::uint64_t sequence) noexcept override;

  // Test-only helpers use a caller-provided target below the same strict CVPK
  // parser. Production code must use Load/AdvanceHighSequence.
  static bool Decode(const std::uint8_t* bytes, std::size_t size,
                     PairCredential* credential) noexcept;
  static bool Encode(const PairCredential& credential,
                     std::uint8_t* bytes, std::size_t size) noexcept;
  static std::wstring TargetForSession(
      const crypto::UuidBytes& session_epoch);
  static std::wstring MutexForSession(
      const crypto::UuidBytes& session_epoch);
};

}  // namespace clipvault::otp::authority
