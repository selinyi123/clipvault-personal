#include "runtime_snapshot.h"

#include <objbase.h>
#include <sddl.h>
#include <softpub.h>
#include <wintrust.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cwctype>
#include <limits>
#include <mutex>
#include <thread>
#include <unordered_set>
#include <utility>

namespace clipvault::ime {
namespace {

constexpr std::uint32_t kWireVarint = 0;
constexpr std::uint32_t kWireBytes = 2;
constexpr std::uint64_t kMaximumPositiveInt64 =
    static_cast<std::uint64_t>(std::numeric_limits<std::int64_t>::max());

struct Field final {
  std::uint32_t number = 0;
  std::uint32_t wire_type = 0;
  std::uint64_t varint = 0;
  std::vector<std::uint8_t> bytes;
};

void AppendVarint(std::vector<std::uint8_t>* output, std::uint64_t value) {
  while (value >= 0x80) {
    output->push_back(static_cast<std::uint8_t>(value & 0x7f) | 0x80);
    value >>= 7;
  }
  output->push_back(static_cast<std::uint8_t>(value));
}

void AppendUInt(std::vector<std::uint8_t>* output, std::uint32_t field,
                std::uint64_t value) {
  AppendVarint(output, (static_cast<std::uint64_t>(field) << 3) | kWireVarint);
  AppendVarint(output, value);
}

void AppendBytes(std::vector<std::uint8_t>* output, std::uint32_t field,
                 const std::vector<std::uint8_t>& value) {
  AppendVarint(output, (static_cast<std::uint64_t>(field) << 3) | kWireBytes);
  AppendVarint(output, value.size());
  output->insert(output->end(), value.begin(), value.end());
}

void AppendString(std::vector<std::uint8_t>* output, std::uint32_t field,
                  const std::string& value) {
  AppendBytes(output, field,
              std::vector<std::uint8_t>(value.begin(), value.end()));
}

std::size_t EncodedVarintSize(std::uint64_t value) noexcept {
  std::size_t size = 1;
  while (value >= 0x80) {
    value >>= 7;
    ++size;
  }
  return size;
}

bool ReadVarint(const std::vector<std::uint8_t>& input, std::size_t* cursor,
                std::uint64_t* value) {
  const std::size_t start = *cursor;
  std::uint64_t result = 0;
  for (unsigned shift = 0; shift < 70; shift += 7) {
    if (*cursor >= input.size()) return false;
    const std::uint8_t byte = input[(*cursor)++];
    if (shift == 63 && (byte & 0xfe) != 0) return false;
    if (shift > 63 && (byte & 0x7f) != 0) return false;
    result |= static_cast<std::uint64_t>(byte & 0x7f) << shift;
    if ((byte & 0x80) == 0) {
      if (*cursor - start != EncodedVarintSize(result)) return false;
      *value = result;
      return true;
    }
  }
  return false;
}

bool ParseStrict(const std::vector<std::uint8_t>& input,
                 const std::vector<std::pair<std::uint32_t, std::uint32_t>>&
                     allowed,
                 const std::unordered_set<std::uint32_t>& repeated,
                 std::vector<Field>* fields) {
  fields->clear();
  std::unordered_set<std::uint32_t> seen;
  std::size_t cursor = 0;
  while (cursor < input.size()) {
    std::uint64_t key = 0;
    if (!ReadVarint(input, &cursor, &key)) return false;
    const auto number = static_cast<std::uint32_t>(key >> 3);
    const auto wire = static_cast<std::uint32_t>(key & 7);
    const auto allowed_it = std::find_if(
        allowed.begin(), allowed.end(), [number](const auto& item) {
          return item.first == number;
        });
    if (number == 0 || allowed_it == allowed.end() ||
        allowed_it->second != wire ||
        (seen.contains(number) && !repeated.contains(number))) {
      return false;
    }
    seen.insert(number);
    Field field;
    field.number = number;
    field.wire_type = wire;
    if (wire == kWireVarint) {
      if (!ReadVarint(input, &cursor, &field.varint)) return false;
    } else if (wire == kWireBytes) {
      std::uint64_t length = 0;
      if (!ReadVarint(input, &cursor, &length) ||
          length > input.size() - cursor) {
        return false;
      }
      const auto end = cursor + static_cast<std::size_t>(length);
      field.bytes.assign(input.begin() + static_cast<std::ptrdiff_t>(cursor),
                         input.begin() + static_cast<std::ptrdiff_t>(end));
      cursor = end;
    } else {
      return false;
    }
    fields->push_back(std::move(field));
  }
  return cursor == input.size();
}

const Field* Find(const std::vector<Field>& fields, std::uint32_t number) {
  const auto found = std::find_if(fields.begin(), fields.end(),
                                  [number](const Field& field) {
                                    return field.number == number;
                                  });
  return found == fields.end() ? nullptr : &*found;
}

bool HasRequired(const std::vector<Field>& fields,
                 std::initializer_list<std::uint32_t> required) {
  return std::all_of(required.begin(), required.end(),
                     [&fields](std::uint32_t number) {
                       return Find(fields, number) != nullptr;
                     });
}

std::string BytesToString(const std::vector<std::uint8_t>& value) {
  return {value.begin(), value.end()};
}

bool IsCanonicalUuidV4(const std::string& value) {
  if (value.size() != 36 || value[8] != '-' || value[13] != '-' ||
      value[18] != '-' || value[23] != '-' || value[14] != '4' ||
      (value[19] != '8' && value[19] != '9' && value[19] != 'a' &&
       value[19] != 'b')) {
    return false;
  }
  for (std::size_t index = 0; index < value.size(); ++index) {
    if (index == 8 || index == 13 || index == 18 || index == 23) continue;
    const char ch = value[index];
    if (!((ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f'))) return false;
  }
  return true;
}

std::string NewCanonicalUuidV4() {
  for (int attempt = 0; attempt < 4; ++attempt) {
    GUID value{};
    if (FAILED(CoCreateGuid(&value))) return {};
    std::array<char, 37> text{};
    const int length = std::snprintf(
        text.data(), text.size(),
        "%08lx-%04x-%04x-%02x%02x-%02x%02x%02x%02x%02x%02x",
        static_cast<unsigned long>(value.Data1),
        static_cast<unsigned int>(value.Data2),
        static_cast<unsigned int>(value.Data3),
        static_cast<unsigned int>(value.Data4[0]),
        static_cast<unsigned int>(value.Data4[1]),
        static_cast<unsigned int>(value.Data4[2]),
        static_cast<unsigned int>(value.Data4[3]),
        static_cast<unsigned int>(value.Data4[4]),
        static_cast<unsigned int>(value.Data4[5]),
        static_cast<unsigned int>(value.Data4[6]),
        static_cast<unsigned int>(value.Data4[7]));
    if (length == 36 && IsCanonicalUuidV4(text.data())) return text.data();
  }
  return {};
}

bool WideFromUtf8(const std::vector<std::uint8_t>& value,
                  std::wstring* output) {
  output->clear();
  if (value.empty()) return true;
  const char* data = reinterpret_cast<const char*>(value.data());
  const int size = static_cast<int>(value.size());
  const int required = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, data,
                                            size, nullptr, 0);
  if (required <= 0) return false;
  output->resize(static_cast<std::size_t>(required));
  return MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, data, size,
                             output->data(), required) == required;
}

bool Utf8FromWide(const std::wstring& value, std::string* output) {
  output->clear();
  if (value.empty()) return true;
  const int required = WideCharToMultiByte(
      CP_UTF8, WC_ERR_INVALID_CHARS, value.data(), static_cast<int>(value.size()),
      nullptr, 0, nullptr, nullptr);
  if (required <= 0) return false;
  output->resize(static_cast<std::size_t>(required));
  return WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value.data(),
                             static_cast<int>(value.size()), output->data(),
                             required, nullptr, nullptr) == required;
}

bool IsValidUtf8(const std::vector<std::uint8_t>& value) {
  std::wstring ignored;
  return WideFromUtf8(value, &ignored);
}

bool IsValidUtf8(const std::string& value) {
  return IsValidUtf8(
      std::vector<std::uint8_t>(value.begin(), value.end()));
}

void WipeString(std::string* value) noexcept {
  std::fill(value->begin(), value->end(), '\0');
  value->clear();
}

void WipeString(std::wstring* value) noexcept {
  std::fill(value->begin(), value->end(), L'\0');
  value->clear();
}

void WipeSurface(SnapshotSurface* surface) noexcept {
  WipeString(&surface->publisher_epoch);
  surface->generation = 0;
  surface->expires_at_ms = 0;
  for (auto& candidate : surface->candidates) {
    WipeString(&candidate.candidate_id);
    WipeString(&candidate.label);
    WipeString(&candidate.text);
  }
  surface->candidates.clear();
}

DWORD RemainingMilliseconds(ULONGLONG deadline_tick) noexcept {
  const ULONGLONG now = GetTickCount64();
  if (now >= deadline_tick) return 0;
  return static_cast<DWORD>(
      std::min<ULONGLONG>(deadline_tick - now, MAXDWORD));
}

bool FinishOverlapped(HANDLE pipe, OVERLAPPED* overlapped,
                      ULONGLONG deadline_tick, DWORD* transferred) {
  const DWORD remaining = RemainingMilliseconds(deadline_tick);
  if (remaining == 0 ||
      WaitForSingleObject(overlapped->hEvent, remaining) != WAIT_OBJECT_0) {
    CancelIoEx(pipe, overlapped);
    GetOverlappedResult(pipe, overlapped, transferred, TRUE);
    return false;
  }
  return GetOverlappedResult(pipe, overlapped, transferred, FALSE) != FALSE;
}

bool TransferExactUntil(HANDLE pipe, std::uint8_t* data, DWORD size,
                        bool writing, ULONGLONG deadline_tick) {
  DWORD offset = 0;
  while (offset < size) {
    HANDLE event = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    if (event == nullptr) return false;
    OVERLAPPED overlapped{};
    overlapped.hEvent = event;
    DWORD transferred = 0;
    const BOOL immediate =
        writing ? WriteFile(pipe, data + offset, size - offset, &transferred,
                            &overlapped)
                : ReadFile(pipe, data + offset, size - offset, &transferred,
                           &overlapped);
    bool complete = immediate != FALSE;
    if (!complete && GetLastError() == ERROR_IO_PENDING) {
      complete = FinishOverlapped(pipe, &overlapped, deadline_tick,
                                  &transferred);
    }
    CloseHandle(event);
    if (!complete || transferred == 0) return false;
    offset += transferred;
  }
  return true;
}

bool CurrentProcessUserSid(std::vector<std::uint8_t>* output) {
  HANDLE token = nullptr;
  if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token)) return false;
  DWORD required = 0;
  GetTokenInformation(token, TokenUser, nullptr, 0, &required);
  output->resize(required);
  const bool result = required != 0 &&
                      GetTokenInformation(token, TokenUser, output->data(),
                                          required, &required) != FALSE;
  CloseHandle(token);
  return result;
}

