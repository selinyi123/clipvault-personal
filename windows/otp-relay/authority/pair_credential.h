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
};

}  // namespace clipvault::otp::authority
