#include "client_identity.h"

#include <softpub.h>
#include <wintrust.h>

#include <algorithm>
#include <array>
#include <cwctype>
#include <vector>

namespace clipvault::otp::broker {
namespace {

std::wstring FullPath(std::wstring path) {
  std::array<wchar_t, 32768> output{};
  const DWORD length = GetFullPathNameW(path.c_str(),
                                        static_cast<DWORD>(output.size()),
                                        output.data(), nullptr);
  if (length == 0 || length >= output.size()) return {};
  std::wstring result(output.data(), length);
  std::transform(result.begin(), result.end(), result.begin(), towlower);
  return result;
}

std::wstring Parent(std::wstring path) {
  const auto separator = path.find_last_of(L"\\/");
  return separator == std::wstring::npos ? std::wstring{}
                                         : path.substr(0, separator);
}

std::wstring CurrentExecutable() {
  std::array<wchar_t, 32768> path{};
  const DWORD length = GetModuleFileNameW(nullptr, path.data(),
                                          static_cast<DWORD>(path.size()));
  return length == 0 || length >= path.size()
             ? std::wstring{}
             : FullPath(std::wstring(path.data(), length));
}

std::wstring ProcessExecutable(DWORD process_id) {
  HANDLE process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE,
                               process_id);
  if (process == nullptr) return {};
  std::array<wchar_t, 32768> path{};
  DWORD length = static_cast<DWORD>(path.size());
  const bool queried = QueryFullProcessImageNameW(process, 0, path.data(),
                                                   &length) != FALSE;
  CloseHandle(process);
  return queried ? FullPath(std::wstring(path.data(), length)) : std::wstring{};
}

bool TrustedFile(const std::wstring& path) {
  if (path.empty()) return false;
  WINTRUST_FILE_INFO file_info{};
  file_info.cbStruct = sizeof(file_info);
  file_info.pcwszFilePath = path.c_str();
  WINTRUST_DATA trust_data{};
  trust_data.cbStruct = sizeof(trust_data);
  trust_data.dwUIChoice = WTD_UI_NONE;
  trust_data.fdwRevocationChecks = WTD_REVOKE_NONE;
  trust_data.dwUnionChoice = WTD_CHOICE_FILE;
  trust_data.pFile = &file_info;
  trust_data.dwStateAction = WTD_STATEACTION_VERIFY;
  trust_data.dwProvFlags = WTD_CACHE_ONLY_URL_RETRIEVAL;
  GUID action = WINTRUST_ACTION_GENERIC_VERIFY_V2;
  const LONG status = WinVerifyTrust(nullptr, &action, &trust_data);
  trust_data.dwStateAction = WTD_STATEACTION_CLOSE;
  WinVerifyTrust(nullptr, &action, &trust_data);
  return status == ERROR_SUCCESS;
}

bool SameUser(DWORD process_id) {
  HANDLE self_token = nullptr;
  HANDLE peer_process = nullptr;
  HANDLE peer_token = nullptr;
  bool same = false;
  DWORD self_size = 0;
  DWORD peer_size = 0;
  if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &self_token))
    goto cleanup;
  peer_process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE,
                             process_id);
  if (peer_process == nullptr ||
      !OpenProcessToken(peer_process, TOKEN_QUERY, &peer_token))
    goto cleanup;
  GetTokenInformation(self_token, TokenUser, nullptr, 0, &self_size);
  GetTokenInformation(peer_token, TokenUser, nullptr, 0, &peer_size);
  if (self_size == 0 || peer_size == 0) goto cleanup;
  {
    std::vector<std::uint8_t> self(self_size);
    std::vector<std::uint8_t> peer(peer_size);
    if (!GetTokenInformation(self_token, TokenUser, self.data(), self_size,
                             &self_size) ||
        !GetTokenInformation(peer_token, TokenUser, peer.data(), peer_size,
                             &peer_size))
      goto cleanup;
    same = EqualSid(reinterpret_cast<TOKEN_USER*>(self.data())->User.Sid,
                    reinterpret_cast<TOKEN_USER*>(peer.data())->User.Sid) !=
           FALSE;
  }
cleanup:
  if (peer_token != nullptr) CloseHandle(peer_token);
  if (peer_process != nullptr) CloseHandle(peer_process);
  if (self_token != nullptr) CloseHandle(self_token);
  return same;
}

}  // namespace

ProductionBrokerClientAuthorizer::ProductionBrokerClientAuthorizer() {
  broker_path_ = CurrentExecutable();
  const auto broker_directory = Parent(broker_path_);
  const auto ime_directory = Parent(broker_directory);
  const auto app_directory = Parent(ime_directory);
  desktop_path_ = FullPath(app_directory + L"\\ClipVault.exe");
  host_path_ = FullPath(ime_directory + L"\\host-x64\\ClipVaultImeHost.exe");
  broker_trusted_ = TrustedFile(broker_path_);
}

bool ProductionBrokerClientAuthorizer::Authorize(
    DWORD process_id, BrokerClientRole role) noexcept {
  if (!broker_trusted_ || process_id == 0 || !SameUser(process_id)) return false;
  const auto actual = ProcessExecutable(process_id);
  const auto& expected = role == BrokerClientRole::kOpaqueDesktopOffer
                             ? desktop_path_
                             : host_path_;
  return !actual.empty() && actual == expected && TrustedFile(actual);
}

}  // namespace clipvault::otp::broker