bool ProcessUserMatchesCurrent(HANDLE process) {
  HANDLE token = nullptr;
  if (!OpenProcessToken(process, TOKEN_QUERY, &token)) return false;
  DWORD required = 0;
  GetTokenInformation(token, TokenUser, nullptr, 0, &required);
  std::vector<std::uint8_t> server(required);
  const bool loaded = required != 0 &&
                      GetTokenInformation(token, TokenUser, server.data(),
                                          required, &required) != FALSE;
  CloseHandle(token);
  if (!loaded) return false;
  std::vector<std::uint8_t> current;
  if (!CurrentProcessUserSid(&current)) return false;
  const auto* server_user =
      reinterpret_cast<const TOKEN_USER*>(server.data());
  const auto* current_user =
      reinterpret_cast<const TOKEN_USER*>(current.data());
  return EqualSid(server_user->User.Sid, current_user->User.Sid) != FALSE;
}

std::wstring ProcessImage(HANDLE process) {
  std::array<wchar_t, 32768> path{};
  DWORD length = static_cast<DWORD>(path.size());
  if (!QueryFullProcessImageNameW(process, 0, path.data(), &length)) return {};
  return {path.data(), length};
}

std::wstring FinalPath(const std::wstring& path) {
  HANDLE file = CreateFileW(path.c_str(), FILE_READ_ATTRIBUTES,
                            FILE_SHARE_READ | FILE_SHARE_WRITE |
                                FILE_SHARE_DELETE,
                            nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL,
                            nullptr);
  if (file == INVALID_HANDLE_VALUE) return {};
  std::array<wchar_t, 32768> buffer{};
  const DWORD length = GetFinalPathNameByHandleW(
      file, buffer.data(), static_cast<DWORD>(buffer.size()),
      FILE_NAME_NORMALIZED | VOLUME_NAME_DOS);
  CloseHandle(file);
  if (length == 0 || length >= buffer.size()) return {};
  std::wstring result(buffer.data(), length);
  if (result.rfind(L"\\\\?\\", 0) == 0) result.erase(0, 4);
  std::transform(result.begin(), result.end(), result.begin(),
                 [](wchar_t value) { return std::towlower(value); });
  return result;
}

