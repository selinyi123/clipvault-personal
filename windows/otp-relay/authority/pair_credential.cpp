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

CredentialAcquireStatus ReadCredential(
    const std::wstring& target,
    std::array<std::uint8_t, kPairCredentialBytes>* blob) {
  PCREDENTIALW credential = nullptr;
  if (!CredReadW(target.c_str(), kCredentialType, 0, &credential)) {
    // A missing record is a durable invalidation. Other WinCred/provider
    // errors are transient and must not make the broker discard a live Slot.
    return GetLastError() == ERROR_NOT_FOUND
               ? CredentialAcquireStatus::kInvalid
               : CredentialAcquireStatus::kUnavailable;
  }
  bool valid = credential != nullptr &&
               credential->CredentialBlobSize == kPairCredentialBytes &&
               credential->CredentialBlob != nullptr;
  if (valid) {
    std::copy_n(credential->CredentialBlob, blob->size(), blob->begin());
  }
  if (credential != nullptr) {
    // The returned structure owns a plaintext CVPK buffer.  Wipe the bounded
    // provider allocation after copying it and before CredFree releases it.
    if (credential->CredentialBlob != nullptr &&
        credential->CredentialBlobSize <= kPairCredentialBytes) {
      SecureZeroMemory(credential->CredentialBlob,
                       credential->CredentialBlobSize);
    }
    CredFree(credential);
  }
  return valid ? CredentialAcquireStatus::kAcquired
               : CredentialAcquireStatus::kInvalid;
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

HANDLE AcquireNamedMutex(const std::wstring& name,
                         DWORD budget_milliseconds) noexcept {
  if (name.empty()) return nullptr;
  if (budget_milliseconds == 0) return nullptr;
  HANDLE handle = CreateMutexW(nullptr, FALSE, name.c_str());
  if (handle == nullptr) return nullptr;
  const DWORD wait =
      WaitForSingleObject(handle, budget_milliseconds);
  if (wait == WAIT_OBJECT_0 || wait == WAIT_ABANDONED) return handle;
  CloseHandle(handle);
  return nullptr;
}

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

PairCredentialLease::~PairCredentialLease() { Reset(); }

const PairCredential* PairCredentialLease::get() const noexcept {
  return loaded_ ? &credential_ : nullptr;
}

void PairCredentialLease::Reset() noexcept {
  credential_.Clear();
  loaded_ = false;
  if (mutex_ != nullptr) {
    ReleaseMutex(mutex_);
    CloseHandle(mutex_);
    mutex_ = nullptr;
  }
}

bool PairCredentialAuthority::Decode(const std::uint8_t* bytes,
                                     std::size_t size,
                                     PairCredential* credential) noexcept {
  if (credential == nullptr) return false;
  credential->Clear();
  if (bytes == nullptr || size != kPairCredentialBytes ||
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
      credential.session.sender_device == credential.session.target_device ||
      std::all_of(credential.session.pair_verifier.begin(),
                  credential.session.pair_verifier.end(),
                  [](std::uint8_t value) { return value == 0; })) {
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

std::wstring PairCredentialAuthority::MutexForSession(
    const crypto::UuidBytes& session_epoch) {
  const auto target = TargetForSession(session_epoch);
  if (target.empty()) return {};
  return L"Local\\ClipVaultOtpCredentialV1-" +
         target.substr(std::wcslen(kPairCredentialTargetPrefix));
}

CredentialAcquireStatus PairCredentialAuthority::AcquireDetailed(
    const crypto::UuidBytes& session_epoch, PairCredentialLease* lease,
  DWORD mutex_budget_milliseconds) noexcept {
  if (lease == nullptr) return CredentialAcquireStatus::kInvalid;
  // A caller may reuse one lease across attempts.  Every failed acquisition,
  // including a deliberately zero budget, must first release any prior CVPK
  // plaintext and recursive named-mutex ownership.
  lease->Reset();
  if (mutex_budget_milliseconds == 0) {
    return CredentialAcquireStatus::kUnavailable;
  }
  try {
    const auto target = TargetForSession(session_epoch);
    const auto mutex_name = MutexForSession(session_epoch);
    if (target.empty() || mutex_name.empty()) {
      return CredentialAcquireStatus::kInvalid;
    }
    HANDLE mutex = AcquireNamedMutex(mutex_name, mutex_budget_milliseconds);
    if (mutex == nullptr) return CredentialAcquireStatus::kUnavailable;
    // Transfer the acquired handle to RAII immediately so every later failure,
    // including an unexpected C++ exception, releases the recursive mutex.
    lease->mutex_ = mutex;

    std::array<std::uint8_t, kPairCredentialBytes> blob{};
    PairCredential decoded;
    const CredentialAcquireStatus read_status = ReadCredential(target, &blob);
    if (read_status != CredentialAcquireStatus::kAcquired) {
      crypto::SecureErase(blob);
      lease->Reset();
      return read_status;
    }
    const bool decoded_ok =
        Decode(blob.data(), blob.size(), &decoded) &&
        decoded.session.session_epoch == session_epoch;
    crypto::SecureErase(blob);
    if (!decoded_ok) {
      lease->Reset();
      return CredentialAcquireStatus::kInvalid;
    }
    lease->credential_ = std::move(decoded);
    lease->loaded_ = true;
    return CredentialAcquireStatus::kAcquired;
  } catch (...) {
    lease->Reset();
    return CredentialAcquireStatus::kUnavailable;
  }
}

bool PairCredentialAuthority::Acquire(
    const crypto::UuidBytes& session_epoch, PairCredentialLease* lease,
    DWORD mutex_budget_milliseconds) noexcept {
  return AcquireDetailed(session_epoch, lease, mutex_budget_milliseconds) ==
         CredentialAcquireStatus::kAcquired;
}

bool PairCredentialAuthority::Load(
    const crypto::UuidBytes& session_epoch,
    PairCredential* credential) noexcept {
  if (credential == nullptr) return false;
  credential->Clear();
  PairCredentialLease lease;
  if (!Acquire(session_epoch, &lease) || lease.get() == nullptr) return false;
  *credential = std::move(lease.credential_);
  lease.loaded_ = false;
  return true;
}

bool PairCredentialAuthority::Revoke(
    const crypto::UuidBytes& session_epoch,
    DWORD mutex_budget_milliseconds) noexcept {
  try {
    if (mutex_budget_milliseconds == 0) return false;
    const auto target = TargetForSession(session_epoch);
    const auto mutex_name = MutexForSession(session_epoch);
    if (target.empty() || mutex_name.empty()) return false;
    HANDLE mutex = AcquireNamedMutex(mutex_name, mutex_budget_milliseconds);
    if (mutex == nullptr) return false;

    const BOOL deleted = CredDeleteW(target.c_str(), kCredentialType, 0);
    const DWORD error = deleted ? ERROR_SUCCESS : GetLastError();
    ReleaseMutex(mutex);
    CloseHandle(mutex);
    return deleted != FALSE || error == ERROR_NOT_FOUND;
  } catch (...) {
    return false;
  }
}

bool PairCredentialAuthority::AdvanceHighSequence(
    const broker::PairSession& session, std::uint64_t sequence) noexcept {
  if (sequence == 0) return false;
  try {
    const auto target = TargetForSession(session.session_epoch);
    if (target.empty()) return false;
    PairCredentialLease lease;
    if (!Acquire(session.session_epoch, &lease) || lease.get() == nullptr) {
      return false;
    }
    std::array<std::uint8_t, kPairCredentialBytes> blob{};
    PairCredential current;
    current.session = lease.get()->session;
    current.high_sequence = lease.get()->high_sequence;
    bool success = current.session.session_epoch == session.session_epoch &&
                   current.session.sender_device == session.sender_device &&
                   current.session.target_device == session.target_device &&
                   current.session.pair_verifier == session.pair_verifier &&
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
      const auto read_status = ReadCredential(target, &verified_blob);
      success = read_status == CredentialAcquireStatus::kAcquired &&
                Decode(verified_blob.data(), verified_blob.size(), &verified) &&
                verified.session.session_epoch == session.session_epoch &&
                verified.session.sender_device == session.sender_device &&
                verified.session.target_device == session.target_device &&
                verified.session.pair_verifier == session.pair_verifier &&
                verified.high_sequence == sequence;
      crypto::SecureErase(verified_blob);
    }
    crypto::SecureErase(blob);
    return success;
  } catch (...) {
    return false;
  }
}

}  // namespace clipvault::otp::authority
