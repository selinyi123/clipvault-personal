#include "protocol.h"

#include <windows.h>

#include <cwctype>
#include <string>

namespace {

std::wstring Quote(const std::wstring& value) { return L"\"" + value + L"\""; }

}  // namespace

int wmain(int argc, wchar_t** argv) {
  if (argc < 2 || argc > 3) return 2;
  const bool expect_pass_through =
      argc == 3 && std::wstring(argv[2]) == L"--expect-pass-through";
  const std::wstring test_namespace =
      L"ready-" + std::to_wstring(GetCurrentProcessId()) + L"-" +
      std::to_wstring(GetTickCount64());
  if (!SetEnvironmentVariableW(L"CLIPVAULT_IME_TEST_NAMESPACE",
                               test_namespace.c_str()) ||
      !SetEnvironmentVariableW(L"CLIPVAULT_INSECURE_TEST_PIPE_TRUST", L"1") ||
      !SetEnvironmentVariableW(L"CLIPVAULT_IME_TEST_RIME_INIT_DELAY_MS",
                               L"1000")) {
    return 3;
  }

  std::wstring command = Quote(argv[1]) + L" --once";
  STARTUPINFOW startup{sizeof(STARTUPINFOW)};
  PROCESS_INFORMATION process{};
  if (!CreateProcessW(argv[1], command.data(), nullptr, nullptr, FALSE,
                      CREATE_NO_WINDOW, nullptr, nullptr, &startup, &process)) {
    return 4;
  }

  clipvault::ime::PipeEngineClient client;
  const ULONGLONG started = GetTickCount64();
  clipvault::ime::EngineState state;
  bool ok = client.Connect(300) && client.StartSession(&state, 100);
  const ULONGLONG ready_elapsed = GetTickCount64() - started;
  clipvault::ime::KeyEvent key;
  key.virtual_key = 'A';
  key.text = L"a";
  ok = ok && ready_elapsed < 500 && client.ProcessKey(key, &state, 100);
  ok = ok && (expect_pass_through
                  ? !state.handled && state.preedit.empty() &&
                        !state.composition_active
                  : state.handled && state.preedit == L"a" &&
                        state.composition_active);
  client.Disconnect();

  const DWORD wait = WaitForSingleObject(process.hProcess, 5000);
  if (wait == WAIT_TIMEOUT) {
    TerminateProcess(process.hProcess, 5);
    WaitForSingleObject(process.hProcess, 2000);
  }
  DWORD exit_code = 99;
  GetExitCodeProcess(process.hProcess, &exit_code);
  CloseHandle(process.hThread);
  CloseHandle(process.hProcess);
  return ok && exit_code == 0 ? 0 : 1;
}