bool HasTrustedSignature(const std::wstring& path) {
  WINTRUST_FILE_INFO file{};
  file.cbStruct = sizeof(file);
  file.pcwszFilePath = path.c_str();
  WINTRUST_DATA trust{};
  trust.cbStruct = sizeof(trust);
  trust.dwUIChoice = WTD_UI_NONE;
  trust.fdwRevocationChecks = WTD_REVOKE_NONE;
  trust.dwUnionChoice = WTD_CHOICE_FILE;
  trust.pFile = &file;
  trust.dwStateAction = WTD_STATEACTION_IGNORE;
  trust.dwProvFlags = WTD_CACHE_ONLY_URL_RETRIEVAL;
  GUID policy = WINTRUST_ACTION_GENERIC_VERIFY_V2;
  return WinVerifyTrust(nullptr, &policy, &trust) == ERROR_SUCCESS;
}

bool VerifyServer(HANDLE pipe, const RuntimeSnapshotFetchOptions& options) {
  ULONG process_id = 0;
  if (!GetNamedPipeServerProcessId(pipe, &process_id) || process_id == 0)
    return false;
  HANDLE process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE,
                               process_id);
  if (process == nullptr) return false;
  const bool same_user = ProcessUserMatchesCurrent(process);
  const std::wstring image = same_user ? ProcessImage(process) : std::wstring{};
  CloseHandle(process);
  if (image.empty() || options.expected_server_path.empty() ||
      FinalPath(image) != FinalPath(options.expected_server_path)) {
    return false;
  }
  return !options.require_trusted_signature || HasTrustedSignature(image);
}

