#include "client_identity.h"

#include "../../ime/common/pipe_peer_trust.h"

#include <algorithm>
#include <array>
#include <cwctype>
#include <wintrust.h>

namespace clipvault::otp::broker {
namespace {

void RetainStaticWinVerifyTrustImport() noexcept {
  // The Broker dependency audit requires a statically visible WinTrust
  // boundary.  Trust helpers remain resolved through the system32 module in
  // pipe_peer_trust.h so TSF and Host do not inherit this import.
  using WinVerifyTrustFunction = decltype(&WinVerifyTrust);
  volatile WinVerifyTrustFunction function = &WinVerifyTrust;
  (void)function;
}

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

}  // namespace

ProductionBrokerClientAuthorizer::ProductionBrokerClientAuthorizer() {
  RetainStaticWinVerifyTrustImport();
  broker_path_ = CurrentExecutable();
  const auto broker_directory = Parent(broker_path_);
  const auto ime_directory = Parent(broker_directory);
  const auto app_directory = Parent(ime_directory);
  desktop_path_ = FullPath(app_directory + L"\\ClipVault.exe");
  host_path_ = FullPath(ime_directory + L"\\host-x64\\ClipVaultImeHost.exe");
}

bool ProductionBrokerClientAuthorizer::Authorize(
    DWORD process_id, BrokerClientRole role) noexcept {
  try {
    using namespace clipvault::windows::trust;
    if (process_id == 0 || !ProcessMatchesCurrentUserAndSession(process_id)) {
      return false;
    }
    const auto actual = ProcessExecutablePath(process_id);
    const auto& expected = role == BrokerClientRole::kImeHostControl
                               ? host_path_
                               : desktop_path_;
    if (actual.empty() || actual != CanonicalFilePath(expected)) return false;
    return ExplicitUnsignedDevelopmentTrustEnabled() ||
           HaveSameTrustedPublisher(actual, broker_path_);
  } catch (...) {
    // This function is a pipe authorization boundary. Allocation or platform
    // trust-provider failures must deny the peer rather than terminate Broker.
    return false;
  }
}

}  // namespace clipvault::otp::broker
