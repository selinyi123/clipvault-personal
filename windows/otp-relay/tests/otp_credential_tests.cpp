#include "pair_credential.h"

#include <bcrypt.h>
#include <wincred.h>

#include <algorithm>
#include <array>
#include <cstdint>
#include <iostream>

namespace {
using namespace clipvault::otp;

bool Expect(bool condition, const char* message) {
  if (!condition) std::cerr << "credential test failed: " << message << '\n';
  return condition;
}

crypto::UuidBytes Uuid(std::uint8_t seed) {
  crypto::UuidBytes value{};
  for (std::size_t index = 0; index < value.size(); ++index)
    value[index] = static_cast<std::uint8_t>(seed + index);
  value[6] = static_cast<std::uint8_t>((value[6] & 0x0fU) | 0x40U);
  value[8] = static_cast<std::uint8_t>((value[8] & 0x3fU) | 0x80U);
  return value;
}

bool WriteTestCredential(
    const std::wstring& target,
    std::array<std::uint8_t, authority::kPairCredentialBytes>* blob) {
  CREDENTIALW credential{};
  credential.Type = CRED_TYPE_GENERIC;
  credential.TargetName = const_cast<wchar_t*>(target.c_str());
  credential.CredentialBlobSize = static_cast<DWORD>(blob->size());
  credential.CredentialBlob = blob->data();
  credential.Persist = CRED_PERSIST_SESSION;
  wchar_t username[] = L"ClipVault OTP native test";
  credential.UserName = username;
  return CredWriteW(&credential, 0) != FALSE;
}

bool ReadTestCredential(
    const std::wstring& target,
    std::array<std::uint8_t, authority::kPairCredentialBytes>* blob) {
  PCREDENTIALW credential = nullptr;
  if (!CredReadW(target.c_str(), CRED_TYPE_GENERIC, 0, &credential)) return false;
  const bool valid = credential != nullptr &&
                     credential->CredentialBlobSize == blob->size();
  if (valid)
    std::copy_n(credential->CredentialBlob, blob->size(), blob->begin());
  if (credential != nullptr) CredFree(credential);
  return valid;
}
}  // namespace

int main() {
  using namespace clipvault::otp;
  bool ok = true;
  authority::PairCredential record;
  record.session.session_epoch = Uuid(0x11);
  record.session.sender_device = Uuid(0x33);
  record.session.target_device = Uuid(0x55);
  for (std::size_t index = 0; index < record.session.pair_verifier.size(); ++index)
    record.session.pair_verifier[index] = static_cast<std::uint8_t>(0xa0 + index);
  record.high_sequence = 42;
  std::array<std::uint8_t, authority::kPairCredentialBytes> blob{};
  ok &= Expect(authority::PairCredentialAuthority::Encode(
                   record, blob.data(), blob.size()),
               "encode fixed CVPK");
  ok &= Expect(std::equal(blob.begin(), blob.begin() + 8,
                          std::array<std::uint8_t, 8>{'C', 'V', 'P', 'K', 1, 0,
                                                      0, 0}.begin()),
               "magic/version/reserved");
  ok &= Expect(blob[88] == 0 && blob[95] == 42,
               "high sequence big endian at offset 88");
  authority::PairCredential decoded;
  ok &= Expect(authority::PairCredentialAuthority::Decode(
                   blob.data(), blob.size(), &decoded) &&
                   decoded.session.session_epoch == record.session.session_epoch &&
                   decoded.session.sender_device == record.session.sender_device &&
                   decoded.session.target_device == record.session.target_device &&
                   decoded.high_sequence == 42,
               "strict round trip");
  auto malformed = blob;
  malformed[5] = 1;
  authority::PairCredential rejected;
  ok &= Expect(!authority::PairCredentialAuthority::Decode(
                   malformed.data(), malformed.size(), &rejected),
               "reserved byte rejected");
  ok &= Expect(!authority::PairCredentialAuthority::Decode(
                   blob.data(), blob.size() - 1, &rejected),
               "non-96-byte record rejected");

  // Real current-user WinCred round trip. The unique CVPK target is deleted in
  // every path and never contains a production verifier.
  BCryptGenRandom(nullptr, record.session.session_epoch.data(),
                  static_cast<ULONG>(record.session.session_epoch.size()),
                  BCRYPT_USE_SYSTEM_PREFERRED_RNG);
  record.session.session_epoch[6] = static_cast<std::uint8_t>(
      (record.session.session_epoch[6] & 0x0fU) | 0x40U);
  record.session.session_epoch[8] = static_cast<std::uint8_t>(
      (record.session.session_epoch[8] & 0x3fU) | 0x80U);
  record.high_sequence = 0;
  authority::PairCredentialAuthority::Encode(record, blob.data(), blob.size());
  const auto target = authority::PairCredentialAuthority::TargetForSession(
      record.session.session_epoch);
  CredDeleteW(target.c_str(), CRED_TYPE_GENERIC, 0);
  const bool wrote = WriteTestCredential(target, &blob);
  ok &= Expect(wrote, "write current-user test CVPK");
  if (wrote) {
    authority::PairCredentialAuthority authority;
    authority::PairCredential loaded;
    ok &= Expect(authority.Load(record.session.session_epoch, &loaded) &&
                     loaded.high_sequence == 0,
                 "load exact target");
    ok &= Expect(authority.AdvanceHighSequence(record.session, 7),
                 "persist high sequence");
    ok &= Expect(!authority.AdvanceHighSequence(record.session, 7),
                 "same sequence fails closed");
    std::array<std::uint8_t, authority::kPairCredentialBytes> persisted{};
    authority::PairCredential persisted_record;
    ok &= Expect(ReadTestCredential(target, &persisted) &&
                     authority::PairCredentialAuthority::Decode(
                         persisted.data(), persisted.size(), &persisted_record) &&
                     persisted_record.high_sequence == 7,
                 "read-after-write high sequence");
    crypto::SecureErase(persisted);
  }
  CredDeleteW(target.c_str(), CRED_TYPE_GENERIC, 0);
  crypto::SecureErase(blob);
  return ok ? 0 : 1;
}