HANDLE OpenSnapshotPipeUntil(const std::wstring& pipe_name,
                             ULONGLONG deadline_tick) {
  while (RemainingMilliseconds(deadline_tick) != 0) {
    HANDLE pipe = CreateFileW(pipe_name.c_str(), GENERIC_READ | GENERIC_WRITE,
                              0, nullptr, OPEN_EXISTING,
                              FILE_FLAG_OVERLAPPED | SECURITY_SQOS_PRESENT |
                                  SECURITY_IDENTIFICATION,
                              nullptr);
    if (pipe != INVALID_HANDLE_VALUE) return pipe;
    const DWORD error = GetLastError();
    if (error != ERROR_PIPE_BUSY) return INVALID_HANDLE_VALUE;
    const DWORD remaining = RemainingMilliseconds(deadline_tick);
    if (remaining == 0 || !WaitNamedPipeW(pipe_name.c_str(), remaining))
      return INVALID_HANDLE_VALUE;
  }
  return INVALID_HANDLE_VALUE;
}

bool IsSurfaceStructurallyValid(const SnapshotSurface& surface) {
  if (!IsCanonicalUuidV4(surface.publisher_epoch) || surface.generation == 0 ||
      surface.generation > kMaximumPositiveInt64 || surface.expires_at_ms == 0 ||
      surface.candidates.size() > kRuntimeSnapshotMaximumItems) {
    return false;
  }
  std::unordered_set<std::string> ids;
  for (const auto& item : surface.candidates) {
    std::string label;
    std::string text;
    if (item.candidate_id.empty() || item.candidate_id.size() > 128 ||
        !IsValidUtf8(item.candidate_id) || !ids.insert(item.candidate_id).second ||
        (item.source != 1 && item.source != 2) ||
        !Utf8FromWide(item.label, &label) || label.size() > 64 ||
        !Utf8FromWide(item.text, &text) || text.empty() ||
        text.size() > 16'384) {
      return false;
    }
  }
  return true;
}

}  // namespace

std::wstring RuntimeSnapshotPipeNameForCurrentSession() {
  DWORD session_id = 0;
  if (!ProcessIdToSessionId(GetCurrentProcessId(), &session_id)) return {};
  return L"\\\\.\\pipe\\ClipVaultRuntimeSnapshotV1-" +
         std::to_wstring(session_id);
}

