#include "protocol.h"

#include <windows.h>

#include <filesystem>
#include <iostream>
#include <string>
#include <system_error>

namespace {

constexpr int kCleanFirstRunIterations = 8;

std::wstring Quote(const std::wstring& value) { return L"\"" + value + L"\""; }

bool LaunchAndWait(const std::wstring& executable,
                   const std::wstring& command, DWORD timeout,
                   DWORD* exit_code) {
  STARTUPINFOW startup{sizeof(STARTUPINFOW)};
  PROCESS_INFORMATION process{};
  std::wstring mutable_command = command;
  if (!CreateProcessW(executable.c_str(), mutable_command.data(),
                      nullptr, nullptr, FALSE, CREATE_NO_WINDOW, nullptr,
                      nullptr, &startup, &process)) {
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

bool StartAndExercise(const std::wstring& executable, int* stage) {
  std::wstring command = Quote(executable) + L" --once --require-rime";
  STARTUPINFOW startup{sizeof(STARTUPINFOW)};
  PROCESS_INFORMATION process{};
  if (!CreateProcessW(executable.c_str(), command.data(), nullptr, nullptr,
                      FALSE, CREATE_NO_WINDOW, nullptr, nullptr, &startup,
                      &process)) {
    return false;
  }

  clipvault::ime::PipeEngineClient client;
  clipvault::ime::EngineState state;
  bool ok = client.Connect(30'000);
  if (ok) *stage = 1;
  clipvault::ime::InputContext private_context;
  private_context.field_kind = clipvault::ime::InputFieldKind::kText;
  private_context.incognito = true;
  private_context.learning_allowed = false;
  private_context.clipvault_allowed = false;
  ok = ok && client.StartSession(private_context, &state);
  if (ok) *stage = 2;
  clipvault::ime::KeyEvent n;
  n.virtual_key = 'N';
  n.text = L"n";
  clipvault::ime::KeyEvent i;
  i.virtual_key = 'I';
  i.text = L"i";
  ok = ok && client.ProcessKey(n, &state) &&
       client.ProcessKey(i, &state) && state.composition_active &&
       !state.candidates.empty() && client.CancelComposition(&state);
  if (ok) *stage = 3;
  ok = ok && client.StartSession(&state);
  if (ok) *stage = 4;
  ok = ok && client.ProcessKey(n, &state) &&
       client.ProcessKey(i, &state) && state.composition_active &&
       !state.candidates.empty();
  if (ok) *stage = 5;
  client.Disconnect();

  const DWORD wait = WaitForSingleObject(process.hProcess, 10'000);
  if (wait == WAIT_TIMEOUT) {
    TerminateProcess(process.hProcess, 5);
    WaitForSingleObject(process.hProcess, 2000);
  }
  DWORD exit_code = 99;
  GetExitCodeProcess(process.hProcess, &exit_code);
  CloseHandle(process.hThread);
  CloseHandle(process.hProcess);
  return ok && wait == WAIT_OBJECT_0 && exit_code == 0;
}

}  // namespace

int wmain(int argc, wchar_t** argv) {
  if (argc != 2) return 2;
  std::error_code error;
  const auto root = std::filesystem::temp_directory_path(error) /
                    (L"ClipVaultRimeFirstRun-" +
                     std::to_wstring(GetCurrentProcessId()) + L"-" +
                     std::to_wstring(GetTickCount64()));
  std::filesystem::create_directories(root, error);
  if (error) return 3;

  bool ok = true;
  for (int iteration = 0; iteration < kCleanFirstRunIterations && ok;
       ++iteration) {
    const auto user = root / std::to_wstring(iteration);
    std::filesystem::create_directories(user, error);
    const std::wstring test_namespace =
        L"first-run-" + std::to_wstring(GetCurrentProcessId()) + L"-" +
        std::to_wstring(iteration);
    ok = !error &&
         SetEnvironmentVariableW(L"CLIPVAULT_RIME_USER_DIR",
                                 user.c_str()) != FALSE &&
         SetEnvironmentVariableW(L"CLIPVAULT_IME_TEST_NAMESPACE",
                                 test_namespace.c_str()) != FALSE;
    DWORD deploy_exit = 99;
    ok = ok && LaunchAndWait(argv[1], Quote(argv[1]) + L" --deploy-rime",
                             60'000, &deploy_exit) &&
         deploy_exit == 0;
    int stage = 0;
    ok = ok && StartAndExercise(argv[1], &stage);
    if (!ok) {
      std::cerr << "Rime clean first-run stability failed at iteration "
                << iteration << ", stage " << stage << '\n';
    }
  }
  std::filesystem::remove_all(root, error);
  return ok ? 0 : 1;
}
