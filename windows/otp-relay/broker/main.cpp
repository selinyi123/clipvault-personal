#include "broker_server.h"
#include "client_identity.h"
#include "otp_broker_service.h"
#include "otp_prompt.h"
#include "pair_credential.h"

#include <windows.h>

#include <atomic>
#include <string>

namespace {

std::atomic_bool g_stop = false;

BOOL WINAPI StopHandler(DWORD event) {
  if (event == CTRL_C_EVENT || event == CTRL_BREAK_EVENT ||
      event == CTRL_CLOSE_EVENT || event == CTRL_LOGOFF_EVENT ||
      event == CTRL_SHUTDOWN_EVENT) {
    g_stop = true;
    return TRUE;
  }
  return FALSE;
}

}  // namespace

int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR, int) {
  using namespace clipvault::otp;
  DWORD session_id = 0;
  ProcessIdToSessionId(GetCurrentProcessId(), &session_id);
  const std::wstring mutex_name =
      L"Local\\ClipVaultOtpBrokerV1-" + std::to_wstring(session_id);
  HANDLE mutex = CreateMutexW(nullptr, TRUE, mutex_name.c_str());
  if (mutex == nullptr || GetLastError() == ERROR_ALREADY_EXISTS) {
    if (mutex != nullptr) CloseHandle(mutex);
    return 2;
  }

  SetConsoleCtrlHandler(StopHandler, TRUE);
  authority::PairCredentialAuthority credentials;
  broker::OtpBrokerService service(&credentials);
  broker::ProductionBrokerClientAuthorizer authorizer;
  broker::NonActivatingOtpPrompt prompt;
  if (!service.ready() || !prompt.Start()) {
    ReleaseMutex(mutex);
    CloseHandle(mutex);
    return 3;
  }
  broker::BrokerPipeServer server(&service, &authorizer, &prompt);
  while (!g_stop.load()) {
    service.ExpireDue(GetTickCount64());
    server.ServeOne(500, broker::kBrokerForwardBudgetMilliseconds);
  }
  prompt.Stop();
  service.Clear();
  ReleaseMutex(mutex);
  CloseHandle(mutex);
  return 0;
}