std::wstring ExpectedRuntimeExecutable(const std::wstring& host_directory) {
  if (host_directory.empty()) return {};
  return host_directory + L"\\..\\..\\ClipVault.exe";
}

std::uint64_t UnixTimeMilliseconds() noexcept {
  FILETIME file_time{};
  GetSystemTimeAsFileTime(&file_time);
  ULARGE_INTEGER ticks{};
  ticks.LowPart = file_time.dwLowDateTime;
  ticks.HighPart = file_time.dwHighDateTime;
  constexpr std::uint64_t kWindowsToUnixEpoch100ns = 116'444'736'000'000'000ULL;
  if (ticks.QuadPart < kWindowsToUnixEpoch100ns) return 0;
  return (ticks.QuadPart - kWindowsToUnixEpoch100ns) / 10'000;
}

std::vector<std::uint8_t> EncodeRuntimeSnapshotClientHello(
    const std::string& client_instance) {
  if (!IsCanonicalUuidV4(client_instance)) return {};
  std::vector<std::uint8_t> payload;
  AppendUInt(&payload, 1, kRuntimeSnapshotProtocolVersion);
  AppendString(&payload, 2, client_instance);
  return payload;
}

std::vector<std::uint8_t> EncodeRuntimeSnapshotHostHello(
    const std::string& publisher_epoch) {
  return EncodeRuntimeSnapshotClientHello(publisher_epoch);
}

std::vector<std::uint8_t> EncodeRuntimeSnapshotRequest(
    std::uint64_t request_id, std::uint32_t limit) {
  if (request_id == 0 || request_id > kMaximumPositiveInt64 || limit == 0 ||
      limit > kRuntimeSnapshotMaximumItems) {
    return {};
  }
  std::vector<std::uint8_t> payload;
  AppendUInt(&payload, 1, request_id);
  AppendUInt(&payload, 2, limit);
  return payload;
}

std::vector<std::uint8_t> EncodeRuntimeSnapshotResponse(
    const RuntimeSnapshotResponse& response) {
  if (response.request_id == 0 || response.request_id > kMaximumPositiveInt64 ||
      !IsSurfaceStructurallyValid(response.surface)) {
    return {};
  }
  std::vector<std::uint8_t> payload;
  AppendUInt(&payload, 1, response.request_id);
  AppendString(&payload, 2, response.surface.publisher_epoch);
  AppendUInt(&payload, 3, response.surface.generation);
  AppendUInt(&payload, 4, response.surface.expires_at_ms);
  for (const auto& item : response.surface.candidates) {
    std::string label;
    std::string text;
    if (!Utf8FromWide(item.label, &label) || !Utf8FromWide(item.text, &text))
      return {};
    std::vector<std::uint8_t> encoded;
    AppendString(&encoded, 1, item.candidate_id);
    AppendUInt(&encoded, 2, item.source);
    AppendString(&encoded, 3, label);
    AppendString(&encoded, 4, text);
    AppendBytes(&payload, 5, encoded);
  }
  if (payload.empty() || payload.size() > kRuntimeSnapshotMaximumFrameBytes)
    return {};
  return payload;
}

bool DecodeRuntimeSnapshotClientHello(const std::vector<std::uint8_t>& payload,
                                      std::string* client_instance) {
  client_instance->clear();
  std::vector<Field> fields;
  if (!ParseStrict(payload, {{1, kWireVarint}, {2, kWireBytes}}, {},
                   &fields) ||
      fields.size() != 2 || !HasRequired(fields, {1, 2})) {
    return false;
  }
  const auto* version = Find(fields, 1);
  const auto* instance = Find(fields, 2);
  *client_instance = BytesToString(instance->bytes);
  return version->varint == kRuntimeSnapshotProtocolVersion &&
         IsCanonicalUuidV4(*client_instance);
}

bool DecodeRuntimeSnapshotHostHello(const std::vector<std::uint8_t>& payload,
                                    std::string* publisher_epoch) {
  return DecodeRuntimeSnapshotClientHello(payload, publisher_epoch);
}

