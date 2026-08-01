#include "runtime_snapshot.h"

#include <windows.h>

#include <array>
#include <atomic>
#include <iostream>
#include <string>
#include <thread>

namespace {

using namespace clipvault::ime;

constexpr char kEpoch[] = "01234567-89ab-4def-8abc-0123456789ab";

bool Expect(bool condition, const char* label) {
  if (!condition) std::cerr << "FAILED: " << label << '\n';
  return condition;
}

std::wstring CurrentExecutable() {
  std::array<wchar_t, 32768> path{};
  const DWORD length = GetModuleFileNameW(nullptr, path.data(),
                                          static_cast<DWORD>(path.size()));
  return length == 0 || length >= path.size()
             ? std::wstring{}
             : std::wstring(path.data(), length);
}

std::wstring NewPipeName(const wchar_t* label) {
  return L"\\\\.\\pipe\\ClipVaultRuntimeSnapshotV1-test-" +
         std::to_wstring(GetCurrentProcessId()) + L"-" +
         std::to_wstring(GetTickCount64()) + L"-" + label;
}

HANDLE CreateServer(const std::wstring& name) {
  return CreateNamedPipeW(
      name.c_str(), PIPE_ACCESS_DUPLEX,
      PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT |
          PIPE_REJECT_REMOTE_CLIENTS,
      1, kRuntimeSnapshotMaximumFrameBytes + 4,
      kRuntimeSnapshotMaximumFrameBytes + 4, 0, nullptr);
}

bool ConnectServer(HANDLE pipe) {
  return ConnectNamedPipe(pipe, nullptr) != FALSE ||
         GetLastError() == ERROR_PIPE_CONNECTED;
}

bool ReadExact(HANDLE pipe, std::uint8_t* output, DWORD size) {
  DWORD offset = 0;
  while (offset < size) {
    DWORD read = 0;
    if (!ReadFile(pipe, output + offset, size - offset, &read, nullptr) ||
        read == 0) {
      return false;
    }
    offset += read;
  }
  return true;
}

bool WriteExact(HANDLE pipe, const std::uint8_t* input, DWORD size) {
  DWORD offset = 0;
  while (offset < size) {
    DWORD written = 0;
    if (!WriteFile(pipe, input + offset, size - offset, &written, nullptr) ||
        written == 0) {
      return false;
    }
    offset += written;
  }
  return true;
}

bool ReadFrame(HANDLE pipe, std::vector<std::uint8_t>* payload) {
  std::array<std::uint8_t, 4> prefix{};
  if (!ReadExact(pipe, prefix.data(), static_cast<DWORD>(prefix.size())))
    return false;
  const std::uint32_t size = (static_cast<std::uint32_t>(prefix[0]) << 24) |
                             (static_cast<std::uint32_t>(prefix[1]) << 16) |
                             (static_cast<std::uint32_t>(prefix[2]) << 8) |
                             static_cast<std::uint32_t>(prefix[3]);
  if (size == 0 || size > kRuntimeSnapshotMaximumFrameBytes) return false;
  payload->resize(size);
  return ReadExact(pipe, payload->data(), size);
}

bool WriteFrame(HANDLE pipe, const std::vector<std::uint8_t>& payload) {
  if (payload.empty() || payload.size() > kRuntimeSnapshotMaximumFrameBytes)
    return false;
  const std::uint32_t size = static_cast<std::uint32_t>(payload.size());
  const std::array<std::uint8_t, 4> prefix{
      static_cast<std::uint8_t>(size >> 24),
      static_cast<std::uint8_t>(size >> 16),
      static_cast<std::uint8_t>(size >> 8),
      static_cast<std::uint8_t>(size)};
  return WriteExact(pipe, prefix.data(), static_cast<DWORD>(prefix.size())) &&
         WriteExact(pipe, payload.data(), static_cast<DWORD>(payload.size()));
}

void CloseServer(HANDLE pipe) {
  if (pipe == INVALID_HANDLE_VALUE) return;
  FlushFileBuffers(pipe);
  DisconnectNamedPipe(pipe);
  CloseHandle(pipe);
}

bool RunSuccessfulExchange() {
  const std::wstring name = NewPipeName(L"success");
  std::atomic_bool ready{false};
  std::atomic_bool server_ok{false};
  std::atomic_int server_stage{0};
  std::thread server([&] {
    HANDLE pipe = CreateServer(name);
    if (pipe == INVALID_HANDLE_VALUE) return;
    ready.store(true);
    std::vector<std::uint8_t> payload;
    std::string client;
    std::uint64_t request_id = 0;
    std::uint32_t limit = 0;
    bool ok = ConnectServer(pipe);
    if (ok) server_stage.store(1);
    ok = ok && ReadFrame(pipe, &payload);
    if (ok) server_stage.store(2);
    ok = ok && DecodeRuntimeSnapshotClientHello(payload, &client);
    if (ok) server_stage.store(3);
    ok = ok && WriteFrame(pipe, EncodeRuntimeSnapshotHostHello(kEpoch));
    if (ok) server_stage.store(4);
    ok = ok && ReadFrame(pipe, &payload);
    if (ok) server_stage.store(5);
    ok = ok && DecodeRuntimeSnapshotRequest(payload, &request_id, &limit) &&
         limit == kRuntimeSnapshotMaximumItems;
    if (ok) server_stage.store(6);
    if (ok) {
      RuntimeSnapshotResponse response;
      response.request_id = request_id;
      response.surface.publisher_epoch = kEpoch;
      response.surface.generation = 9;
      response.surface.expires_at_ms = UnixTimeMilliseconds() + 5000;
      response.surface.candidates.push_back(
          {"memory:home", 1, L"Home", L"123 Example Street"});
      ok = WriteFrame(pipe, EncodeRuntimeSnapshotResponse(response));
      if (ok) server_stage.store(7);
    }
    server_ok.store(ok);
    CloseServer(pipe);
  });
  const ULONGLONG ready_deadline = GetTickCount64() + 1000;
  while (!ready.load() && GetTickCount64() < ready_deadline) Sleep(1);
  RuntimeSnapshotPipeClient client(
      {name, CurrentExecutable(), false});
  RuntimeSnapshotResponse response;
  const bool fetched =
      ready.load() && client.Fetch(42, 8, UnixTimeMilliseconds(), &response);
  server.join();
  const bool ok = fetched && server_ok.load() && response.request_id == 42 &&
         response.surface.generation == 9 &&
         response.surface.candidates.size() == 1 &&
         response.surface.candidates.front().text == L"123 Example Street";
  if (!ok) {
    std::cerr << "snapshot exchange diagnostic: fetched=" << fetched
              << ", server_ok=" << server_ok.load()
              << ", server_stage=" << server_stage.load()
              << ", response_id=" << response.request_id << '\n';
  }
  return ok;
}

bool RunTimeout() {
  const std::wstring name = NewPipeName(L"timeout");
  std::atomic_bool ready{false};
  std::thread server([&] {
    HANDLE pipe = CreateServer(name);
    if (pipe == INVALID_HANDLE_VALUE) return;
    ready.store(true);
    if (ConnectServer(pipe)) Sleep(400);
    CloseServer(pipe);
  });
  const ULONGLONG ready_deadline = GetTickCount64() + 1000;
  while (!ready.load() && GetTickCount64() < ready_deadline) Sleep(1);
  RuntimeSnapshotPipeClient client(
      {name, CurrentExecutable(), false});
  RuntimeSnapshotResponse response;
  const ULONGLONG started = GetTickCount64();
  const bool fetched =
      ready.load() && client.Fetch(43, 8, UnixTimeMilliseconds(), &response);
  const ULONGLONG elapsed = GetTickCount64() - started;
  server.join();
  return !fetched && elapsed <= 350;
}

bool RunWrongServerPath() {
  const std::wstring name = NewPipeName(L"wrong-path");
  std::atomic_bool ready{false};
  std::thread server([&] {
    HANDLE pipe = CreateServer(name);
    if (pipe == INVALID_HANDLE_VALUE) return;
    ready.store(true);
    if (ConnectServer(pipe)) Sleep(20);
    CloseServer(pipe);
  });
  const ULONGLONG ready_deadline = GetTickCount64() + 1000;
  while (!ready.load() && GetTickCount64() < ready_deadline) Sleep(1);
  RuntimeSnapshotPipeClient client(
      {name, CurrentExecutable() + L".not-the-runtime", false});
  RuntimeSnapshotResponse response;
  const bool fetched =
      ready.load() && client.Fetch(44, 8, UnixTimeMilliseconds(), &response);
  server.join();
  return !fetched;
}

bool ProductionNameIgnoresEnvironment() {
  const std::wstring before = RuntimeSnapshotPipeNameForCurrentSession();
  SetEnvironmentVariableW(L"CLIPVAULT_RUNTIME_SNAPSHOT_PIPE",
                          L"attacker-controlled");
  const std::wstring after = RuntimeSnapshotPipeNameForCurrentSession();
  SetEnvironmentVariableW(L"CLIPVAULT_RUNTIME_SNAPSHOT_PIPE", nullptr);
  return !before.empty() && before == after &&
         before.find(L"ClipVaultRuntimeSnapshotV1-") != std::wstring::npos;
}

}  // namespace

int wmain() {
  return Expect(RunSuccessfulExchange(), "real Named Pipe exchange") &&
                 Expect(RunTimeout(), "absolute 250 ms pipe deadline") &&
                 Expect(RunWrongServerPath(), "unexpected server path rejected") &&
                 Expect(ProductionNameIgnoresEnvironment(),
                        "production pipe name has no environment override")
             ? 0
             : 1;
}
