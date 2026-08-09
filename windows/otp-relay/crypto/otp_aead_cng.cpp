#include "otp_aead_cng.h"

#include <windows.h>
#include <bcrypt.h>

#include <algorithm>
#include <limits>
#include <utility>

namespace clipvault::otp::crypto {
namespace {

constexpr char kKdfLabel[] = "ClipVault OTP Relay KDF v1";
constexpr char kKeyInfoLabel[] = "ClipVault OTP Relay key v1";
constexpr char kAadLabel[] = "ClipVault OTP Relay AEAD v1";

class AlgorithmHandle final {
 public:
  AlgorithmHandle() = default;
  ~AlgorithmHandle() {
    if (value_ != nullptr) {
      BCryptCloseAlgorithmProvider(value_, 0);
    }
  }
  AlgorithmHandle(const AlgorithmHandle&) = delete;
  AlgorithmHandle& operator=(const AlgorithmHandle&) = delete;
  [[nodiscard]] BCRYPT_ALG_HANDLE* put() noexcept { return &value_; }
  [[nodiscard]] BCRYPT_ALG_HANDLE get() const noexcept { return value_; }

 private:
  BCRYPT_ALG_HANDLE value_ = nullptr;
};

class HashHandle final {
 public:
  HashHandle() = default;
  ~HashHandle() { Reset(); }
  HashHandle(const HashHandle&) = delete;
  HashHandle& operator=(const HashHandle&) = delete;
  [[nodiscard]] BCRYPT_HASH_HANDLE* put() noexcept { return &value_; }
  [[nodiscard]] BCRYPT_HASH_HANDLE get() const noexcept { return value_; }
  void Reset() noexcept {
    if (value_ != nullptr) {
      BCryptDestroyHash(value_);
      value_ = nullptr;
    }
  }

 private:
  BCRYPT_HASH_HANDLE value_ = nullptr;
};

class KeyHandle final {
 public:
  KeyHandle() = default;
  ~KeyHandle() { Reset(); }
  KeyHandle(const KeyHandle&) = delete;
  KeyHandle& operator=(const KeyHandle&) = delete;
  [[nodiscard]] BCRYPT_KEY_HANDLE* put() noexcept { return &value_; }
  [[nodiscard]] BCRYPT_KEY_HANDLE get() const noexcept { return value_; }
  void Reset() noexcept {
    if (value_ != nullptr) {
      BCryptDestroyKey(value_);
      value_ = nullptr;
    }
  }

 private:
  BCRYPT_KEY_HANDLE value_ = nullptr;
};

class SensitiveVectorWipe final {
 public:
  explicit SensitiveVectorWipe(std::vector<std::uint8_t>* value) noexcept
      : value_(value) {}
  ~SensitiveVectorWipe() {
    if (value_ != nullptr && !value_->empty()) {
      SecureZeroMemory(value_->data(), value_->size());
    }
  }
  SensitiveVectorWipe(const SensitiveVectorWipe&) = delete;
  SensitiveVectorWipe& operator=(const SensitiveVectorWipe&) = delete;

