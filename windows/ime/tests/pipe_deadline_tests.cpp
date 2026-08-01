#include "protocol.h"
#include "recovery_policy.h"

#include <windows.h>

#include <array>
#include <atomic>
#include <cstdint>
#include <cwctype>
#include <string>
#include <thread>
#include <vector>

namespace {

enum class Behavior { kAcceptNoReply, kHalfFrame };

struct EnvironmentGuard final {
  EnvironmentGuard() {
    const DWORD required =
        GetEnvironmentVariableW(L"CLIPVAULT_IME_TEST_NAMESPACE", nullptr, 0);
    if (required != 0) {
      prior.resize(required, L'\0');
      const DWORD written = GetEnvironmentVariableW(
          L"CLIPVAULT_IME_TEST_NAMESPACE", prior.data(), required);
      if (written < required) prior.resize(written);
    }
  }
  ~EnvironmentGuard() {
    SetEnvironmentVariableW(L"CLIPVAULT_IME_TEST_NAMESPACE",
                            prior.empty() ? nullptr : prior.c_str());
  }
  std::wstring prior;
};

HANDLE CreateTestPipe() {
  return CreateNamedPipeW(
      clipvault::ime::PipeNameForCurrentSession().c_str(), PIPE_ACCESS_DUPLEX,
      PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
      1, 1024, 1024, 0, nullptr);
}

bool ReadResponseAck(HANDLE pipe, const std::string& session_id,
                     std::uint64_t expected_sequence) {
  std::vector<std::uint8_t> frame;
  clipvault::ime::ResponseAck acknowledgement;
  return clipvault::ime::ReadFrame(pipe, &frame) &&
         clipvault::ime::DecodeResponseAck(frame, &acknowledgement) &&
         acknowledgement.host_instance_id == "deadline-host" &&
         acknowledgement.session_id == session_id &&
         acknowledgement.ack_request_seq == expected_sequence;
}

bool RunHandshakeTimeout(Behavior behavior) {
  const std::wstring suffix =
      L"deadline-" + std::to_wstring(GetCurrentProcessId()) + L"-" +
      std::to_wstring(GetTickCount64()) +
      (behavior == Behavior::kHalfFrame ? L"-half" : L"-silent");
  if (!SetEnvironmentVariableW(L"CLIPVAULT_IME_TEST_NAMESPACE", suffix.c_str()))
    return false;

  std::atomic_bool ready{false};
  std::thread server([&] {
    HANDLE pipe = CreateTestPipe();
    if (pipe == INVALID_HANDLE_VALUE) return;
    ready.store(true, std::memory_order_release);
    const bool connected = ConnectNamedPipe(pipe, nullptr) != FALSE ||
                           GetLastError() == ERROR_PIPE_CONNECTED;
    if (connected && behavior == Behavior::kHalfFrame) {
      std::vector<std::uint8_t> hello;
      if (clipvault::ime::ReadFrame(pipe, &hello)) {
        const std::array<std::uint8_t, 7> partial{0, 0, 0, 16, 1, 2, 3};
        DWORD written = 0;
        WriteFile(pipe, partial.data(), static_cast<DWORD>(partial.size()),
                  &written, nullptr);
      }
    }
    Sleep(250);
    DisconnectNamedPipe(pipe);
    CloseHandle(pipe);
  });

  const ULONGLONG ready_deadline = GetTickCount64() + 1000;
  while (!ready.load(std::memory_order_acquire) &&
         GetTickCount64() < ready_deadline) {
    Sleep(1);
  }
  clipvault::ime::PipeEngineClient client;
  const ULONGLONG started = GetTickCount64();
  const bool connected = client.Connect(50);
  const ULONGLONG elapsed = GetTickCount64() - started;
  client.Disconnect();
  server.join();
  return !connected && elapsed <= 200;
}

bool RunWriteNotReadTimeout() {
  const std::wstring suffix =
      L"deadline-" + std::to_wstring(GetCurrentProcessId()) + L"-" +
      std::to_wstring(GetTickCount64()) + L"-blocked-write";
  if (!SetEnvironmentVariableW(L"CLIPVAULT_IME_TEST_NAMESPACE", suffix.c_str()))
    return false;

  std::atomic_bool ready{false};
  std::thread server([&] {
    HANDLE pipe = CreateTestPipe();
    if (pipe == INVALID_HANDLE_VALUE) return;
    ready.store(true, std::memory_order_release);
    const bool connected = ConnectNamedPipe(pipe, nullptr) != FALSE ||
                           GetLastError() == ERROR_PIPE_CONNECTED;
    if (connected) Sleep(250);
    DisconnectNamedPipe(pipe);
    CloseHandle(pipe);
  });
  const ULONGLONG ready_deadline = GetTickCount64() + 1000;
  while (!ready.load(std::memory_order_acquire) &&
         GetTickCount64() < ready_deadline) {
    Sleep(1);
  }
  HANDLE client = CreateFileW(clipvault::ime::PipeNameForCurrentSession().c_str(),
                              GENERIC_READ | GENERIC_WRITE, 0, nullptr,
                              OPEN_EXISTING,
                              FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OVERLAPPED,
                              nullptr);
  if (client == INVALID_HANDLE_VALUE) {
    server.join();
    return false;
  }
  std::vector<std::uint8_t> payload(clipvault::ime::kMaximumFrameBytes, 0x5a);
  const ULONGLONG started = GetTickCount64();
  const bool written = clipvault::ime::WriteFrameUntil(
      client, payload, started + 50);
  const ULONGLONG elapsed = GetTickCount64() - started;
  CancelIoEx(client, nullptr);
  CloseHandle(client);
  server.join();
  return !written && elapsed <= 200;
}

bool RunMidSessionHalfFrameRecovery() {
  const std::wstring suffix =
      L"deadline-" + std::to_wstring(GetCurrentProcessId()) + L"-" +
      std::to_wstring(GetTickCount64()) + L"-mid-session";
  if (!SetEnvironmentVariableW(L"CLIPVAULT_IME_TEST_NAMESPACE", suffix.c_str()))
    return false;

  std::atomic_bool ready{false};
  std::thread server([&] {
    HANDLE pipe = CreateTestPipe();
    if (pipe == INVALID_HANDLE_VALUE) return;
    ready.store(true, std::memory_order_release);
    const bool connected = ConnectNamedPipe(pipe, nullptr) != FALSE ||
                           GetLastError() == ERROR_PIPE_CONNECTED;
    std::vector<std::uint8_t> frame;
    std::string client_id;
    if (!connected || !clipvault::ime::ReadFrame(pipe, &frame) ||
        !clipvault::ime::DecodeClientHello(frame, &client_id) ||
        !clipvault::ime::WriteFrame(
            pipe, clipvault::ime::EncodeHostHello("deadline-host"))) {
      CloseHandle(pipe);
      return;
    }
    clipvault::ime::StartSessionRequest start;
    if (!clipvault::ime::ReadFrame(pipe, &frame) ||
        !clipvault::ime::DecodeStartSession(frame, &start)) {
      CloseHandle(pipe);
      return;
    }
    clipvault::ime::EngineState state;
    state.host_instance_id = "deadline-host";
    state.session_id = start.session_id;
    state.ack_request_seq = start.request_seq;
    if (!clipvault::ime::WriteFrame(pipe,
                                    clipvault::ime::EncodeEngineState(state))) {
      CloseHandle(pipe);
      return;
    }
    if (!ReadResponseAck(pipe, start.session_id, start.request_seq)) {
      CloseHandle(pipe);
      return;
    }
    for (const wchar_t expected : std::wstring(L"ni")) {
      clipvault::ime::ProcessKeyRequest request;
      if (!clipvault::ime::ReadFrame(pipe, &frame) ||
          !clipvault::ime::DecodeProcessKey(frame, &request) ||
          request.event.text != std::wstring(1, expected)) {
        CloseHandle(pipe);
        return;
      }
      state.ack_request_seq = request.request_seq;
      state.revision += 1;
      state.handled = true;
      state.preedit.push_back(expected);
      state.caret_utf16 = static_cast<std::uint32_t>(state.preedit.size());
      state.composition_active = true;
      if (!clipvault::ime::WriteFrame(pipe,
                                      clipvault::ime::EncodeEngineState(state))) {
        CloseHandle(pipe);
        return;
      }
      if (!ReadResponseAck(pipe, start.session_id, request.request_seq)) {
        CloseHandle(pipe);
        return;
      }
    }
    clipvault::ime::ProcessKeyRequest current;
    if (clipvault::ime::ReadFrame(pipe, &frame) &&
        clipvault::ime::DecodeProcessKey(frame, &current)) {
      const std::array<std::uint8_t, 7> partial{0, 0, 0, 24, 1, 2, 3};
      DWORD written = 0;
      WriteFile(pipe, partial.data(), static_cast<DWORD>(partial.size()),
                &written, nullptr);
      Sleep(250);
    }
    DisconnectNamedPipe(pipe);
    CloseHandle(pipe);
  });

  const ULONGLONG ready_deadline = GetTickCount64() + 1000;
  while (!ready.load(std::memory_order_acquire) &&
         GetTickCount64() < ready_deadline) {
    Sleep(1);
  }
  clipvault::ime::PipeEngineClient client;
  clipvault::ime::EngineState last_state;
  bool ok = client.Connect(100) && client.StartSession(&last_state, 100);
  for (const wchar_t value : std::wstring(L"ni")) {
    clipvault::ime::KeyEvent event;
    event.virtual_key = static_cast<std::uint32_t>(towupper(value));
    event.text.assign(1, value);
    ok = ok && client.ProcessKey(event, &last_state, 100);
  }
  clipvault::ime::KeyEvent current;
  current.virtual_key = 'H';
  current.text = L"h";
  const ULONGLONG started = GetTickCount64();
  const bool received = ok && client.ProcessKey(current, &last_state, 50);
  const ULONGLONG elapsed = GetTickCount64() - started;
  const auto recovery = clipvault::ime::PlanRpcRecovery(
      last_state.preedit == L"ni", true);
  const auto candidate_recovery =
      clipvault::ime::PlanRpcRecovery(true, false);
  client.Disconnect();
  server.join();
  return !received && elapsed <= 200 && recovery.preserve_preedit_as_literal &&
         recovery.replay_plain_letter && !recovery.consume_original_key &&
         candidate_recovery.preserve_preedit_as_literal &&
         !candidate_recovery.replay_plain_letter &&
         candidate_recovery.consume_original_key;
}

}  // namespace

int wmain() {
  EnvironmentGuard environment;
  return RunHandshakeTimeout(Behavior::kAcceptNoReply) &&
                 RunHandshakeTimeout(Behavior::kHalfFrame) &&
                 RunWriteNotReadTimeout() && RunMidSessionHalfFrameRecovery()
             ? 0
             : 1;
}
