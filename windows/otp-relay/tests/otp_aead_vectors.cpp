#include "otp_aead_cng.h"

#include <algorithm>
#include <array>
#include <cstdint>
#include <iostream>
#include <span>
#include <string_view>
#include <vector>

namespace {

using clipvault::otp::crypto::EnvelopeFields;
using clipvault::otp::crypto::KeySchedule;
using clipvault::otp::crypto::NonceBytes;
using clipvault::otp::crypto::Sha256Bytes;
using clipvault::otp::crypto::TagBytes;
using clipvault::otp::crypto::UuidBytes;

int HexNibble(char value) {
  if (value >= '0' && value <= '9') {
    return value - '0';
  }
  if (value >= 'a' && value <= 'f') {
    return value - 'a' + 10;
  }
  return -1;
}

std::vector<std::uint8_t> Hex(std::string_view value) {
  std::vector<std::uint8_t> output;
  if (value.size() % 2 != 0) {
    return output;
  }
  output.reserve(value.size() / 2);
  for (std::size_t index = 0; index < value.size(); index += 2) {
    const int high = HexNibble(value[index]);
    const int low = HexNibble(value[index + 1]);
    if (high < 0 || low < 0) {
      return {};
    }
    output.push_back(static_cast<std::uint8_t>((high << 4) | low));
  }
  return output;
}

template <std::size_t Size>
std::array<std::uint8_t, Size> HexArray(std::string_view value) {
  const auto decoded = Hex(value);
  std::array<std::uint8_t, Size> output{};
  if (decoded.size() == output.size()) {
    std::copy(decoded.begin(), decoded.end(), output.begin());
  }
  return output;
}

template <typename Left, typename Right>
bool Equal(const Left& left, const Right& right) {
  const std::span left_bytes(left);
  const std::span right_bytes(right);
  return left_bytes.size() == right_bytes.size() &&
         std::equal(left_bytes.begin(), left_bytes.end(), right_bytes.begin());
}

bool Expect(bool condition, std::string_view name) {
  if (!condition) {
    std::cerr << "FAILED: " << name << '\n';
  }
  return condition;
}

bool RejectsTamperedAad(const Sha256Bytes& key, const NonceBytes& nonce,
                        const std::vector<std::uint8_t>& aad,
                        const std::vector<std::uint8_t>& ciphertext,
                        const TagBytes& tag, std::size_t offset) {
  auto tampered = aad;
  tampered.at(offset) ^= 0x01U;
  std::vector<std::uint8_t> plaintext;
  return !clipvault::otp::crypto::DecryptOtp(
      key, nonce, tampered, ciphertext, tag, &plaintext);
}

}  // namespace