bool DecodeRuntimeSnapshotRequest(const std::vector<std::uint8_t>& payload,
                                  std::uint64_t* request_id,
                                  std::uint32_t* limit) {
  *request_id = 0;
  *limit = 0;
  std::vector<Field> fields;
  if (!ParseStrict(payload, {{1, kWireVarint}, {2, kWireVarint}}, {},
                   &fields) ||
      fields.size() != 2 || !HasRequired(fields, {1, 2})) {
    return false;
  }
  const auto* request = Find(fields, 1);
  const auto* requested_limit = Find(fields, 2);
  if (request->varint == 0 || request->varint > kMaximumPositiveInt64 ||
      requested_limit->varint == 0 ||
      requested_limit->varint > kRuntimeSnapshotMaximumItems) {
    return false;
  }
  *request_id = request->varint;
  *limit = static_cast<std::uint32_t>(requested_limit->varint);
  return true;
}

bool DecodeRuntimeSnapshotResponse(const std::vector<std::uint8_t>& payload,
                                   std::uint64_t now_ms,
                                   RuntimeSnapshotResponse* response) {
  *response = RuntimeSnapshotResponse{};
  if (payload.empty() || payload.size() > kRuntimeSnapshotMaximumFrameBytes)
    return false;
  std::vector<Field> fields;
  if (!ParseStrict(payload,
                   {{1, kWireVarint}, {2, kWireBytes}, {3, kWireVarint},
                    {4, kWireVarint}, {5, kWireBytes}},
                   {5}, &fields) ||
      !HasRequired(fields, {1, 2, 3, 4})) {
    return false;
  }
  const auto item_count = static_cast<std::size_t>(std::count_if(
      fields.begin(), fields.end(), [](const Field& field) {
        return field.number == 5;
      }));
  if (item_count > kRuntimeSnapshotMaximumItems ||
      fields.size() != 4 + item_count) {
    return false;
  }
  const auto* request = Find(fields, 1);
  const auto* epoch = Find(fields, 2);
  const auto* generation = Find(fields, 3);
  const auto* expiry = Find(fields, 4);
  response->request_id = request->varint;
  response->surface.publisher_epoch = BytesToString(epoch->bytes);
  response->surface.generation = generation->varint;
  response->surface.expires_at_ms = expiry->varint;
  if (response->request_id == 0 ||
      response->request_id > kMaximumPositiveInt64 ||
      !IsCanonicalUuidV4(response->surface.publisher_epoch) ||
      response->surface.generation == 0 ||
      response->surface.generation > kMaximumPositiveInt64 || now_ms == 0 ||
      !(now_ms < response->surface.expires_at_ms &&
        response->surface.expires_at_ms <=
            now_ms + kRuntimeSnapshotMaximumLifetimeMilliseconds)) {
    return false;
  }
  std::unordered_set<std::string> ids;
  for (const auto& raw_item : fields) {
    if (raw_item.number != 5) continue;
    std::vector<Field> item_fields;
    if (!ParseStrict(raw_item.bytes,
                     {{1, kWireBytes}, {2, kWireVarint}, {3, kWireBytes},
                      {4, kWireBytes}},
                     {}, &item_fields) ||
        item_fields.size() != 4 ||
        !HasRequired(item_fields, {1, 2, 3, 4})) {
      return false;
    }
    const auto* id = Find(item_fields, 1);
    const auto* source = Find(item_fields, 2);
    const auto* label = Find(item_fields, 3);
    const auto* text = Find(item_fields, 4);
    SnapshotCandidate item;
    item.candidate_id = BytesToString(id->bytes);
    if (id->bytes.empty() || id->bytes.size() > 128 ||
        !IsValidUtf8(id->bytes) || !ids.insert(item.candidate_id).second ||
        (source->varint != 1 && source->varint != 2) ||
        label->bytes.size() > 64 || text->bytes.empty() ||
        text->bytes.size() > 16'384 ||
        !WideFromUtf8(label->bytes, &item.label) ||
        !WideFromUtf8(text->bytes, &item.text)) {
      return false;
    }
    item.source = static_cast<std::uint32_t>(source->varint);
    response->surface.candidates.push_back(std::move(item));
  }
  return true;
}

