#pragma once

#include <windows.h>
#include <softpub.h>
#include <tlhelp32.h>
#include <wincrypt.h>
#include <wintrust.h>

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <cwctype>
#include <string>
#include <string_view>
#include <vector>

namespace clipvault::windows::trust {

struct TrustedPublisherFingerprint final {
  std::array<std::uint8_t, 32> certificate_sha256{};
  std::array<std::uint8_t, 32> subject_public_key_info_sha256{};

  bool operator==(const TrustedPublisherFingerprint&) const = default;
};

namespace detail {

template <typename Function>
Function ResolveSystemFunction(const wchar_t* module_name,
                               const char* function_name,
                               HMODULE* module) noexcept {
  *module = LoadLibraryExW(module_name, nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32);
  if (*module == nullptr) return nullptr;
  const FARPROC address = GetProcAddress(*module, function_name);
  static_assert(sizeof(Function) == sizeof(address));
  Function function = nullptr;
  std::memcpy(&function, &address, sizeof(function));
  if (function == nullptr) {
    FreeLibrary(*module);
    *module = nullptr;
  }
  return function;
}

template <typename Function>
Function ResolveLoadedFunction(HMODULE module,
                               const char* function_name) noexcept {
  if (module == nullptr) return nullptr;
  const FARPROC address = GetProcAddress(module, function_name);
  static_assert(sizeof(Function) == sizeof(address));
  Function function = nullptr;
  std::memcpy(&function, &address, sizeof(function));
  return function;
}

inline std::wstring NormalizeFinalPath(std::wstring path) {
  constexpr std::wstring_view kUncPrefix = L"\\\\?\\UNC\\";
  constexpr std::wstring_view kDevicePrefix = L"\\\\?\\";
  if (path.starts_with(kUncPrefix)) {
    path = L"\\\\" + path.substr(kUncPrefix.size());
  } else if (path.starts_with(kDevicePrefix)) {
    path.erase(0, kDevicePrefix.size());
  }
  std::transform(path.begin(), path.end(), path.begin(),
                 [](wchar_t value) {
                   return static_cast<wchar_t>(std::towlower(value));
                 });
  return path;
}

inline bool ReadTokenUser(HANDLE token, std::vector<std::uint8_t>* output) {
  if (token == nullptr || output == nullptr) return false;
  HMODULE advapi = nullptr;
  const auto get_token_information = ResolveSystemFunction<
      decltype(&GetTokenInformation)>(L"advapi32.dll", "GetTokenInformation",
                                      &advapi);
  if (get_token_information == nullptr) return false;
  DWORD required = 0;
  get_token_information(token, TokenUser, nullptr, 0, &required);
  output->resize(required);
  const bool loaded =
      required != 0 &&
      get_token_information(token, TokenUser, output->data(), required,
                            &required) != FALSE;
  FreeLibrary(advapi);
  if (!loaded) output->clear();
  return loaded;
}

inline bool SameUser(HANDLE process) {
  if (process == nullptr) return false;
  HMODULE advapi = nullptr;
  const auto open_process_token = ResolveSystemFunction<
      decltype(&OpenProcessToken)>(L"advapi32.dll", "OpenProcessToken",
                                   &advapi);
  if (open_process_token == nullptr) return false;
  HANDLE self_token = nullptr;
  HANDLE peer_token = nullptr;
  const bool opened =
      open_process_token(GetCurrentProcess(), TOKEN_QUERY, &self_token) !=
          FALSE &&
      open_process_token(process, TOKEN_QUERY, &peer_token) != FALSE;
  FreeLibrary(advapi);
  if (!opened) {
    if (peer_token != nullptr) CloseHandle(peer_token);
    if (self_token != nullptr) CloseHandle(self_token);
    return false;
  }
  std::vector<std::uint8_t> self;
  std::vector<std::uint8_t> peer;
  const bool loaded = ReadTokenUser(self_token, &self) &&
                      ReadTokenUser(peer_token, &peer);
  CloseHandle(peer_token);
  CloseHandle(self_token);
  if (!loaded) return false;

  HMODULE equal_sid_module = nullptr;
  const auto equal_sid = ResolveSystemFunction<decltype(&EqualSid)>(
      L"advapi32.dll", "EqualSid", &equal_sid_module);
  if (equal_sid == nullptr) return false;
  const bool same =
      equal_sid(reinterpret_cast<const TOKEN_USER*>(self.data())->User.Sid,
                reinterpret_cast<const TOKEN_USER*>(peer.data())->User.Sid) !=
      FALSE;
  FreeLibrary(equal_sid_module);
  return same;
}

inline bool HashSha256(decltype(&CryptHashCertificate2) hash,
                       const std::uint8_t* bytes, DWORD size,
                       std::array<std::uint8_t, 32>* output) {
  if (hash == nullptr || bytes == nullptr || size == 0 || output == nullptr) {
    return false;
  }
  DWORD output_size = static_cast<DWORD>(output->size());
  return hash(L"SHA256", 0, nullptr, bytes, size, output->data(),
              &output_size) != FALSE &&
         output_size == output->size();
}

inline bool FingerprintSignerCertificate(
    PCCERT_CONTEXT certificate,
    decltype(&CryptEncodeObjectEx) encode,
    decltype(&CryptHashCertificate2) hash,
    TrustedPublisherFingerprint* output) {
  if (certificate == nullptr || certificate->pCertInfo == nullptr ||
      certificate->pbCertEncoded == nullptr || certificate->cbCertEncoded == 0 ||
      encode == nullptr || hash == nullptr || output == nullptr) {
    return false;
  }

  TrustedPublisherFingerprint candidate;
  if (!HashSha256(hash, certificate->pbCertEncoded,
                  certificate->cbCertEncoded,
                  &candidate.certificate_sha256)) {
    return false;
  }

  DWORD encoded_size = 0;
  if (!encode(X509_ASN_ENCODING, X509_PUBLIC_KEY_INFO,
              &certificate->pCertInfo->SubjectPublicKeyInfo, 0, nullptr,
              nullptr, &encoded_size) ||
      encoded_size == 0) {
    return false;
  }
  std::vector<std::uint8_t> encoded(encoded_size);
  if (!encode(X509_ASN_ENCODING, X509_PUBLIC_KEY_INFO,
              &certificate->pCertInfo->SubjectPublicKeyInfo, 0, nullptr,
              encoded.data(), &encoded_size) ||
      encoded_size == 0 || encoded_size > encoded.size()) {
    SecureZeroMemory(encoded.data(), encoded.size());
    return false;
  }
  const bool hashed =
      HashSha256(hash, encoded.data(), encoded_size,
                 &candidate.subject_public_key_info_sha256);
  SecureZeroMemory(encoded.data(), encoded.size());
  if (!hashed) return false;
  *output = candidate;
  return true;
}

inline void AppendUniquePublisher(
    const TrustedPublisherFingerprint& candidate,
    std::vector<TrustedPublisherFingerprint>* publishers) {
  if (publishers == nullptr) return;
  const auto found = std::find(publishers->begin(), publishers->end(),
                               candidate);
  if (found == publishers->end()) publishers->push_back(candidate);
}

struct WinTrustStateCloser final {
  decltype(&WinVerifyTrust) verify = nullptr;
  GUID* policy = nullptr;
  WINTRUST_DATA* trust = nullptr;

