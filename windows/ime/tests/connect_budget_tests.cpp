#include "protocol.h"
#include "key_translation.h"

#include <windows.h>

#include <iostream>
#include <string>

int main() {
  if (clipvault::ime::LatinUppercase(false, false) ||
      !clipvault::ime::LatinUppercase(true, false) ||
      !clipvault::ime::LatinUppercase(false, true) ||
      clipvault::ime::LatinUppercase(true, true)) {
    std::cerr << "Shift/CapsLock XOR mapping drifted\n";
    return 3;
  }
  const std::wstring test_namespace =
      L"budget-" + std::to_wstring(GetCurrentProcessId()) + L"-" +
      std::to_wstring(GetTickCount64());
  if (!SetEnvironmentVariableW(L"CLIPVAULT_IME_TEST_NAMESPACE",
                               test_namespace.c_str())) return 2;
  clipvault::ime::PipeEngineClient client;
  const ULONGLONG started = GetTickCount64();
  const bool connected = client.Connect(30);
  const ULONGLONG elapsed = GetTickCount64() - started;
  if (connected || elapsed > 250) {
    std::cerr << "cold Host connection budget violated: connected=" << connected
              << ", elapsed_ms=" << elapsed << '\n';
    return 1;
  }
  return 0;
}
