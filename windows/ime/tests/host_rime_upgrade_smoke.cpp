#include "protocol.h"

#include <windows.h>

#include <cwctype>
#include <filesystem>
#include <string>
#include <system_error>

namespace {

std::wstring Quote(const std::wstring& value) { return L"\"" + value + L"\""; }

bool LaunchAndWait(const wchar_t* executable, std::wstring command,
                   DWORD timeout, DWORD* exit_code) {
  STARTUPINFOW startup{sizeof(STARTUPINFOW)};
  PROCESS_INFORMATION process{};
  if (!CreateProcessW(executable, command.data(), nullptr, nullptr, FALSE,
                      CREATE_NO_WINDOW, nullptr, nullptr, &startup, &process)) {
    return false;
  }
  const DWORD wait = WaitForSingleObject(process.hProcess, timeout);
  if (wait == WAIT_TIMEOUT) {
    TerminateProcess(process.hProcess, 5);
    WaitForSingleObject(process.hProcess, 2000);
  }
  GetExitCodeProcess(process.hProcess, exit_code);
  CloseHandle(process.hThread);
  CloseHandle(process.hProcess);
  return wait == WAIT_OBJECT_0;
}

bool SendText(clipvault::ime::PipeEngineClient* client,
              const std::wstring& text, clipvault::ime::EngineState* state) {
  for (const wchar_t value : text) {
    clipvault::ime::KeyEvent event;
    event.virtual_key = static_cast<std::uint32_t>(std::towupper(value));
    event.text.assign(1, value);
    if (!client->ProcessKey(event, state, 200)) return false;
  }
  return true;
}

}  // namespace

int wmain(int argc, wchar_t** argv) {
  if (argc != 2) return 2;
  std::error_code error;
  const auto user = std::filesystem::temp_directory_path(error) /
                    (L"ClipVaultRimeUpgrade-" +
                     std::to_wstring(GetCurrentProcessId()) + L"-" +
                     std::to_wstring(GetTickCount64()));
  std::filesystem::create_directories(user, error);
  if (error || !SetEnvironmentVariableW(L"CLIPVAULT_RIME_USER_DIR",
                                         user.c_str())) {
    return 3;
  }
  const std::wstring test_namespace =
      L"upgrade-" + std::to_wstring(GetCurrentProcessId()) + L"-" +
      std::to_wstring(GetTickCount64());
  if (!SetEnvironmentVariableW(L"CLIPVAULT_IME_TEST_NAMESPACE",
                               test_namespace.c_str()) ||
      !SetEnvironmentVariableW(L"CLIPVAULT_IME_TEST_RIME_INIT_DELAY_MS",
                               L"750")) {
    return 3;
  }

  DWORD deploy_exit = 99;
  if (!LaunchAndWait(argv[1], Quote(argv[1]) + L" --deploy-rime", 60000,
                     &deploy_exit) ||
      deploy_exit != 0) {
    return 4;
  }

  std::wstring command = Quote(argv[1]) + L" --once";
  STARTUPINFOW startup{sizeof(STARTUPINFOW)};
  PROCESS_INFORMATION process{};
  if (!CreateProcessW(argv[1], command.data(), nullptr, nullptr, FALSE,
                      CREATE_NO_WINDOW, nullptr, nullptr, &startup, &process)) {
    return 4;
  }

  clipvault::ime::PipeEngineClient client;
  clipvault::ime::EngineState state;
  bool ok = client.Connect(500) && client.StartSession(&state, 200) &&
            SendText(&client, L"ni", &state) && state.preedit == L"ni" &&
            state.composition_active;
  Sleep(2500);
  ok = ok && SendText(&client, L"hao", &state) &&
       state.composition_active && !state.candidates.empty();
  bool has_nihao = false;
  for (const auto& candidate : state.candidates) {
    if (candidate.text == L"你好") {
      has_nihao = true;
      break;
    }
  }
  ok = ok && has_nihao;
  client.Disconnect();

  const DWORD wait = WaitForSingleObject(process.hProcess, 10000);
  if (wait == WAIT_TIMEOUT) {
    TerminateProcess(process.hProcess, 5);
    WaitForSingleObject(process.hProcess, 2000);
  }
  DWORD host_exit = 99;
  GetExitCodeProcess(process.hProcess, &host_exit);
  CloseHandle(process.hThread);
  CloseHandle(process.hProcess);
  std::filesystem::remove_all(user, error);
  return ok && host_exit == 0 ? 0 : 1;
}