  ~WinTrustStateCloser() {
    if (verify == nullptr || policy == nullptr || trust == nullptr ||
        trust->hWVTStateData == nullptr) {
      return;
    }
    trust->dwStateAction = WTD_STATEACTION_CLOSE;
    verify(nullptr, policy, trust);
    trust->hWVTStateData = nullptr;
  }
};

inline bool VerifySignatureIndex(
    const std::wstring& canonical, DWORD signature_index,
    bool query_secondary_count, decltype(&WinVerifyTrust) verify,
    decltype(&WTHelperProvDataFromStateData) provider_from_state,
    decltype(&WTHelperGetProvSignerFromChain) signer_from_chain,
    decltype(&WTHelperGetProvCertFromChain) certificate_from_chain,
    decltype(&CryptEncodeObjectEx) encode,
    decltype(&CryptHashCertificate2) hash, DWORD* secondary_count,
    std::vector<TrustedPublisherFingerprint>* publishers) {
  if (canonical.empty() || verify == nullptr || provider_from_state == nullptr ||
      signer_from_chain == nullptr || certificate_from_chain == nullptr ||
      encode == nullptr || hash == nullptr || publishers == nullptr) {
    return false;
  }

  WINTRUST_FILE_INFO file{};
  file.cbStruct = sizeof(file);
  file.pcwszFilePath = canonical.c_str();
  WINTRUST_SIGNATURE_SETTINGS signature{};
  signature.cbStruct = sizeof(signature);
  signature.dwIndex = signature_index;
  signature.dwFlags = query_secondary_count ? WSS_GET_SECONDARY_SIG_COUNT
                                            : WSS_VERIFY_SPECIFIC;
  WINTRUST_DATA trust{};
  trust.cbStruct = sizeof(trust);
  trust.dwUIChoice = WTD_UI_NONE;
  trust.fdwRevocationChecks = WTD_REVOKE_NONE;
  trust.dwUnionChoice = WTD_CHOICE_FILE;
  trust.pFile = &file;
  trust.dwStateAction = WTD_STATEACTION_VERIFY;
  trust.dwProvFlags = WTD_CACHE_ONLY_URL_RETRIEVAL;
  trust.pSignatureSettings = &signature;
  GUID policy = WINTRUST_ACTION_GENERIC_VERIFY_V2;
  WinTrustStateCloser close_state{verify, &policy, &trust};

  const LONG status = verify(nullptr, &policy, &trust);
  if (query_secondary_count && secondary_count != nullptr) {
    // Bound work and fail closed rather than trusting only an arbitrary prefix
    // of an unexpectedly large signer set.
    if (signature.cSecondarySigs > 16) {
      *secondary_count = 0;
      return false;
    }
    *secondary_count = signature.cSecondarySigs;
  }

  bool trusted_signer_loaded = false;
  if (status == ERROR_SUCCESS && trust.hWVTStateData != nullptr) {
    CRYPT_PROVIDER_DATA* provider =
        provider_from_state(trust.hWVTStateData);
    CRYPT_PROVIDER_SGNR* signer =
        provider == nullptr
            ? nullptr
            : signer_from_chain(provider, 0, FALSE, 0);
    CRYPT_PROVIDER_CERT* certificate =
        signer == nullptr ? nullptr : certificate_from_chain(signer, 0);
    TrustedPublisherFingerprint fingerprint;
    trusted_signer_loaded =
        certificate != nullptr &&
        FingerprintSignerCertificate(certificate->pCert, encode, hash,
                                     &fingerprint);
    if (trusted_signer_loaded) {
      AppendUniquePublisher(fingerprint, publishers);
    }
  }

  return status == ERROR_SUCCESS && trusted_signer_loaded;
}

}  // namespace detail

inline std::wstring ParentDirectory(const std::wstring& path) {
  const auto separator = path.find_last_of(L"\\/");
  return separator == std::wstring::npos ? std::wstring{}
                                         : path.substr(0, separator);
}

inline std::wstring FileName(const std::wstring& path) {
  const auto separator = path.find_last_of(L"\\/");
  return separator == std::wstring::npos ? path : path.substr(separator + 1);
}

inline std::wstring JoinPath(const std::wstring& directory,
                             std::wstring_view leaf) {
  if (directory.empty() || leaf.empty()) return {};
  return directory + L"\\" + std::wstring(leaf);
}

inline std::wstring CanonicalFilePath(const std::wstring& path) {
  if (path.empty()) return {};
  HANDLE file = CreateFileW(path.c_str(), FILE_READ_ATTRIBUTES,
                            FILE_SHARE_READ | FILE_SHARE_WRITE |
                                FILE_SHARE_DELETE,
                            nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL,
                            nullptr);
  if (file == INVALID_HANDLE_VALUE) return {};
  std::array<wchar_t, 32768> output{};
  const DWORD length = GetFinalPathNameByHandleW(
      file, output.data(), static_cast<DWORD>(output.size()),
      FILE_NAME_NORMALIZED | VOLUME_NAME_DOS);
  CloseHandle(file);
  if (length == 0 || length >= static_cast<DWORD>(output.size())) return {};
  return detail::NormalizeFinalPath(std::wstring(output.data(), length));
}

inline std::wstring CurrentExecutablePath() {
  std::array<wchar_t, 32768> path{};
  const DWORD length =
      GetModuleFileNameW(nullptr, path.data(), static_cast<DWORD>(path.size()));
  if (length == 0 || length >= static_cast<DWORD>(path.size())) return {};
  return CanonicalFilePath(std::wstring(path.data(), length));
}

inline std::wstring CurrentModulePath() {
  static const wchar_t kModuleAnchor = L'\0';
  HMODULE module = nullptr;
  if (!GetModuleHandleExW(
          GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
              GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
          &kModuleAnchor, &module)) {
    return {};
  }
  std::array<wchar_t, 32768> path{};
  const DWORD length =
      GetModuleFileNameW(module, path.data(), static_cast<DWORD>(path.size()));
  if (length == 0 || length >= static_cast<DWORD>(path.size())) return {};
  return CanonicalFilePath(std::wstring(path.data(), length));
}

inline std::wstring ProcessExecutablePath(DWORD process_id) {
  if (process_id == 0) return {};
  HANDLE process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE,
                               process_id);
  if (process == nullptr) return {};
  std::array<wchar_t, 32768> path{};
  DWORD length = static_cast<DWORD>(path.size());
  const bool queried =
      QueryFullProcessImageNameW(process, 0, path.data(), &length) != FALSE;
  CloseHandle(process);
  return queried ? CanonicalFilePath(std::wstring(path.data(), length))
                 : std::wstring{};
}

