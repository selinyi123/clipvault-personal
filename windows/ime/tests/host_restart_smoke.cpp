#include "protocol.h"

#include <windows.h>

#include <cwctype>
#include <iostream>
#include <string>

namespace {

struct ChildProcess final {
  HANDLE process = nullptr;
  HANDLE thread = nullptr;

  ~ChildProcess() { Stop(); }
  ChildProcess(const ChildProcess&) = delete;
  ChildProcess& operator=(const ChildProcess&) = delete;
  ChildProcess() = default;

  bool Start(const std::wstring& executable) {
    std::wstring command = L"\"" + executable + L"\" --echo";
    STARTUPINFOW startup{sizeof(STARTUPINFOW)};
    PROCESS_INFORMATION created{};
    if (!CreateProcessW(executable.c_str(), command.data(), nullptr, nullptr,
                        FALSE, CREATE_NO_WINDOW, nullptr, nullptr, &startup,
                        &created)) return false;
    process = created.hProcess;
    thread = created.hThread;
    return true;
  }

  void Stop() noexcept {
    if (process != nullptr) {
      TerminateProcess(process, 0);
      WaitForSingleObject(process, 3000);
      CloseHandle(process);
      process = nullptr;
    }
    if (thread != nullptr) {
      CloseHandle(thread);
      thread = nullptr;
    }
  }
};

bool SendText(clipvault::ime::PipeEngineClient* client, const std::wstring& text,
              clipvault::ime::EngineState* state) {
  for (const wchar_t value : text) {
    clipvault::ime::KeyEvent event;
    event.virtual_key = static_cast<std::uint32_t>(towupper(value));
    event.text.assign(1, value);
    if (!client->ProcessKey(event, state)) return false;
  }
  return true;
}

}  // namespace

int wmain(int argc, wchar_t** argv) {
  if (argc != 2) return 2;
  const std::wstring test_namespace =
      L"restart-" + std::to_wstring(GetCurrentProcessId()) + L"-" +
      std::to_wstring(GetTickCount64());
  if (!SetEnvironmentVariableW(L"CLIPVAULT_IME_TEST_NAMESPACE",
                               test_namespace.c_str()) ||
      !SetEnvironmentVariableW(L"CLIPVAULT_INSECURE_TEST_PIPE_TRUST", L"1"))
    return 3;
  bool ok = true;
  int stage = 0;
  ChildProcess first;
  ok = first.Start(argv[1]);
  clipvault::ime::PipeEngineClient client;
  ok = ok && client.Connect(5000);
  clipvault::ime::EngineState state;
  ok = ok && client.StartSession(&state) && SendText(&client, L"stale", &state) &&
       state.composition_active;
  if (ok) stage = 1;

  first.Stop();
  clipvault::ime::KeyEvent ambiguous;
  ambiguous.virtual_key = 'X';
  ambiguous.text = L"x";
  ok = ok && !client.ProcessKey(ambiguous, &state) && !client.connected();
  if (ok) stage = 2;

  ChildProcess second;
  ok = ok && second.Start(argv[1]) && client.Connect(5000) &&
       client.StartSession(&state) && state.revision == 0 &&
       SendText(&client, L"fresh", &state) && client.CommitComposition(&state) &&
       state.commit_text == L"fresh" && !state.composition_active;
  if (ok) stage = 3;
  client.Disconnect();
  second.Stop();

  if (!ok) std::cerr << "Host restart recovery smoke failed at stage " << stage << '\n';
  return ok ? 0 : 1;
}
