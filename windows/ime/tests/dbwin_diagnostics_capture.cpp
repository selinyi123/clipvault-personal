#include <windows.h>

#include <array>
#include <cstdio>
#include <string_view>

namespace {

constexpr DWORD kBufferSize = 4096;
constexpr std::string_view kDiagnosticPrefix = "ClipVaultIme event=";

struct DbwinBuffer {
  DWORD process_id;
  std::array<char, kBufferSize - sizeof(DWORD)> data;
};

void CloseIfValid(HANDLE handle) noexcept {
  if (handle != nullptr) CloseHandle(handle);
}

void PrintMatchingLines(const DbwinBuffer& buffer) {
  const std::size_t length =
      strnlen_s(buffer.data.data(), buffer.data.size());
  const std::string_view message(buffer.data.data(), length);

  std::size_t line_start = 0;
  while (line_start < message.size()) {
    std::size_t line_end = message.find('\n', line_start);
    if (line_end == std::string_view::npos) line_end = message.size();

    std::string_view line = message.substr(line_start, line_end - line_start);
    if (!line.empty() && line.back() == '\r') line.remove_suffix(1);
    if (line.starts_with(kDiagnosticPrefix)) {
      std::printf("pid=%lu %.*s\n",
                  static_cast<unsigned long>(buffer.process_id),
                  static_cast<int>(line.size()), line.data());
      std::fflush(stdout);
    }

    if (line_end == message.size()) break;
    line_start = line_end + 1;
  }
}

}  // namespace

int main() {
  SetLastError(ERROR_SUCCESS);
  HANDLE buffer_ready =
      CreateEventW(nullptr, FALSE, FALSE, L"DBWIN_BUFFER_READY");
  const DWORD buffer_ready_error = GetLastError();
  if (buffer_ready == nullptr) {
    std::fprintf(stderr, "failed to create DBWIN_BUFFER_READY: %lu\n",
                 static_cast<unsigned long>(buffer_ready_error));
    return 1;
  }
  if (buffer_ready_error == ERROR_ALREADY_EXISTS) {
    std::fprintf(stderr, "another DBWIN capture session is already active\n");
    CloseIfValid(buffer_ready);
    return 2;
  }

  SetLastError(ERROR_SUCCESS);
  HANDLE data_ready = CreateEventW(nullptr, FALSE, FALSE, L"DBWIN_DATA_READY");
  const DWORD data_ready_error = GetLastError();
  if (data_ready == nullptr || data_ready_error == ERROR_ALREADY_EXISTS) {
    std::fprintf(stderr, "failed to own DBWIN_DATA_READY: %lu\n",
                 static_cast<unsigned long>(data_ready_error));
    CloseIfValid(data_ready);
    CloseIfValid(buffer_ready);
    return 3;
  }

  SetLastError(ERROR_SUCCESS);
  HANDLE mapping = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr,
                                      PAGE_READWRITE, 0, kBufferSize,
                                      L"DBWIN_BUFFER");
  const DWORD mapping_error = GetLastError();
  if (mapping == nullptr || mapping_error == ERROR_ALREADY_EXISTS) {
    std::fprintf(stderr, "failed to own DBWIN_BUFFER: %lu\n",
                 static_cast<unsigned long>(mapping_error));
    CloseIfValid(mapping);
    CloseIfValid(data_ready);
    CloseIfValid(buffer_ready);
    return 4;
  }

  auto* buffer = static_cast<DbwinBuffer*>(
      MapViewOfFile(mapping, FILE_MAP_READ, 0, 0, kBufferSize));
  if (buffer == nullptr) {
    std::fprintf(stderr, "failed to map DBWIN_BUFFER: %lu\n",
                 static_cast<unsigned long>(GetLastError()));
    CloseIfValid(mapping);
    CloseIfValid(data_ready);
    CloseIfValid(buffer_ready);
    return 5;
  }

  SetEvent(buffer_ready);
  for (;;) {
    const DWORD wait_result = WaitForSingleObject(data_ready, INFINITE);
    if (wait_result != WAIT_OBJECT_0) {
      std::fprintf(stderr, "DBWIN wait failed: %lu\n",
                   static_cast<unsigned long>(GetLastError()));
      break;
    }

    PrintMatchingLines(*buffer);
    if (SetEvent(buffer_ready) == FALSE) {
      std::fprintf(stderr, "failed to signal DBWIN_BUFFER_READY: %lu\n",
                   static_cast<unsigned long>(GetLastError()));
      break;
    }
  }

  UnmapViewOfFile(buffer);
  CloseIfValid(mapping);
  CloseIfValid(data_ready);
  CloseIfValid(buffer_ready);
  return 6;
}
