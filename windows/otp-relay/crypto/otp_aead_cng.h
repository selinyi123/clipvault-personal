#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <span>
#include <string_view>
#include <vector>

namespace clipvault::otp::crypto {

using UuidBytes = std::array<std::uint8_t, 16>;
using Sha256Bytes = std::array<std::uint8_t, 32>;
using NonceBytes = std::array<std::uint8_t, 12>;
using TagBytes = std::array<std::uint8_t, 16>;

struct EnvelopeFields final {
  std::uint8_t protocol_version = 1;
  UuidBytes session_epoch{};
  UuidBytes event_id{};
  UuidBytes sender_device{};
  UuidBytes target_device{};
  std::uint64_t sequence = 0;
  std::uint64_t issued_at_unix_ms = 0;
  std::uint64_t expires_at_unix_ms = 0;
};

struct KeySchedule final {
  Sha256Bytes salt{};
  Sha256Bytes prk{};
  std::vector<std::uint8_t> info;
  Sha256Bytes key{};
};

// OTP-3A identifiers are lowercase canonical RFC-4122 UUIDv4 values. Device
// identities additionally carry the exact "device:" prefix. Display names,
// ULIDs, and legacy sync identifiers are intentionally rejected.
bool ParseCanonicalUuidV4(std::string_view value, UuidBytes* output);
bool ParseCanonicalDeviceId(std::string_view value, UuidBytes* output);

bool Sha256(std::span<const std::uint8_t> input, Sha256Bytes* output);
bool ComputePairVerifier(std::string_view pair_secret_utf8,
                         Sha256Bytes* output);
bool DeriveOtpKey(const Sha256Bytes& pair_verifier,
                  const UuidBytes& session_epoch,
                  const UuidBytes& sender_device,
                  const UuidBytes& target_device,
                  KeySchedule* output);

std::vector<std::uint8_t> BuildAad(const EnvelopeFields& fields);

bool EncryptOtp(const Sha256Bytes& key, const NonceBytes& nonce,
                std::span<const std::uint8_t> aad,
                std::span<const std::uint8_t> plaintext,
                std::vector<std::uint8_t>* ciphertext, TagBytes* tag);
bool DecryptOtp(const Sha256Bytes& key, const NonceBytes& nonce,
                std::span<const std::uint8_t> aad,
                std::span<const std::uint8_t> ciphertext,
                const TagBytes& tag, std::vector<std::uint8_t>* plaintext);

void SecureErase(std::span<std::uint8_t> bytes) noexcept;

// One instance belongs to exactly one authenticated pair session. It is a
// bounded, memory-only pre-encryption nonce guard; exhaustion fails closed.
class NonceReuseGuard final {
 public:
  explicit NonceReuseGuard(std::size_t capacity);
  NonceReuseGuard(const NonceReuseGuard&) = delete;
  NonceReuseGuard& operator=(const NonceReuseGuard&) = delete;
  ~NonceReuseGuard();

  bool TryRemember(const NonceBytes& nonce);
  void Clear() noexcept;

 private:
  std::size_t capacity_ = 0;
  std::vector<NonceBytes> nonces_;
};

}  // namespace clipvault::otp::crypto