inline bool ProcessMatchesCurrentUserAndSession(DWORD process_id) {
  if (process_id == 0) return false;
  DWORD current_session = 0;
  DWORD peer_session = 0;
  if (!ProcessIdToSessionId(GetCurrentProcessId(), &current_session) ||
      !ProcessIdToSessionId(process_id, &peer_session) ||
      current_session != peer_session) {
    return false;
  }
  HANDLE process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE,
                               process_id);
  if (process == nullptr) return false;
  const bool same_user = detail::SameUser(process);
  CloseHandle(process);
  return same_user;
}

inline bool TrustedPublisherFingerprints(
    const std::wstring& path,
    std::vector<TrustedPublisherFingerprint>* publishers) {
  if (publishers == nullptr) return false;
  publishers->clear();
  const std::wstring canonical = CanonicalFilePath(path);
  if (canonical.empty()) return false;

  HMODULE wintrust = nullptr;
  HMODULE crypt32 = nullptr;
  wintrust =
      LoadLibraryExW(L"wintrust.dll", nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32);
  crypt32 =
      LoadLibraryExW(L"crypt32.dll", nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32);
  if (wintrust == nullptr || crypt32 == nullptr) {
    if (crypt32 != nullptr) FreeLibrary(crypt32);
    if (wintrust != nullptr) FreeLibrary(wintrust);
    return false;
  }

  const auto verify = detail::ResolveLoadedFunction<decltype(&WinVerifyTrust)>(
      wintrust, "WinVerifyTrust");
  const auto provider_from_state = detail::ResolveLoadedFunction<
      decltype(&WTHelperProvDataFromStateData)>(
      wintrust, "WTHelperProvDataFromStateData");
  const auto signer_from_chain = detail::ResolveLoadedFunction<
      decltype(&WTHelperGetProvSignerFromChain)>(
      wintrust, "WTHelperGetProvSignerFromChain");
  const auto certificate_from_chain = detail::ResolveLoadedFunction<
      decltype(&WTHelperGetProvCertFromChain)>(
      wintrust, "WTHelperGetProvCertFromChain");
  const auto encode = detail::ResolveLoadedFunction<
      decltype(&CryptEncodeObjectEx)>(crypt32, "CryptEncodeObjectEx");
  const auto hash = detail::ResolveLoadedFunction<
      decltype(&CryptHashCertificate2)>(crypt32, "CryptHashCertificate2");

  DWORD secondary_count = 0;
  detail::VerifySignatureIndex(
      canonical, 0, true, verify, provider_from_state, signer_from_chain,
      certificate_from_chain, encode, hash, &secondary_count, publishers);
  // A dual-signed binary may have a legacy primary signature and a newer
  // secondary signature.  Compare only leaves from signatures that
  // WinVerifyTrust independently accepted; never scrape an arbitrary embedded
  // certificate and call it a publisher identity.
  for (DWORD signature_index = 1; signature_index <= secondary_count;
       ++signature_index) {
    detail::VerifySignatureIndex(
        canonical, signature_index, false, verify, provider_from_state,
        signer_from_chain, certificate_from_chain, encode, hash, nullptr,
        publishers);
  }

  const bool loaded = !publishers->empty();
  if (!loaded) publishers->clear();
  FreeLibrary(crypt32);
  FreeLibrary(wintrust);
  return loaded;
}