 private:
  std::vector<std::uint8_t>* value_ = nullptr;
};

bool ToUlong(std::size_t size, ULONG* output) {
  if (output == nullptr || size > std::numeric_limits<ULONG>::max()) {
    return false;
  }
  *output = static_cast<ULONG>(size);
  return true;
}

bool GetDwordProperty(BCRYPT_HANDLE handle, const wchar_t* property,
                      DWORD* output) {
  if (output == nullptr) {
    return false;
  }
  DWORD written = 0;
  return BCRYPT_SUCCESS(BCryptGetProperty(
             handle, property, reinterpret_cast<PUCHAR>(output),
             static_cast<ULONG>(sizeof(*output)), &written, 0)) &&
         written == sizeof(*output);
}

bool Hash(bool hmac, std::span<const std::uint8_t> key,
          std::span<const std::uint8_t> input, Sha256Bytes* output) {
  if (output == nullptr) {
    return false;
  }
  SecureErase(*output);

  ULONG key_size = 0;
  ULONG input_size = 0;
  if (!ToUlong(key.size(), &key_size) || !ToUlong(input.size(), &input_size)) {
    return false;
  }

  AlgorithmHandle algorithm;
  const ULONG flags = hmac ? BCRYPT_ALG_HANDLE_HMAC_FLAG : 0;
  if (!BCRYPT_SUCCESS(BCryptOpenAlgorithmProvider(
          algorithm.put(), BCRYPT_SHA256_ALGORITHM, nullptr, flags))) {
    return false;
  }

  DWORD object_size = 0;
  DWORD hash_size = 0;
  if (!GetDwordProperty(algorithm.get(), BCRYPT_OBJECT_LENGTH, &object_size) ||
      !GetDwordProperty(algorithm.get(), BCRYPT_HASH_LENGTH, &hash_size) ||
      hash_size != output->size()) {
    return false;
  }

  std::vector<std::uint8_t> object(object_size);
  SensitiveVectorWipe object_wipe{&object};
  HashHandle hash;
  PUCHAR key_data = key.empty()
                        ? nullptr
                        : const_cast<PUCHAR>(key.data());  // CNG does not mutate.
  if (!BCRYPT_SUCCESS(BCryptCreateHash(
          algorithm.get(), hash.put(), object.data(), object_size, key_data,
          key_size, 0))) {
    // BCryptCreateHash retains the caller-owned object buffer until the hash
    // handle is destroyed.  Destroy the opaque handle before wiping/releasing
    // that backing storage.
    hash.Reset();
    SecureErase(object);
    return false;
  }

  PUCHAR input_data =
      input.empty() ? nullptr
                    : const_cast<PUCHAR>(input.data());  // CNG does not mutate.
  const bool succeeded =
      BCRYPT_SUCCESS(
          BCryptHashData(hash.get(), input_data, input_size, 0)) &&
      BCRYPT_SUCCESS(BCryptFinishHash(hash.get(), output->data(),
                                      static_cast<ULONG>(output->size()), 0));
  // The hash handle still references object even after FinishHash returns.
  hash.Reset();
  SecureErase(object);
  if (!succeeded) {
    SecureErase(*output);
  }
  return succeeded;
}

bool HmacSha256(std::span<const std::uint8_t> key,
                std::span<const std::uint8_t> input, Sha256Bytes* output) {
  return Hash(true, key, input, output);
}

int HexNibble(char value) {
  if (value >= '0' && value <= '9') {
    return value - '0';
  }
  if (value >= 'a' && value <= 'f') {
    return value - 'a' + 10;
  }
  return -1;
}

void AppendArray(std::vector<std::uint8_t>* output,
                 std::span<const std::uint8_t> input) {
  output->insert(output->end(), input.begin(), input.end());
}

template <std::size_t Size>
void AppendLiteralWithNull(std::vector<std::uint8_t>* output,
                           const char (&value)[Size]) {
  const auto* begin = reinterpret_cast<const std::uint8_t*>(value);
  output->insert(output->end(), begin, begin + Size);
}

void AppendBigEndian(std::vector<std::uint8_t>* output, std::uint64_t value) {
  for (int shift = 56; shift >= 0; shift -= 8) {
    output->push_back(static_cast<std::uint8_t>((value >> shift) & 0xffU));
  }
}

bool IsNormalizedOtp(std::span<const std::uint8_t> value) {
  return value.size() >= 4 && value.size() <= 8 &&
         std::all_of(value.begin(), value.end(), [](std::uint8_t character) {
           return character >= static_cast<std::uint8_t>('0') &&
                  character <= static_cast<std::uint8_t>('9');
         });
}

bool ConfigureAesGcm(AlgorithmHandle* algorithm) {
  if (algorithm == nullptr ||
      !BCRYPT_SUCCESS(BCryptOpenAlgorithmProvider(
          algorithm->put(), BCRYPT_AES_ALGORITHM, nullptr, 0))) {
    return false;
  }
  constexpr wchar_t mode[] = BCRYPT_CHAIN_MODE_GCM;
  return BCRYPT_SUCCESS(BCryptSetProperty(
      algorithm->get(), BCRYPT_CHAINING_MODE,
      reinterpret_cast<PUCHAR>(const_cast<wchar_t*>(mode)),
      static_cast<ULONG>(sizeof(mode)), 0));
}

bool CreateAesKey(AlgorithmHandle* algorithm, const Sha256Bytes& key,
                  std::vector<std::uint8_t>* key_object,
                  KeyHandle* key_handle) {
  if (algorithm == nullptr || key_object == nullptr || key_handle == nullptr) {
    return false;
  }
  DWORD object_size = 0;
  if (!GetDwordProperty(algorithm->get(), BCRYPT_OBJECT_LENGTH, &object_size)) {
    return false;
  }
  key_object->resize(object_size);
  return BCRYPT_SUCCESS(BCryptGenerateSymmetricKey(
      algorithm->get(), key_handle->put(), key_object->data(), object_size,
      const_cast<PUCHAR>(key.data()), static_cast<ULONG>(key.size()), 0));
}

}  // namespace

bool ParseCanonicalUuidV4(std::string_view value, UuidBytes* output) {
  if (output == nullptr || value.size() != 36) {
    return false;
  }
  constexpr std::array<std::size_t, 4> hyphens{8, 13, 18, 23};
  for (const std::size_t position : hyphens) {
    if (value[position] != '-') {
      return false;
    }
  }
  if (value[14] != '4' ||
      (value[19] != '8' && value[19] != '9' && value[19] != 'a' &&
       value[19] != 'b')) {
    return false;
  }

  UuidBytes parsed{};
  std::size_t parsed_index = 0;
  for (std::size_t index = 0; index < value.size();) {
    if (value[index] == '-') {
      ++index;
      continue;
    }
    if (index + 1 >= value.size() || parsed_index >= parsed.size()) {
      return false;
    }
    const int high = HexNibble(value[index]);
    const int low = HexNibble(value[index + 1]);
    if (high < 0 || low < 0) {
      return false;
    }
    parsed[parsed_index++] =
        static_cast<std::uint8_t>((high << 4) | low);
    index += 2;
  }
  if (parsed_index != parsed.size()) {
    return false;
  }
  *output = parsed;
  return true;
}

bool ParseCanonicalDeviceId(std::string_view value, UuidBytes* output) {
  constexpr std::string_view prefix = "device:";
  return value.starts_with(prefix) &&
         ParseCanonicalUuidV4(value.substr(prefix.size()), output);
}

bool Sha256(std::span<const std::uint8_t> input, Sha256Bytes* output) {
  return Hash(false, {}, input, output);
}

bool ComputePairVerifier(std::string_view pair_secret_utf8,
                         Sha256Bytes* output) {
  const auto* data = reinterpret_cast<const std::uint8_t*>(
      pair_secret_utf8.data());
  return Sha256({data, pair_secret_utf8.size()}, output);
}

bool DeriveOtpKey(const Sha256Bytes& pair_verifier,
                  const UuidBytes& session_epoch,
                  const UuidBytes& sender_device,
                  const UuidBytes& target_device,
                  KeySchedule* output) {
  if (output == nullptr) {
    return false;
  }
  KeySchedule derived;
  std::vector<std::uint8_t> salt_input;
  std::vector<std::uint8_t> expand_input;
  struct DerivationWipeGuard final {
    KeySchedule* schedule;
    std::vector<std::uint8_t>* salt;
    std::vector<std::uint8_t>* expand;
    ~DerivationWipeGuard() {
      SecureErase(schedule->key);
      SecureErase(schedule->prk);
      SecureErase(schedule->salt);
      SecureErase(schedule->info);
      SecureErase(*salt);
      SecureErase(*expand);
    }
  } wipe{&derived, &salt_input, &expand_input};

  salt_input.reserve(sizeof(kKdfLabel) + session_epoch.size());
  AppendLiteralWithNull(&salt_input, kKdfLabel);
  AppendArray(&salt_input, session_epoch);
  if (!Sha256(salt_input, &derived.salt) ||
      !HmacSha256(derived.salt, pair_verifier, &derived.prk)) {
    return false;
  }

  derived.info.reserve(sizeof(kKeyInfoLabel) + sender_device.size() +
                       target_device.size());
  AppendLiteralWithNull(&derived.info, kKeyInfoLabel);
  AppendArray(&derived.info, sender_device);
  AppendArray(&derived.info, target_device);

  expand_input = derived.info;
  expand_input.push_back(0x01U);
  if (!HmacSha256(derived.prk, expand_input, &derived.key)) {
    return false;
  }

  // Callers may reuse an output object during explicit key rotation. Wipe its
  // prior schedule before move-assignment can release the old vector storage.
  SecureErase(output->key);
  SecureErase(output->prk);
  SecureErase(output->salt);
  SecureErase(output->info);
  *output = std::move(derived);
  // std::array move-assignment copies the key bytes. The guard intentionally
  // wipes the moved-from local schedule as this function returns.
  return true;
}

std::vector<std::uint8_t> BuildAad(const EnvelopeFields& fields) {
  std::vector<std::uint8_t> aad;
  aad.reserve(sizeof(kAadLabel) + 1 + 4 * UuidBytes{}.size() + 3 * 8);
  AppendLiteralWithNull(&aad, kAadLabel);
  aad.push_back(fields.protocol_version);
  AppendArray(&aad, fields.session_epoch);
  AppendArray(&aad, fields.event_id);
  AppendArray(&aad, fields.sender_device);
  AppendArray(&aad, fields.target_device);
  AppendBigEndian(&aad, fields.sequence);
  AppendBigEndian(&aad, fields.issued_at_unix_ms);
  AppendBigEndian(&aad, fields.expires_at_unix_ms);
  return aad;
}

bool EncryptOtp(const Sha256Bytes& key, const NonceBytes& nonce,
                std::span<const std::uint8_t> aad,
                std::span<const std::uint8_t> plaintext,
                std::vector<std::uint8_t>* ciphertext, TagBytes* tag) {
  if (ciphertext == nullptr || tag == nullptr) {
    return false;
  }
  SecureErase(*ciphertext);
  ciphertext->clear();
  SecureErase(*tag);
  if (!IsNormalizedOtp(plaintext)) return false;
  ULONG plaintext_size = 0;
  ULONG aad_size = 0;
  if (!ToUlong(plaintext.size(), &plaintext_size) ||
      !ToUlong(aad.size(), &aad_size)) {
    return false;
  }

  AlgorithmHandle algorithm;
  std::vector<std::uint8_t> key_object;
  SensitiveVectorWipe key_object_wipe{&key_object};
  KeyHandle key_handle;
  if (!ConfigureAesGcm(&algorithm) ||
       !CreateAesKey(&algorithm, key, &key_object, &key_handle)) {
    key_handle.Reset();
    SecureErase(key_object);
    return false;
  }

  BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO authentication_info;
  BCRYPT_INIT_AUTH_MODE_INFO(authentication_info);
  authentication_info.pbNonce = const_cast<PUCHAR>(nonce.data());
  authentication_info.cbNonce = static_cast<ULONG>(nonce.size());
  authentication_info.pbAuthData =
      aad.empty() ? nullptr : const_cast<PUCHAR>(aad.data());
  authentication_info.cbAuthData = aad_size;
  authentication_info.pbTag = tag->data();
  authentication_info.cbTag = static_cast<ULONG>(tag->size());

  ciphertext->assign(plaintext.size(), 0);
  ULONG bytes_written = 0;
  const NTSTATUS status = BCryptEncrypt(
      key_handle.get(), const_cast<PUCHAR>(plaintext.data()), plaintext_size,
      &authentication_info, nullptr, 0, ciphertext->data(), plaintext_size,
      &bytes_written, 0);
  key_handle.Reset();
  SecureErase(key_object);
  if (!BCRYPT_SUCCESS(status) || bytes_written != ciphertext->size()) {
    SecureErase(*ciphertext);
    ciphertext->clear();
    SecureErase(*tag);
    return false;
  }
  return true;
}

bool DecryptOtp(const Sha256Bytes& key, const NonceBytes& nonce,
                std::span<const std::uint8_t> aad,
                std::span<const std::uint8_t> ciphertext,
                const TagBytes& tag, std::vector<std::uint8_t>* plaintext) {
  if (plaintext == nullptr) {
    return false;
  }
  SecureErase(*plaintext);
  plaintext->clear();
  if (ciphertext.size() < 4 || ciphertext.size() > 8) return false;
  ULONG ciphertext_size = 0;
  ULONG aad_size = 0;
  if (!ToUlong(ciphertext.size(), &ciphertext_size) ||
      !ToUlong(aad.size(), &aad_size)) {
    return false;
  }

  AlgorithmHandle algorithm;
  std::vector<std::uint8_t> key_object;
  SensitiveVectorWipe key_object_wipe{&key_object};
  KeyHandle key_handle;
  if (!ConfigureAesGcm(&algorithm) ||
       !CreateAesKey(&algorithm, key, &key_object, &key_handle)) {
    key_handle.Reset();
    SecureErase(key_object);
    return false;
  }

  BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO authentication_info;
  BCRYPT_INIT_AUTH_MODE_INFO(authentication_info);
  authentication_info.pbNonce = const_cast<PUCHAR>(nonce.data());
  authentication_info.cbNonce = static_cast<ULONG>(nonce.size());
  authentication_info.pbAuthData =
      aad.empty() ? nullptr : const_cast<PUCHAR>(aad.data());
  authentication_info.cbAuthData = aad_size;
  authentication_info.pbTag = const_cast<PUCHAR>(tag.data());
  authentication_info.cbTag = static_cast<ULONG>(tag.size());

  plaintext->assign(ciphertext.size(), 0);
  ULONG bytes_written = 0;
  const NTSTATUS status = BCryptDecrypt(
      key_handle.get(), const_cast<PUCHAR>(ciphertext.data()), ciphertext_size,
      &authentication_info, nullptr, 0, plaintext->data(), ciphertext_size,
      &bytes_written, 0);
  key_handle.Reset();
  SecureErase(key_object);
  if (!BCRYPT_SUCCESS(status) || bytes_written != plaintext->size() ||
      !IsNormalizedOtp(*plaintext)) {
    SecureErase(*plaintext);
    plaintext->clear();
    return false;
  }
  return true;
}

void SecureErase(std::span<std::uint8_t> bytes) noexcept {
  if (!bytes.empty()) {
    SecureZeroMemory(bytes.data(), bytes.size());
  }
}

NonceReuseGuard::NonceReuseGuard(std::size_t capacity) : capacity_(capacity) {
  nonces_.reserve(capacity_);
}

NonceReuseGuard::~NonceReuseGuard() { Clear(); }

bool NonceReuseGuard::TryRemember(const NonceBytes& nonce) {
  if (nonces_.size() >= capacity_ ||
      std::find(nonces_.begin(), nonces_.end(), nonce) != nonces_.end()) {
    return false;
  }
  nonces_.push_back(nonce);
  return true;
}

void NonceReuseGuard::Clear() noexcept {
  for (auto& nonce : nonces_) {
    SecureErase(nonce);
  }
  nonces_.clear();
}

}  // namespace clipvault::otp::crypto