int main() {
  bool ok = true;

  UuidBytes session_epoch{};
  UuidBytes event_id{};
  UuidBytes sender{};
  UuidBytes target{};
  ok &= Expect(clipvault::otp::crypto::ParseCanonicalUuidV4(
                   "11111111-1111-4111-8111-111111111111", &session_epoch),
               "canonical session epoch");
  ok &= Expect(clipvault::otp::crypto::ParseCanonicalUuidV4(
                   "22222222-2222-4222-8222-222222222222", &event_id),
               "canonical event ID");
  ok &= Expect(clipvault::otp::crypto::ParseCanonicalDeviceId(
                   "device:33333333-3333-4333-8333-333333333333", &sender),
               "canonical sender device ID");
  ok &= Expect(clipvault::otp::crypto::ParseCanonicalDeviceId(
                   "device:44444444-4444-4444-8444-444444444444", &target),
               "canonical target device ID");
  UuidBytes rejected{};
  ok &= Expect(!clipvault::otp::crypto::ParseCanonicalDeviceId(
                   "desktop:33333333-3333-4333-8333-333333333333", &rejected),
               "reject display/legacy prefix");
  ok &= Expect(!clipvault::otp::crypto::ParseCanonicalDeviceId(
                   "device:33333333-3333-4333-8333-33333333333A", &rejected),
               "reject noncanonical uppercase UUID");
  ok &= Expect(!clipvault::otp::crypto::ParseCanonicalDeviceId(
                   "device:33333333-3333-7333-8333-333333333333", &rejected),
               "reject non-v4 device UUID");

  Sha256Bytes pair_verifier{};
  ok &= Expect(clipvault::otp::crypto::ComputePairVerifier(
                   "clipvault-test-pair-token-v1", &pair_verifier),
               "CNG SHA-256 pair verifier");
  ok &= Expect(
      Equal(pair_verifier,
            HexArray<32>(
                "1984dac1230b907d0d407910707577a37f0fa1d2676e3dec3903221edffb4a7d")),
      "OTP-AEAD-V001 pair verifier");

  KeySchedule schedule;
  ok &= Expect(clipvault::otp::crypto::DeriveOtpKey(
                   pair_verifier, session_epoch, sender, target, &schedule),
               "CNG HKDF-SHA256 derivation");
  ok &= Expect(
      Equal(schedule.salt,
            HexArray<32>(
                "b8523666d202a197562792708f1c09e60b84c07892be20b48f1164b99825ca8b")),
      "OTP-AEAD-V001 salt");
  ok &= Expect(
      Equal(schedule.prk,
            HexArray<32>(
                "d895243bfb09fafe4a851f6f2824a660202009b92f5657bdd4a5e0189e15c8b1")),
      "OTP-AEAD-V001 PRK");
  ok &= Expect(
      Equal(schedule.info,
            Hex("436c69705661756c74204f54502052656c6179206b6579207631003333333333334333833333333333333344444444444444448444444444444444")),
      "OTP-AEAD-V001 info");
  ok &= Expect(
      Equal(schedule.key,
            HexArray<32>(
                "2a162d27a8d904a9f89858586c108f04d8cf93fb1e2af055bbc04549ac5faeae")),
      "OTP-AEAD-V001 key");

  const EnvelopeFields fields{
      .protocol_version = 1,
      .session_epoch = session_epoch,
      .event_id = event_id,
      .sender_device = sender,
      .target_device = target,
      .sequence = 42,
      .issued_at_unix_ms = 1785566400000ULL,
      .expires_at_unix_ms = 1785566520000ULL,
  };
  const auto aad = clipvault::otp::crypto::BuildAad(fields);
  const auto expected_aad = Hex(
      "436c69705661756c74204f54502052656c61792041454144207631000111111111111141118111111111111111222222222222422282222222222222223333333333334333833333333333333344444444444444448444444444444444000000000000002a0000019fbc0d0e000000019fbc0ee2c0");
  ok &= Expect(Equal(aad, expected_aad), "OTP-AEAD-V001 canonical AAD");

  Sha256Bytes aad_digest{};
  ok &= Expect(clipvault::otp::crypto::Sha256(aad, &aad_digest),
               "CNG SHA-256 AAD digest");
  ok &= Expect(
      Equal(aad_digest,
            HexArray<32>(
                "cd80a82e0ecb1498d3152d9de8eb11633e8e3d226b247f552be35631f6bb43e6")),
      "OTP-AEAD-V001 AAD digest");

  const NonceBytes nonce =
      HexArray<12>("000102030405060708090a0b");
  const auto plaintext = Hex("343832393137");
  std::vector<std::uint8_t> ciphertext;
  TagBytes tag{};
  ok &= Expect(clipvault::otp::crypto::EncryptOtp(
                   schedule.key, nonce, aad, plaintext, &ciphertext, &tag),
               "CNG AES-256-GCM encrypt");
  ok &= Expect(Equal(ciphertext, Hex("89a93a853549")),
               "OTP-AEAD-V001 ciphertext");
  ok &= Expect(Equal(tag, HexArray<16>("bd37d5d249eda03302fbe64b0014d882")),
               "OTP-AEAD-V001 authentication tag");

  std::vector<std::uint8_t> decrypted;
  ok &= Expect(clipvault::otp::crypto::DecryptOtp(
                   schedule.key, nonce, aad, ciphertext, tag, &decrypted) &&
                   Equal(decrypted, plaintext),
               "OTP-AEAD-V001 decrypt");

  constexpr std::size_t prefix_size = sizeof("ClipVault OTP Relay AEAD v1");
  const std::array<std::size_t, 8> authenticated_field_offsets{
      prefix_size,          // protocol version
      prefix_size + 1,      // session epoch
      prefix_size + 17,     // event ID
      prefix_size + 33,     // sender device
      prefix_size + 49,     // target device
      prefix_size + 65,     // sequence
      prefix_size + 73,     // issued_at
      prefix_size + 81,     // expires_at
  };
  for (const std::size_t offset : authenticated_field_offsets) {
    ok &= Expect(RejectsTamperedAad(schedule.key, nonce, aad, ciphertext, tag,
                                    offset),
                 "one-bit authenticated-field tamper rejection");
  }

  auto tampered_nonce = nonce;
  tampered_nonce[0] ^= 0x01U;
  ok &= Expect(!clipvault::otp::crypto::DecryptOtp(
                   schedule.key, tampered_nonce, aad, ciphertext, tag,
                   &decrypted),
               "one-bit nonce tamper rejection");
  auto tampered_ciphertext = ciphertext;
  tampered_ciphertext[0] ^= 0x01U;
  ok &= Expect(!clipvault::otp::crypto::DecryptOtp(
                   schedule.key, nonce, aad, tampered_ciphertext, tag,
                   &decrypted),
               "one-bit ciphertext tamper rejection");
  auto tampered_tag = tag;
  tampered_tag[0] ^= 0x01U;
  ok &= Expect(!clipvault::otp::crypto::DecryptOtp(
                   schedule.key, nonce, aad, ciphertext, tampered_tag,
                   &decrypted),
               "one-bit tag tamper rejection");
  auto wrong_pair_verifier = pair_verifier;
  wrong_pair_verifier[0] ^= 0x01U;
  KeySchedule wrong_schedule;
  ok &= Expect(clipvault::otp::crypto::DeriveOtpKey(
                   wrong_pair_verifier, session_epoch, sender, target,
                   &wrong_schedule) &&
                   !clipvault::otp::crypto::DecryptOtp(
                       wrong_schedule.key, nonce, aad, ciphertext, tag,
                       &decrypted),
               "wrong pair verifier rejection");

  clipvault::otp::crypto::NonceReuseGuard nonce_guard(2);
  ok &= Expect(nonce_guard.TryRemember(nonce), "remember first nonce");
  ok &= Expect(!nonce_guard.TryRemember(nonce), "reject reused nonce");
  auto second_nonce = nonce;
  second_nonce[0] = 0x7fU;
  ok &= Expect(nonce_guard.TryRemember(second_nonce), "remember second nonce");
  auto third_nonce = nonce;
  third_nonce[0] = 0x6eU;
  ok &= Expect(!nonce_guard.TryRemember(third_nonce),
               "nonce capacity exhaustion fails closed");

  const auto invalid_otp = Hex("31326134");
  ok &= Expect(!clipvault::otp::crypto::EncryptOtp(
                   schedule.key, nonce, aad, invalid_otp, &ciphertext, &tag),
               "reject non-digit plaintext");

  clipvault::otp::crypto::SecureErase(schedule.key);
  clipvault::otp::crypto::SecureErase(schedule.prk);
  clipvault::otp::crypto::SecureErase(schedule.salt);
  clipvault::otp::crypto::SecureErase(schedule.info);
  clipvault::otp::crypto::SecureErase(pair_verifier);
  clipvault::otp::crypto::SecureErase(decrypted);

  if (ok) {
    std::cout << "OTP-AEAD-V001 CNG vector passed\n";
  }
  return ok ? 0 : 1;
}