inline bool HasTrustedSignature(const std::wstring& path) {
  std::vector<TrustedPublisherFingerprint> publishers;
  return TrustedPublisherFingerprints(path, &publishers);
}

inline bool PublisherSetsIntersect(
    const std::vector<TrustedPublisherFingerprint>& first,
    const std::vector<TrustedPublisherFingerprint>& second) {
  for (const auto& left : first) {
    const auto found = std::find_if(
        second.begin(), second.end(),
        [&](const TrustedPublisherFingerprint& right) {
          return left.subject_public_key_info_sha256 ==
                 right.subject_public_key_info_sha256;
        });
    if (found != second.end()) return true;
  }
  return false;
}

inline bool HaveSameTrustedPublisher(const std::wstring& first_path,
                                     const std::wstring& second_path) {
  std::vector<TrustedPublisherFingerprint> first;
  std::vector<TrustedPublisherFingerprint> second;
  return TrustedPublisherFingerprints(first_path, &first) &&
         TrustedPublisherFingerprints(second_path, &second) &&
         PublisherSetsIntersect(first, second);
}

inline bool ExplicitUnsignedTestTrustEnabled(
    const std::wstring& test_namespace_suffix) {
  if (test_namespace_suffix.empty()) return false;
  wchar_t value[2]{};
  const DWORD length = GetEnvironmentVariableW(
      L"CLIPVAULT_INSECURE_TEST_PIPE_TRUST", value,
      static_cast<DWORD>(std::size(value)));
  return length == 1 && value[0] == L'1';
}

