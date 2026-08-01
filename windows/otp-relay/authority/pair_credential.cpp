#include "pair_credential.h"

#include <wincred.h>

#include <algorithm>
#include <array>
#include <cwchar>
#include <utility>

namespace clipvault::otp::authority {
namespace {

constexpr std::array<std::uint8_t, 8> kHeader{'C', 'V', 'P', 'K', 1, 0, 0, 0};
constexpr DWORD kCredentialType = CRED_TYPE_GENERIC;
constexpr DWORD kCredentialPersist = CRED_PERSIST_LOCAL_MACHINE;
constexpr DWORD kMutexBudgetMilliseconds = 100;

bool IsCanonicalUuidV4(const crypto::UuidBytes& value) noexcept {
  return (value[6] & 0xf0U) == 0x40U && (value[8] & 0xc0U) == 0x80U;
}

std::uint64_t ReadU64(const std::uint8_t* bytes) noexcept {
  std::uint64_t value = 0;
  for (int index = 0; index < 8; ++index) value = (value << 8) | bytes[index];
  return value;
}

void WriteU64(std::uint8_t* bytes, std::uint64_t value) noexcept {
  for (int index = 7; index >= 0; --index) {
    bytes[index] = static_cast<std::uint8_t>(value);
    value >>= 8;
  }
}

void AppendHexByte(std::wstring* output, std::uint8_t byte) {
  constexpr wchar_t kHex[] = L"0123456789abcdef";
  output->push_back(kHex[byte >> 4]);
  output->push_back(kHex[byte & 0x0fU]);
}

bool ReadCredential(const std::wstring& target,
                    std::array<std::uint8_t, kPairCredentialBytes>* blob) {
  PCREDENTIALW credential = nullptr;
  if (!CredReadW(target.c_str(), kCredentialType, 0, &credential)) return false;
  bool valid = credential != nullptr &&
               credential->CredentialBlobSize == kPairCredentialBytes &&
               credential->CredentialBlob != nullptr;
  if (valid) {
    std::copy_n(credential->CredentialBlob, blob->size(), blob->begin());
  }
  if (credential != nullptr) CredFree(credential);
  return valid;
}

bool WriteCredential(const std::wstring& target,
                     std::array<std::uint8_t, kPairCredentialBytes>* blob) {
  CREDENTIALW credential{};
  credential.Type = kCredentialType;
  credential.TargetName = const_cast<wchar_t*>(target.c_str());
  credential.CredentialBlobSize = static_cast<DWORD>(blob->size());
  credential.CredentialBlob = blob->data();
  credential.Persist = kCredentialPersist;
  wchar_t user_name[] = L"ClipVault OTP Pair v1";
  credential.UserName = user_name;
  return CredWriteW(&credential, 0) != FALSE;
}

class ScopedMutex final {
 public:
  explicit ScopedMutex(const std::wstring& name) {
    handle_ = CreateMutexW(nullptr, FALSE, name.c_str());
    if (handle_ != nullptr) {
      const DWORD wait = WaitForSingleObject(handle_, kMutexBudgetMilliseconds);
      owned_ = wait == WAIT_OBJECT_0 || wait == WAIT_ABANDONED;
    }
  }
  ~ScopedMutex() {
    if (owned_) ReleaseMutex(handle_);
    if (handle_ != nullptr) CloseHandle(handle_);
  }
  [[nodiscard]] bool owned() const noexcept { return owned_; }