bool ReadRuntimeSnapshotFrameUntil(HANDLE pipe,
                                   std::vector<std::uint8_t>* payload,
                                   ULONGLONG deadline_tick) {
  payload->clear();
  std::array<std::uint8_t, 4> prefix{};
  if (!TransferExactUntil(pipe, prefix.data(),
                          static_cast<DWORD>(prefix.size()), false,
                          deadline_tick)) {
    return false;
  }
  const std::uint32_t size = (static_cast<std::uint32_t>(prefix[0]) << 24) |
                             (static_cast<std::uint32_t>(prefix[1]) << 16) |
                             (static_cast<std::uint32_t>(prefix[2]) << 8) |
                             static_cast<std::uint32_t>(prefix[3]);
  if (size == 0 || size > kRuntimeSnapshotMaximumFrameBytes) return false;
  payload->resize(size);
  return TransferExactUntil(pipe, payload->data(), size, false, deadline_tick);
}

bool WriteRuntimeSnapshotFrameUntil(HANDLE pipe,
                                    const std::vector<std::uint8_t>& payload,
                                    ULONGLONG deadline_tick) {
  if (payload.empty() || payload.size() > kRuntimeSnapshotMaximumFrameBytes)
    return false;
  const std::uint32_t size = static_cast<std::uint32_t>(payload.size());
  std::array<std::uint8_t, 4> prefix{
      static_cast<std::uint8_t>(size >> 24),
      static_cast<std::uint8_t>(size >> 16),
      static_cast<std::uint8_t>(size >> 8),
      static_cast<std::uint8_t>(size)};
  if (!TransferExactUntil(pipe, prefix.data(),
                          static_cast<DWORD>(prefix.size()), true,
                          deadline_tick)) {
    return false;
  }
  return TransferExactUntil(
      pipe, const_cast<std::uint8_t*>(payload.data()),
      static_cast<DWORD>(payload.size()), true, deadline_tick);
}

RuntimeSnapshotPipeClient::RuntimeSnapshotPipeClient(
    RuntimeSnapshotFetchOptions options)
    : options_(std::move(options)) {}

bool RuntimeSnapshotPipeClient::Fetch(
    std::uint64_t request_id, std::uint32_t limit, std::uint64_t now_ms,
    RuntimeSnapshotResponse* response) const {
  *response = RuntimeSnapshotResponse{};
  const std::wstring pipe_name =
      options_.pipe_name.empty() ? RuntimeSnapshotPipeNameForCurrentSession()
                                 : options_.pipe_name;
  const auto hello = EncodeRuntimeSnapshotClientHello(NewCanonicalUuidV4());
  const auto request = EncodeRuntimeSnapshotRequest(request_id, limit);
  if (pipe_name.empty() || options_.expected_server_path.empty() ||
      hello.empty() || request.empty()) {
    return false;
  }
  const ULONGLONG deadline =
      GetTickCount64() + kRuntimeSnapshotDeadlineMilliseconds;
  HANDLE pipe = OpenSnapshotPipeUntil(pipe_name, deadline);
  if (pipe == INVALID_HANDLE_VALUE) return false;
  bool success = VerifyServer(pipe, options_);
  std::vector<std::uint8_t> payload;
  std::string hello_epoch;
  RuntimeSnapshotResponse decoded;
  success = success && WriteRuntimeSnapshotFrameUntil(pipe, hello, deadline) &&
            ReadRuntimeSnapshotFrameUntil(pipe, &payload, deadline) &&
            DecodeRuntimeSnapshotHostHello(payload, &hello_epoch) &&
            WriteRuntimeSnapshotFrameUntil(pipe, request, deadline) &&
            ReadRuntimeSnapshotFrameUntil(pipe, &payload, deadline) &&
            DecodeRuntimeSnapshotResponse(payload, now_ms, &decoded) &&
            decoded.request_id == request_id &&
            decoded.surface.publisher_epoch == hello_epoch;
  CloseHandle(pipe);
  if (!success) return false;
  *response = std::move(decoded);
  return true;
}

struct RuntimeSnapshotCoordinator::SessionHandle final {
  mutable std::mutex mutex;
  bool allowed = false;
  std::uint64_t input_generation = 1;
  SnapshotSurface surface;
};

struct RuntimeSnapshotCoordinator::SharedState final {
  explicit SharedState(Fetcher value) : fetcher(std::move(value)) {}

  std::mutex mutex;
  Fetcher fetcher;
  std::atomic<std::uint64_t> next_request{1};
  std::string publisher_epoch;
  std::uint64_t generation = 0;
  std::unordered_set<std::string> retired_epochs;
  std::vector<std::weak_ptr<SessionHandle>> sessions;
};

RuntimeSnapshotCoordinator::RuntimeSnapshotCoordinator(Fetcher fetcher)
    : state_(std::make_shared<SharedState>(std::move(fetcher))) {}