inline bool ExplicitUnsignedDevelopmentTrustEnabled() {
#if !defined(CLIPVAULT_ENABLE_INSECURE_DEVELOPMENT_TRUST)
  // Production builds do not contain an unsigned-trust escape hatch.  The
  // environment variable is intentionally insufficient on its own because
  // it can be inherited by a packaged Host or TSF process.
  return false;
#else
  // This opt-in relaxes only Authenticode and same-publisher comparison for an
  // otherwise exact installed path. It is intentionally absent from installer
  // defaults and must be enabled at build time plus explicitly set for the
  // process tree; unsigned daily binaries remain unusable otherwise.
  wchar_t value[2]{};
  const DWORD length = GetEnvironmentVariableW(
      L"CLIPVAULT_INSECURE_DEVELOPMENT_PIPE_TRUST", value,
      static_cast<DWORD>(std::size(value)));
  return length == 1 && value[0] == L'1';
#endif
}

inline bool VerifyNamedPipeServer(
    HANDLE pipe, const std::wstring& expected_server_path,
    const std::wstring& test_namespace_suffix) {
  if (pipe == INVALID_HANDLE_VALUE) return false;
  ULONG process_id = 0;
  if (!GetNamedPipeServerProcessId(pipe, &process_id) || process_id == 0 ||
      !ProcessMatchesCurrentUserAndSession(process_id)) {
    return false;
  }
  const std::wstring actual = ProcessExecutablePath(process_id);
  if (actual.empty()) return false;
  const std::wstring expected = CanonicalFilePath(expected_server_path);
  const bool exact_expected = !expected.empty() && actual == expected;
  if (exact_expected && ExplicitUnsignedDevelopmentTrustEnabled()) return true;

  if (exact_expected &&
      HaveSameTrustedPublisher(actual, CurrentModulePath())) {
    return true;
  }

  // Local tests must opt into both a private pipe namespace and an explicitly
  // insecure trust switch. Production clients never take this branch. The
  // escape hatch skips only signature/publisher comparison: it still requires
  // same user/session above and one of the exact canonical paths known to the
  // isolated test.
  if (!ExplicitUnsignedTestTrustEnabled(test_namespace_suffix)) return false;
  if (exact_expected) return true;
  const std::wstring self = CurrentExecutablePath();
  return process_id == GetCurrentProcessId() && !self.empty() && actual == self;
}