 private:
  HANDLE handle_ = nullptr;
  bool owned_ = false;
};

}  // namespace

PairCredential::PairCredential(PairCredential&& other) noexcept
    : session(other.session), high_sequence(other.high_sequence) {
  other.Clear();
}

PairCredential& PairCredential::operator=(PairCredential&& other) noexcept {
  if (this != &other) {
    Clear();
    session = other.session;
    high_sequence = other.high_sequence;
    other.Clear();
  }
  return *this;
}

PairCredential::~PairCredential() { Clear(); }

void PairCredential::Clear() noexcept {
  crypto::SecureErase(session.pair_verifier);
  crypto::SecureErase(session.session_epoch);
  crypto::SecureErase(session.sender_device);
  crypto::SecureErase(session.target_device);
  high_sequence = 0;
}

bool PairCredentialAuthority::Decode(const std::uint8_t* bytes,
                                     std::size_t size,
                                     PairCredential* credential) noexcept {
  if (bytes == nullptr || credential == nullptr || size != kPairCredentialBytes ||
      !std::equal(kHeader.begin(), kHeader.end(), bytes)) {
    return false;
  }
  PairCredential decoded;
  std::copy_n(bytes + 8, 16, decoded.session.session_epoch.begin());
  std::copy_n(bytes + 24, 16, decoded.session.sender_device.begin());
  std::copy_n(bytes + 40, 16, decoded.session.target_device.begin());
  std::copy_n(bytes + 56, 32, decoded.session.pair_verifier.begin());
  decoded.high_sequence = ReadU64(bytes + 88);
  const bool valid = IsCanonicalUuidV4(decoded.session.session_epoch) &&
                     IsCanonicalUuidV4(decoded.session.sender_device) &&
                     IsCanonicalUuidV4(decoded.session.target_device) &&
                     decoded.session.sender_device != decoded.session.target_device &&
                     std::any_of(decoded.session.pair_verifier.begin(),
                                 decoded.session.pair_verifier.end(),
                                 [](std::uint8_t value) { return value != 0; });
  if (!valid) return false;
  *credential = std::move(decoded);
  return true;
}

bool PairCredentialAuthority::Encode(const PairCredential& credential,
                                     std::uint8_t* bytes,
                                     std::size_t size) noexcept {
  if (bytes == nullptr || size != kPairCredentialBytes ||
      !IsCanonicalUuidV4(credential.session.session_epoch) ||
      !IsCanonicalUuidV4(credential.session.sender_device) ||
      !IsCanonicalUuidV4(credential.session.target_device) ||
      credential.session.sender_device == credential.session.target_device) {
    return false;
  }
  std::fill_n(bytes, size, static_cast<std::uint8_t>(0));
  std::copy(kHeader.begin(), kHeader.end(), bytes);
  std::copy(credential.session.session_epoch.begin(),
            credential.session.session_epoch.end(), bytes + 8);
  std::copy(credential.session.sender_device.begin(),
            credential.session.sender_device.end(), bytes + 24);
  std::copy(credential.session.target_device.begin(),
            credential.session.target_device.end(), bytes + 40);
  std::copy(credential.session.pair_verifier.begin(),
            credential.session.pair_verifier.end(), bytes + 56);
  WriteU64(bytes + 88, credential.high_sequence);
  return true;
}

std::wstring PairCredentialAuthority::TargetForSession(
    const crypto::UuidBytes& value) {
  if (!IsCanonicalUuidV4(value)) return {};
  std::wstring result(kPairCredentialTargetPrefix);
  for (std::size_t index = 0; index < value.size(); ++index) {
    if (index == 4 || index == 6 || index == 8 || index == 10)
      result.push_back(L'-');
    AppendHexByte(&result, value[index]);
  }
  return result;
}

bool PairCredentialAuthority::Load(
    const crypto::UuidBytes& session_epoch,
    PairCredential* credential) noexcept {
  if (credential == nullptr) return false;
  const auto target = TargetForSession(session_epoch);
  if (target.empty()) return false;
  std::array<std::uint8_t, kPairCredentialBytes> blob{};
  PairCredential decoded;
  const bool read = ReadCredential(target, &blob);
  const bool valid = read && Decode(blob.data(), blob.size(), &decoded) &&
                     decoded.session.session_epoch == session_epoch;
  crypto::SecureErase(blob);
  if (!valid) return false;
  *credential = std::move(decoded);
  return true;
}

bool PairCredentialAuthority::AdvanceHighSequence(
    const broker::PairSession& session, std::uint64_t sequence) noexcept {
  if (sequence == 0) return false;
  const auto target = TargetForSession(session.session_epoch);
  if (target.empty()) return false;
  const std::wstring mutex_name =
      L"Local\\ClipVaultOtpCredentialV1-" +
      target.substr(std::wcslen(kPairCredentialTargetPrefix));
  ScopedMutex mutex(mutex_name);
  if (!mutex.owned()) return false;

  std::array<std::uint8_t, kPairCredentialBytes> blob{};
  PairCredential current;
  bool success = ReadCredential(target, &blob) &&
                 Decode(blob.data(), blob.size(), &current) &&
                 current.session.session_epoch == session.session_epoch &&
                 current.session.sender_device == session.sender_device &&
                 current.session.target_device == session.target_device &&
                 current.high_sequence < sequence;
  if (success) {
    current.high_sequence = sequence;
    success = Encode(current, blob.data(), blob.size()) &&
              WriteCredential(target, &blob);
  }
  // Read-after-write makes acknowledgement fail closed if Credential Manager
  // did not durably expose the new high-water record.
  if (success) {
    std::array<std::uint8_t, kPairCredentialBytes> verified_blob{};
    PairCredential verified;
    success = ReadCredential(target, &verified_blob) &&
              Decode(verified_blob.data(), verified_blob.size(), &verified) &&
              verified.session.session_epoch == session.session_epoch &&
              verified.session.sender_device == session.sender_device &&
              verified.session.target_device == session.target_device &&
              verified.high_sequence == sequence;
    crypto::SecureErase(verified_blob);
  }
  crypto::SecureErase(blob);
  return success;
}

}  // namespace clipvault::otp::authority