std::shared_ptr<RuntimeSnapshotCoordinator::SessionHandle>
RuntimeSnapshotCoordinator::BeginSession(bool clipvault_allowed) {
  auto session = std::make_shared<SessionHandle>();
  session->allowed = clipvault_allowed;
  {
    std::lock_guard lock(state_->mutex);
    state_->sessions.push_back(session);
  }
  if (!clipvault_allowed || !state_->fetcher) return session;
  std::uint64_t request_id = state_->next_request.fetch_add(1);
  if (request_id == 0 || request_id > kMaximumPositiveInt64) {
    state_->next_request.store(2);
    request_id = 1;
  }
  const std::uint64_t input_generation = session->input_generation;
  auto state = state_;
  std::thread([state, session, request_id, input_generation] {
    RuntimeSnapshotResponse response;
    const std::uint64_t now_ms = UnixTimeMilliseconds();
    if (!state->fetcher(request_id, kRuntimeSnapshotMaximumItems, now_ms,
                        &response) ||
        response.request_id != request_id ||
        !IsSurfaceStructurallyValid(response.surface) ||
        !(now_ms < response.surface.expires_at_ms &&
          response.surface.expires_at_ms <=
              now_ms + kRuntimeSnapshotMaximumLifetimeMilliseconds)) {
      return;
    }

    std::lock_guard state_lock(state->mutex);
    bool epoch_changed = false;
    if (state->publisher_epoch.empty()) {
      state->publisher_epoch = response.surface.publisher_epoch;
    } else if (response.surface.publisher_epoch == state->publisher_epoch) {
      if (response.surface.generation <= state->generation) return;
    } else {
      if (state->retired_epochs.contains(response.surface.publisher_epoch))
        return;
      state->retired_epochs.insert(state->publisher_epoch);
      state->publisher_epoch = response.surface.publisher_epoch;
      state->generation = 0;
      epoch_changed = true;
    }
    state->generation = response.surface.generation;

    auto output = state->sessions.begin();
    for (auto input = state->sessions.begin(); input != state->sessions.end();
         ++input) {
      if (auto current = input->lock()) {
        if (epoch_changed) {
          std::lock_guard session_lock(current->mutex);
          WipeSurface(&current->surface);
        }
        *output++ = *input;
      }
    }
    state->sessions.erase(output, state->sessions.end());

    std::lock_guard session_lock(session->mutex);
    if (!session->allowed ||
        session->input_generation != input_generation) {
      return;
    }
    WipeSurface(&session->surface);
    session->surface = std::move(response.surface);
  }).detach();
  return session;
}

SnapshotSurface RuntimeSnapshotCoordinator::Current(
    const std::shared_ptr<SessionHandle>& session) const {
  if (!session) return {};
  std::lock_guard lock(session->mutex);
  if (!session->allowed ||
      session->surface.expires_at_ms <= UnixTimeMilliseconds()) {
    WipeSurface(&session->surface);
    return {};
  }
  return session->surface;
}

std::optional<std::wstring> RuntimeSnapshotCoordinator::Consume(
    const std::shared_ptr<SessionHandle>& session,
    const std::string& publisher_epoch, std::uint64_t generation,
    const std::string& candidate_id) {
  if (!session) return std::nullopt;
  std::lock_guard lock(session->mutex);
  if (!session->allowed ||
      session->surface.expires_at_ms <= UnixTimeMilliseconds() ||
      publisher_epoch != session->surface.publisher_epoch ||
      generation != session->surface.generation) {
    WipeSurface(&session->surface);
    return std::nullopt;
  }
  const auto candidate = std::find_if(
      session->surface.candidates.begin(), session->surface.candidates.end(),
      [&candidate_id](const SnapshotCandidate& value) {
        return value.candidate_id == candidate_id;
      });
  if (candidate == session->surface.candidates.end()) return std::nullopt;
  std::wstring text = candidate->text;
  WipeSurface(&session->surface);
  return text;
}

void RuntimeSnapshotCoordinator::Invalidate(
    const std::shared_ptr<SessionHandle>& session) noexcept {
  if (!session) return;
  std::lock_guard lock(session->mutex);
  session->allowed = false;
  ++session->input_generation;
  WipeSurface(&session->surface);
}

}  // namespace clipvault::ime