inline bool ProcessHasModuleAt(
    DWORD process_id, const std::vector<std::wstring>& expected_module_paths,
    bool require_trusted_signature) {
  if (process_id == 0 || expected_module_paths.empty()) return false;
  std::vector<std::wstring> expected;
  expected.reserve(expected_module_paths.size());
  for (const auto& path : expected_module_paths) {
    const std::wstring canonical = CanonicalFilePath(path);
    if (!canonical.empty()) expected.push_back(canonical);
  }
  if (expected.empty()) return false;

  HANDLE snapshot = CreateToolhelp32Snapshot(
      TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, process_id);
  if (snapshot == INVALID_HANDLE_VALUE) return false;
  MODULEENTRY32W module{};
  module.dwSize = static_cast<DWORD>(sizeof(module));
  bool matched = false;
  if (Module32FirstW(snapshot, &module)) {
    do {
      const std::wstring actual = CanonicalFilePath(module.szExePath);
      const auto found = std::find(expected.begin(), expected.end(), actual);
      if (found != expected.end() &&
          (!require_trusted_signature || HasTrustedSignature(actual))) {
        matched = true;
        break;
      }
    } while (Module32NextW(snapshot, &module));
  }
  CloseHandle(snapshot);
  return matched;
}

inline bool ProcessHasModuleAtWithSamePublisher(
    DWORD process_id, const std::vector<std::wstring>& expected_module_paths,
    const std::wstring& trusted_reference_path) {
  if (process_id == 0 || expected_module_paths.empty()) return false;
  std::vector<TrustedPublisherFingerprint> reference_publishers;
  if (!TrustedPublisherFingerprints(trusted_reference_path,
                                    &reference_publishers)) {
    return false;
  }

  std::vector<std::wstring> expected;
  expected.reserve(expected_module_paths.size());
  for (const auto& path : expected_module_paths) {
    const std::wstring canonical = CanonicalFilePath(path);
    if (!canonical.empty()) expected.push_back(canonical);
  }
  if (expected.empty()) return false;

  HANDLE snapshot = CreateToolhelp32Snapshot(
      TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, process_id);
  if (snapshot == INVALID_HANDLE_VALUE) return false;
  MODULEENTRY32W module{};
  module.dwSize = static_cast<DWORD>(sizeof(module));
  bool matched = false;
  if (Module32FirstW(snapshot, &module)) {
    do {
      const std::wstring actual = CanonicalFilePath(module.szExePath);
      if (std::find(expected.begin(), expected.end(), actual) ==
          expected.end()) {
        continue;
      }
      std::vector<TrustedPublisherFingerprint> module_publishers;
      if (TrustedPublisherFingerprints(actual, &module_publishers) &&
          PublisherSetsIntersect(reference_publishers, module_publishers)) {
        matched = true;
        break;
      }
    } while (Module32NextW(snapshot, &module));
  }
  CloseHandle(snapshot);
  return matched;
}

}  // namespace clipvault::windows::trust
