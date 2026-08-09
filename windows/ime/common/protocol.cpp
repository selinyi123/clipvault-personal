#include "protocol.h"

#include "pipe_peer_trust.h"

#include <objbase.h>

#include <algorithm>
#include <array>
#include <limits>
#include <unordered_set>
#include <utility>

namespace clipvault::ime {
namespace {

constexpr std::uint32_t kWireVarint = 0;
constexpr std::uint32_t kWireFixed64 = 1;
constexpr std::uint32_t kWireBytes = 2;
constexpr std::uint32_t kWireFixed32 = 5;

struct ParsedField final {
  std::uint32_t number = 0;
  std::uint32_t wire_type = 0;
  std::uint64_t varint = 0;
  std::vector<std::uint8_t> bytes;
};

void AppendVarint(std::vector<std::uint8_t>* output, std::uint64_t value) {
  while (value >= 0x80) {
    output->push_back(static_cast<std::uint8_t>(value) | 0x80);
    value >>= 7;
  }
  output->push_back(static_cast<std::uint8_t>(value));
}

void AppendKey(std::vector<std::uint8_t>* output, std::uint32_t field,
               std::uint32_t wire_type) {
  AppendVarint(output, (static_cast<std::uint64_t>(field) << 3) | wire_type);
}

void AppendUInt(std::vector<std::uint8_t>* output, std::uint32_t field,
                std::uint64_t value) {
  AppendKey(output, field, kWireVarint);
  AppendVarint(output, value);
}

void AppendBool(std::vector<std::uint8_t>* output, std::uint32_t field, bool value) {
  if (value) {
    AppendUInt(output, field, 1);
  }
}

void AppendBytes(std::vector<std::uint8_t>* output, std::uint32_t field,
                 const std::vector<std::uint8_t>& value) {
  AppendKey(output, field, kWireBytes);
  AppendVarint(output, value.size());
  output->insert(output->end(), value.begin(), value.end());
}

void AppendString(std::vector<std::uint8_t>* output, std::uint32_t field,
                  const std::string& value) {
  AppendKey(output, field, kWireBytes);
  AppendVarint(output, value.size());
  output->insert(output->end(), value.begin(), value.end());
}

bool ReadVarint(const std::vector<std::uint8_t>& input, std::size_t* cursor,
                std::uint64_t* value) {
  std::uint64_t result = 0;
  for (unsigned shift = 0; shift < 64; shift += 7) {
    if (*cursor >= input.size()) {
      return false;
    }
    const auto byte = input[(*cursor)++];
    if (shift == 63 && (byte & 0xfe) != 0) {
      return false;
    }
    result |= static_cast<std::uint64_t>(byte & 0x7f) << shift;
    if ((byte & 0x80) == 0) {
      *value = result;
      return true;
    }
  }
  return false;
}

bool ParseFields(const std::vector<std::uint8_t>& input,
                 std::vector<ParsedField>* fields) {
  fields->clear();
  std::size_t cursor = 0;
  while (cursor < input.size()) {
    std::uint64_t raw_key = 0;
    if (!ReadVarint(input, &cursor, &raw_key)) {
      return false;
    }
    ParsedField field;
    field.number = static_cast<std::uint32_t>(raw_key >> 3);
    field.wire_type = static_cast<std::uint32_t>(raw_key & 7);
    if (field.number == 0) {
      return false;
    }
    if (field.wire_type == kWireVarint) {
      if (!ReadVarint(input, &cursor, &field.varint)) {
        return false;
      }
    } else if (field.wire_type == kWireBytes) {
      std::uint64_t length = 0;
      if (!ReadVarint(input, &cursor, &length) ||
          length > input.size() - cursor) {
        return false;
      }
      const auto end = cursor + static_cast<std::size_t>(length);
      field.bytes.assign(input.begin() + static_cast<std::ptrdiff_t>(cursor),
                         input.begin() + static_cast<std::ptrdiff_t>(end));
      cursor = end;
    } else if (field.wire_type == kWireFixed64) {
      if (input.size() - cursor < 8) return false;
      cursor += 8;
    } else if (field.wire_type == kWireFixed32) {
      if (input.size() - cursor < 4) return false;
      cursor += 4;
    } else {
      return false;
    }
    fields->push_back(std::move(field));
  }
  return cursor == input.size();
}

const ParsedField* FindUnique(const std::vector<ParsedField>& fields,
                              std::uint32_t number, std::uint32_t wire_type) {
  const ParsedField* result = nullptr;
  for (const auto& field : fields) {
    if (field.number == number) {
      if (field.wire_type != wire_type || result != nullptr) {
        return nullptr;
      }
      result = &field;
    }
  }
  return result;
}

std::string FieldString(const ParsedField& field) {
  return {field.bytes.begin(), field.bytes.end()};
}

bool Utf8FromWide(const std::wstring& value, std::string* output) {
  output->clear();
  if (value.empty()) return true;
  const int required = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value.data(),
                                            static_cast<int>(value.size()), nullptr, 0,
                                            nullptr, nullptr);
  if (required <= 0) return false;
  output->resize(static_cast<std::size_t>(required));
  return WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value.data(),
                             static_cast<int>(value.size()), output->data(), required,
                             nullptr, nullptr) == required;
}

bool WideFromUtf8(const ParsedField& field, std::wstring* output) {
  output->clear();
  if (field.bytes.empty()) return true;
  const char* data = reinterpret_cast<const char*>(field.bytes.data());
  const int size = static_cast<int>(field.bytes.size());
  const int required = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, data, size,
                                            nullptr, 0);
  if (required <= 0) return false;
  output->resize(static_cast<std::size_t>(required));
  return MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, data, size,
                             output->data(), required) == required;
}

bool IsValidUtf8(const std::vector<std::uint8_t>& value) {
  if (value.empty()) return true;
  const char* data = reinterpret_cast<const char*>(value.data());
  return MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, data,
                             static_cast<int>(value.size()), nullptr, 0) > 0;
}

bool IsValidUtf8(const std::string& value) {
  return IsValidUtf8(
      std::vector<std::uint8_t>(value.begin(), value.end()));
}

bool IsValidOptionName(const std::string& value) {
  if (value.empty() || value.size() > 64 || !IsValidUtf8(value)) return false;
  return std::all_of(value.begin(), value.end(), [](unsigned char ch) {
    return (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') ||
           (ch >= '0' && ch <= '9') || ch == '_' || ch == '-' || ch == '.';
  });
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

bool EncodeSnapshotSurface(const SnapshotSurface& surface,
                           std::vector<std::uint8_t>* encoded) {
  encoded->clear();
  if (surface.empty()) return true;
  if (!IsCanonicalUuidV4(surface.publisher_epoch) || surface.generation == 0 ||
      surface.generation >
          static_cast<std::uint64_t>(std::numeric_limits<std::int64_t>::max()) ||
      surface.expires_at_ms == 0 || surface.candidates.size() > 8) {
    return false;
  }
  AppendString(encoded, 1, surface.publisher_epoch);
  AppendUInt(encoded, 2, surface.generation);
  AppendUInt(encoded, 3, surface.expires_at_ms);
  std::unordered_set<std::string> ids;
  for (const auto& candidate : surface.candidates) {
    std::string label;
    std::string text;
    if (candidate.candidate_id.empty() || candidate.candidate_id.size() > 128 ||
        !IsValidUtf8(candidate.candidate_id) ||
        !ids.insert(candidate.candidate_id).second ||
        (candidate.source != 1 && candidate.source != 2) ||
        !Utf8FromWide(candidate.label, &label) || label.size() > 64 ||
        !Utf8FromWide(candidate.text, &text) || text.empty() ||
        text.size() > 16'384) {
      return false;
    }
    std::vector<std::uint8_t> item;
    AppendString(&item, 1, candidate.candidate_id);
    AppendUInt(&item, 2, candidate.source);
    AppendString(&item, 3, label);
    AppendString(&item, 4, text);
    AppendBytes(encoded, 4, item);
  }
  return true;
}

bool DecodeSnapshotSurface(const ParsedField& field, SnapshotSurface* surface) {
  *surface = SnapshotSurface{};
  if (field.wire_type != kWireBytes) return false;
  std::vector<ParsedField> fields;
  if (!ParseFields(field.bytes, &fields)) return false;
  const auto* epoch = FindUnique(fields, 1, kWireBytes);
  const auto* generation = FindUnique(fields, 2, kWireVarint);
  const auto* expires = FindUnique(fields, 3, kWireVarint);
  std::size_t item_count = 0;
  for (const auto& item : fields) item_count += item.number == 4 ? 1 : 0;
  if (epoch == nullptr || generation == nullptr || expires == nullptr ||
      fields.size() != 3 + item_count || item_count == 0 || item_count > 8 ||
      generation->varint == 0 ||
      generation->varint >
          static_cast<std::uint64_t>(std::numeric_limits<std::int64_t>::max()) ||
      expires->varint == 0) {
    return false;
  }
  surface->publisher_epoch = FieldString(*epoch);
  surface->generation = generation->varint;
  surface->expires_at_ms = expires->varint;
  if (!IsCanonicalUuidV4(surface->publisher_epoch)) return false;
  std::unordered_set<std::string> ids;
  for (const auto& item : fields) {
    if (item.number != 4) continue;
    if (item.wire_type != kWireBytes) return false;
    std::vector<ParsedField> item_fields;
    if (!ParseFields(item.bytes, &item_fields) || item_fields.size() != 4)
      return false;
    const auto* id = FindUnique(item_fields, 1, kWireBytes);
    const auto* source = FindUnique(item_fields, 2, kWireVarint);
    const auto* label = FindUnique(item_fields, 3, kWireBytes);
    const auto* text = FindUnique(item_fields, 4, kWireBytes);
    if (id == nullptr || id->bytes.empty() || id->bytes.size() > 128 ||
        !IsValidUtf8(id->bytes) ||
        !ids.insert(FieldString(*id)).second || source == nullptr ||
        (source->varint != 1 && source->varint != 2) || label == nullptr ||
        label->bytes.size() > 64 || text == nullptr || text->bytes.empty() ||
        text->bytes.size() > 16'384) {
      return false;
    }
    SnapshotCandidate candidate;
    candidate.candidate_id = FieldString(*id);
    candidate.source = static_cast<std::uint32_t>(source->varint);
    if (!WideFromUtf8(*label, &candidate.label) ||
        !WideFromUtf8(*text, &candidate.text)) {
      return false;
    }
    surface->candidates.push_back(std::move(candidate));
  }
  return true;
}

std::vector<std::uint8_t> Wrap(FrameKind kind,
                               const std::vector<std::uint8_t>& message) {
  std::vector<std::uint8_t> frame;
  AppendUInt(&frame, 1, kProtocolVersion);
  AppendBytes(&frame, static_cast<std::uint32_t>(kind), message);
  return frame;
}

bool Unwrap(const std::vector<std::uint8_t>& frame, FrameKind expected,
            std::vector<std::uint8_t>* message) {
  std::vector<ParsedField> fields;
  if (!ParseFields(frame, &fields)) return false;
  const auto* version = FindUnique(fields, 1, kWireVarint);
  const auto* payload = FindUnique(fields, static_cast<std::uint32_t>(expected),
                                   kWireBytes);
  if (version == nullptr || version->varint != kProtocolVersion || payload == nullptr ||
      fields.size() != 2) {
    return false;
  }
  *message = payload->bytes;
  return true;
}

bool ReadExact(HANDLE pipe, std::uint8_t* data, DWORD size) {
  DWORD offset = 0;
  while (offset < size) {
    DWORD read = 0;
    if (!ReadFile(pipe, data + offset, size - offset, &read, nullptr) || read == 0) {
      return false;
    }
    offset += read;
  }
  return true;
}

bool WriteExact(HANDLE pipe, const std::uint8_t* data, DWORD size) {
  DWORD offset = 0;
  while (offset < size) {
    DWORD written = 0;
    if (!WriteFile(pipe, data + offset, size - offset, &written, nullptr) || written == 0) {
      return false;
    }
    offset += written;
  }
  return true;
}

DWORD RemainingMilliseconds(ULONGLONG deadline_tick) noexcept {
  const ULONGLONG now = GetTickCount64();
  if (now >= deadline_tick) return 0;
  return static_cast<DWORD>(
      std::min<ULONGLONG>(deadline_tick - now, MAXDWORD));
}

bool FinishOverlapped(HANDLE pipe, OVERLAPPED* overlapped,
                      ULONGLONG deadline_tick, DWORD* transferred) {
  const DWORD wait = WaitForSingleObject(overlapped->hEvent,
                                         RemainingMilliseconds(deadline_tick));
  if (wait != WAIT_OBJECT_0) {
    CancelIoEx(pipe, overlapped);
    // The OVERLAPPED storage and event must remain alive until cancellation is
    // observed. Named-pipe cancellation completes promptly; callers still
    // disconnect the handle immediately after this failure.
    GetOverlappedResult(pipe, overlapped, transferred, TRUE);
    return false;
  }
  return GetOverlappedResult(pipe, overlapped, transferred, FALSE) != FALSE;
}

bool ReadExactUntil(HANDLE pipe, std::uint8_t* data, DWORD size,
                    ULONGLONG deadline_tick) {
  HANDLE event = CreateEventW(nullptr, TRUE, FALSE, nullptr);
  if (event == nullptr) return false;
  DWORD offset = 0;
  bool success = true;
  while (offset < size) {
    if (RemainingMilliseconds(deadline_tick) == 0) {
      success = false;
      break;
    }
    ResetEvent(event);
    OVERLAPPED overlapped{};
    overlapped.hEvent = event;
    DWORD transferred = 0;
    const BOOL completed = ReadFile(pipe, data + offset, size - offset,
                                    &transferred, &overlapped);
    if (!completed) {
      if (GetLastError() != ERROR_IO_PENDING ||
          !FinishOverlapped(pipe, &overlapped, deadline_tick, &transferred)) {
        success = false;
        break;
      }
    }
    if (transferred == 0) {
      success = false;
      break;
    }
    offset += transferred;
  }
  CloseHandle(event);
  return success;
}

bool WriteExactUntil(HANDLE pipe, const std::uint8_t* data, DWORD size,
                     ULONGLONG deadline_tick) {
  HANDLE event = CreateEventW(nullptr, TRUE, FALSE, nullptr);
  if (event == nullptr) return false;
  DWORD offset = 0;
  bool success = true;
  while (offset < size) {
    if (RemainingMilliseconds(deadline_tick) == 0) {
      success = false;
      break;
    }
    ResetEvent(event);
    OVERLAPPED overlapped{};
    overlapped.hEvent = event;
    DWORD transferred = 0;
    const BOOL completed = WriteFile(pipe, data + offset, size - offset,
                                     &transferred, &overlapped);
    if (!completed) {
      if (GetLastError() != ERROR_IO_PENDING ||
          !FinishOverlapped(pipe, &overlapped, deadline_tick, &transferred)) {
        success = false;
        break;
      }
    }
    if (transferred == 0) {
      success = false;
      break;
    }
    offset += transferred;
  }
  CloseHandle(event);
  return success;
}

}  // namespace

std::vector<std::uint8_t> EncodeClientHello(const std::string& client_instance_id) {
  std::vector<std::uint8_t> message;
  AppendString(&message, 1, client_instance_id);
  AppendUInt(&message, 2, kProtocolVersion);
  AppendUInt(&message, 3, 2);  // INPUT_PLATFORM_WINDOWS
#if defined(_M_ARM64)
  AppendString(&message, 4, "arm64");
#elif defined(_WIN64)
  AppendString(&message, 4, "x64");
#else
  AppendString(&message, 4, "x86");
#endif
  AppendString(&message, 5, "clipvault-native-p1");
  return Wrap(FrameKind::kClientHello, message);
}

std::vector<std::uint8_t> EncodeHostHello(const std::string& host_instance_id) {
  std::vector<std::uint8_t> message;
  AppendString(&message, 1, host_instance_id);
  AppendUInt(&message, 2, kProtocolVersion);
  AppendString(&message, 3, "clipvault-native-host-p1");
  AppendString(&message, 4, "stable-candidate-id");
  AppendString(&message, 4, "page-select-commit-cancel");
  AppendString(&message, 4, "set-option-end-session");
  AppendString(&message, 4, "response-ack-retry-cache");
  return Wrap(FrameKind::kHostHello, message);
}

std::vector<std::uint8_t> EncodeStartSession(const StartSessionRequest& request) {
  std::vector<std::uint8_t> context;
  AppendUInt(&context, 1, 2);  // Windows
  AppendUInt(&context, 2,
             static_cast<std::uint32_t>(request.context.field_kind));
  AppendUInt(&context, 3,
             static_cast<std::uint32_t>(request.context.action));
  // Emit all privacy flags explicitly. The native decoder requires them so a
  // missing/older client cannot silently inherit permissive defaults.
  AppendUInt(&context, 4, request.context.incognito ? 1 : 0);
  AppendUInt(&context, 5, request.context.learning_allowed ? 1 : 0);
  AppendUInt(&context, 6, request.context.clipvault_allowed ? 1 : 0);
  if (!request.context.app_scope.empty())
    AppendString(&context, 7, request.context.app_scope);

  std::vector<std::uint8_t> message;
  AppendString(&message, 1, request.host_instance_id);
  AppendString(&message, 2, request.session_id);
  AppendUInt(&message, 3, request.request_seq);
  AppendBytes(&message, 4, context);
  return Wrap(FrameKind::kStartSessionRequest, message);
}

std::vector<std::uint8_t> EncodeProcessKey(const ProcessKeyRequest& request) {
  std::string text;
  if (!Utf8FromWide(request.event.text, &text)) return {};
  std::vector<std::uint8_t> key;
  AppendUInt(&key, 1, request.event.virtual_key);
  if (!text.empty()) AppendString(&key, 2, text);
  AppendBool(&key, 3, request.event.key_down);
  AppendBool(&key, 4, request.event.repeat);
  AppendBool(&key, 5, request.event.shift);
  AppendBool(&key, 6, request.event.control);
  AppendBool(&key, 7, request.event.alt);

  std::vector<std::uint8_t> message;
  AppendString(&message, 1, request.host_instance_id);
  AppendString(&message, 2, request.session_id);
  AppendUInt(&message, 3, request.request_seq);
  AppendUInt(&message, 4, request.expected_revision);
  AppendBytes(&message, 5, key);
  return Wrap(FrameKind::kProcessKeyRequest, message);
}

std::vector<std::uint8_t> EncodeSelectCandidate(
    const SelectCandidateRequest& request) {
  std::vector<std::uint8_t> message;
  AppendString(&message, 1, request.host_instance_id);
  AppendString(&message, 2, request.session_id);
  AppendUInt(&message, 3, request.request_seq);
  AppendUInt(&message, 4, request.expected_revision);
  AppendString(&message, 5, request.candidate_id);
  return Wrap(FrameKind::kSelectCandidateRequest, message);
}

std::vector<std::uint8_t> EncodePageCandidates(
    const PageCandidatesRequest& request) {
  std::vector<std::uint8_t> message;
  AppendString(&message, 1, request.host_instance_id);
  AppendString(&message, 2, request.session_id);
  AppendUInt(&message, 3, request.request_seq);
  AppendUInt(&message, 4, request.expected_revision);
  AppendUInt(&message, 5, request.backward ? 1 : 2);
  return Wrap(FrameKind::kPageCandidatesRequest, message);
}

std::vector<std::uint8_t> EncodeCompositionCommand(
    FrameKind kind, const CompositionCommandRequest& request) {
  std::vector<std::uint8_t> message;
  AppendString(&message, 1, request.host_instance_id);
  AppendString(&message, 2, request.session_id);
  AppendUInt(&message, 3, request.request_seq);
  AppendUInt(&message, 4, request.expected_revision);
  return Wrap(kind, message);
}

std::vector<std::uint8_t> EncodeCommitComposition(
    const CompositionCommandRequest& request) {
  return EncodeCompositionCommand(FrameKind::kCommitCompositionRequest, request);
}

std::vector<std::uint8_t> EncodeCancelComposition(
    const CompositionCommandRequest& request) {
  return EncodeCompositionCommand(FrameKind::kCancelCompositionRequest, request);
}

std::vector<std::uint8_t> EncodeSetOption(const SetOptionRequest& request) {
  if (!IsValidOptionName(request.option)) return {};
  std::vector<std::uint8_t> message;
  AppendString(&message, 1, request.host_instance_id);
  AppendString(&message, 2, request.session_id);
  AppendUInt(&message, 3, request.request_seq);
  AppendUInt(&message, 4, request.expected_revision);
  AppendString(&message, 5, request.option);
  AppendUInt(&message, 6, request.enabled ? 1 : 0);
  return Wrap(FrameKind::kSetOptionRequest, message);
}

std::vector<std::uint8_t> EncodeEndSession(const EndSessionRequest& request) {
  std::vector<std::uint8_t> message;
  AppendString(&message, 1, request.host_instance_id);
  AppendString(&message, 2, request.session_id);
  AppendUInt(&message, 3, request.request_seq);
  return Wrap(FrameKind::kEndSessionRequest, message);
}

std::vector<std::uint8_t> EncodeSessionEnded(const SessionEnded& response) {
  std::vector<std::uint8_t> message;
  AppendString(&message, 1, response.host_instance_id);
  AppendString(&message, 2, response.session_id);
  AppendUInt(&message, 3, response.ack_request_seq);
  return Wrap(FrameKind::kSessionEnded, message);
}

std::vector<std::uint8_t> EncodeResponseAck(
    const ResponseAck& acknowledgement) {
  std::vector<std::uint8_t> message;
  AppendString(&message, 1, acknowledgement.host_instance_id);
  AppendString(&message, 2, acknowledgement.session_id);
  AppendUInt(&message, 3, acknowledgement.ack_request_seq);
  return Wrap(FrameKind::kResponseAck, message);
}

std::vector<std::uint8_t> EncodeSelectSnapshotCandidate(
    const SelectSnapshotCandidateRequest& request) {
  if (!IsCanonicalUuidV4(request.publisher_epoch) || request.generation == 0 ||
      request.generation >
          static_cast<std::uint64_t>(std::numeric_limits<std::int64_t>::max()) ||
      request.candidate_id.empty() || request.candidate_id.size() > 128 ||
      !IsValidUtf8(request.candidate_id)) {
    return {};
  }
  std::vector<std::uint8_t> message;
  AppendString(&message, 1, request.host_instance_id);
  AppendString(&message, 2, request.session_id);
  AppendUInt(&message, 3, request.request_seq);
  AppendUInt(&message, 4, request.expected_revision);
  AppendString(&message, 5, request.publisher_epoch);
  AppendUInt(&message, 6, request.generation);
  AppendString(&message, 7, request.candidate_id);
  return Wrap(FrameKind::kSelectSnapshotCandidateRequest, message);
}

std::vector<std::uint8_t> EncodeInsertOtp(const InsertOtpRequest& request) {
  const bool valid_tokens =
      (request.context.document_token[6] & 0xf0U) == 0x40U &&
      (request.context.document_token[8] & 0xc0U) == 0x80U &&
      (request.context.context_token[6] & 0xf0U) == 0x40U &&
      (request.context.context_token[8] & 0xc0U) == 0x80U;
  if (request.context.process_id == 0 || request.context.thread_id == 0 ||
      request.context.window_handle == 0 || !valid_tokens) {
    return {};
  }
  std::vector<std::uint8_t> context;
  AppendUInt(&context, 1, request.context.process_id);
  AppendUInt(&context, 2, request.context.thread_id);
  AppendUInt(&context, 3, request.context.window_handle);
  AppendBytes(&context, 4,
              std::vector<std::uint8_t>(request.context.document_token.begin(),
                                        request.context.document_token.end()));
  AppendBytes(&context, 5,
              std::vector<std::uint8_t>(request.context.context_token.begin(),
                                        request.context.context_token.end()));
  std::vector<std::uint8_t> message;
  AppendString(&message, 1, request.host_instance_id);
  AppendString(&message, 2, request.session_id);
  AppendUInt(&message, 3, request.request_seq);
  AppendUInt(&message, 4, request.expected_revision);
  AppendBytes(&message, 5, context);
  return Wrap(FrameKind::kInsertOtpRequest, message);
}

std::vector<std::uint8_t> EncodeEngineState(const EngineState& state) {
  std::string preedit;
  std::string commit;
  struct Utf8Guard final {
    std::string* first;
    std::string* second;
    ~Utf8Guard() {
      if (first != nullptr && !first->empty())
        SecureZeroMemory(first->data(), first->size());
      if (second != nullptr && !second->empty())
        SecureZeroMemory(second->data(), second->size());
    }
  } utf8_guard{&preedit, &commit};
  if (!Utf8FromWide(state.preedit, &preedit) ||
      (state.commit_text.has_value() && !Utf8FromWide(*state.commit_text, &commit))) {
    return {};
  }
  std::vector<std::uint8_t> message;
  struct ByteGuard final {
    std::vector<std::uint8_t>* bytes;
    ~ByteGuard() {
      if (bytes != nullptr && !bytes->empty())
        SecureZeroMemory(bytes->data(), bytes->size());
    }
  } message_guard{&message};
  AppendString(&message, 1, state.host_instance_id);
  AppendString(&message, 2, state.session_id);
  AppendUInt(&message, 3, state.ack_request_seq);
  AppendUInt(&message, 4, state.revision);
  AppendBool(&message, 5, state.handled);
  if (!preedit.empty()) AppendString(&message, 6, preedit);
  AppendUInt(&message, 7, state.caret_utf16);
  if (!preedit.empty()) {
    std::vector<std::uint8_t> segment;
    AppendUInt(&segment, 2, state.preedit.size());
    AppendUInt(&segment, 3, 1);  // RAW
    AppendBytes(&message, 8, segment);
  }
  for (const auto& candidate : state.candidates) {
    std::string text;
    std::string comment;
    if (candidate.candidate_id.empty() || !Utf8FromWide(candidate.text, &text) ||
        !Utf8FromWide(candidate.comment, &comment)) return {};
    std::vector<std::uint8_t> encoded_candidate;
    AppendString(&encoded_candidate, 1, candidate.candidate_id);
    AppendString(&encoded_candidate, 2, text);
    if (!comment.empty()) AppendString(&encoded_candidate, 3, comment);
    AppendUInt(&encoded_candidate, 4, 1);  // CANDIDATE_SOURCE_ENGINE
    AppendBytes(&message, 9, encoded_candidate);
  }
  std::vector<std::uint8_t> page;
  AppendUInt(&page, 1, state.page_index);
  AppendUInt(&page, 2, state.page_size);
  AppendBool(&page, 3, state.has_previous_page);
  AppendBool(&page, 4, state.has_next_page);
  AppendBytes(&message, 10, page);
  if (state.commit_text.has_value()) AppendString(&message, 11, commit);
  AppendBool(&message, 12, state.composition_active);
  AppendUInt(&message, 13, state.mode);
  std::vector<std::uint8_t> snapshot;
  ByteGuard snapshot_guard{&snapshot};
  if (!EncodeSnapshotSurface(state.snapshot_surface, &snapshot)) return {};
  if (!snapshot.empty()) AppendBytes(&message, 20, snapshot);
  return Wrap(FrameKind::kEngineState, message);
}

bool DecodeClientHello(const std::vector<std::uint8_t>& frame,
                       std::string* client_instance_id) {
  std::vector<std::uint8_t> message;
  std::vector<ParsedField> fields;
  if (!Unwrap(frame, FrameKind::kClientHello, &message) ||
      !ParseFields(message, &fields)) return false;
  const auto* id = FindUnique(fields, 1, kWireBytes);
  const auto* platform = FindUnique(fields, 3, kWireVarint);
  bool supports_v2 = false;
  for (const auto& field : fields) {
    supports_v2 |= field.number == 2 && field.wire_type == kWireVarint &&
                   field.varint == kProtocolVersion;
  }
  if (id == nullptr || id->bytes.empty() || platform == nullptr ||
      platform->varint != 2 || !supports_v2) return false;
  *client_instance_id = FieldString(*id);
  return true;
}

bool DecodeHostHello(const std::vector<std::uint8_t>& frame,
                     std::string* host_instance_id) {
  std::vector<std::uint8_t> message;
  std::vector<ParsedField> fields;
  if (!Unwrap(frame, FrameKind::kHostHello, &message) ||
      !ParseFields(message, &fields)) return false;
  const auto* id = FindUnique(fields, 1, kWireBytes);
  const auto* version = FindUnique(fields, 2, kWireVarint);
  if (id == nullptr || id->bytes.empty() || version == nullptr ||
      version->varint != kProtocolVersion) return false;
  *host_instance_id = FieldString(*id);
  return true;
}

bool DecodeStartSession(const std::vector<std::uint8_t>& frame,
                        StartSessionRequest* request) {
  *request = StartSessionRequest{};
  std::vector<std::uint8_t> message;
  std::vector<ParsedField> fields;
  if (!Unwrap(frame, FrameKind::kStartSessionRequest, &message) ||
      !ParseFields(message, &fields)) return false;
  const auto* host = FindUnique(fields, 1, kWireBytes);
  const auto* session = FindUnique(fields, 2, kWireBytes);
  const auto* sequence = FindUnique(fields, 3, kWireVarint);
  const auto* context = FindUnique(fields, 4, kWireBytes);
  if (host == nullptr || session == nullptr || sequence == nullptr ||
      context == nullptr || host->bytes.empty() || session->bytes.empty() ||
      sequence->varint != 1 || fields.size() != 4) return false;
  std::vector<ParsedField> context_fields;
  if (!ParseFields(context->bytes, &context_fields)) return false;
  const auto* platform = FindUnique(context_fields, 1, kWireVarint);
  const auto* field_kind = FindUnique(context_fields, 2, kWireVarint);
  const auto* action = FindUnique(context_fields, 3, kWireVarint);
  const auto* incognito = FindUnique(context_fields, 4, kWireVarint);
  const auto* learning = FindUnique(context_fields, 5, kWireVarint);
  const auto* clipvault = FindUnique(context_fields, 6, kWireVarint);
  const auto* app_scope = FindUnique(context_fields, 7, kWireBytes);
  const std::size_t expected_context_fields = app_scope == nullptr ? 6 : 7;
  if (platform == nullptr || platform->varint != 2 || field_kind == nullptr ||
      field_kind->varint > static_cast<std::uint32_t>(InputFieldKind::kOtp) ||
      action == nullptr ||
      action->varint > static_cast<std::uint32_t>(InputAction::kSend) ||
      incognito == nullptr || incognito->varint > 1 || learning == nullptr ||
      learning->varint > 1 || clipvault == nullptr || clipvault->varint > 1 ||
      context_fields.size() != expected_context_fields ||
      (app_scope != nullptr && app_scope->bytes.size() > 64)) {
    return false;
  }
  request->context.field_kind =
      static_cast<InputFieldKind>(field_kind->varint);
  request->context.action = static_cast<InputAction>(action->varint);
  request->context.incognito = incognito->varint != 0;
  request->context.learning_allowed = learning->varint != 0;
  request->context.clipvault_allowed = clipvault->varint != 0;
  if (app_scope != nullptr) request->context.app_scope = FieldString(*app_scope);
  const bool private_context =
      request->context.field_kind == InputFieldKind::kPassword ||
      request->context.field_kind == InputFieldKind::kUnknown ||
      request->context.incognito;
  if (private_context && (request->context.learning_allowed ||
                          request->context.clipvault_allowed)) {
    return false;
  }
  request->host_instance_id = FieldString(*host);
  request->session_id = FieldString(*session);
  request->request_seq = sequence->varint;
  return true;
}

bool DecodeProcessKey(const std::vector<std::uint8_t>& frame,
                      ProcessKeyRequest* request) {
  *request = ProcessKeyRequest{};
  std::vector<std::uint8_t> message;
  std::vector<ParsedField> fields;
  if (!Unwrap(frame, FrameKind::kProcessKeyRequest, &message) ||
      !ParseFields(message, &fields)) return false;
  const auto* host = FindUnique(fields, 1, kWireBytes);
  const auto* session = FindUnique(fields, 2, kWireBytes);
  const auto* sequence = FindUnique(fields, 3, kWireVarint);
  const auto* revision = FindUnique(fields, 4, kWireVarint);
  const auto* key_message = FindUnique(fields, 5, kWireBytes);
  if (host == nullptr || session == nullptr || sequence == nullptr ||
      revision == nullptr || key_message == nullptr) return false;
  std::vector<ParsedField> key_fields;
  if (!ParseFields(key_message->bytes, &key_fields)) return false;
  const auto* virtual_key = FindUnique(key_fields, 1, kWireVarint);
  if (virtual_key == nullptr) return false;
  request->host_instance_id = FieldString(*host);
  request->session_id = FieldString(*session);
  request->request_seq = sequence->varint;
  request->expected_revision = revision->varint;
  request->event.virtual_key = static_cast<std::uint32_t>(virtual_key->varint);
  if (const auto* text = FindUnique(key_fields, 2, kWireBytes); text != nullptr &&
      !WideFromUtf8(*text, &request->event.text)) return false;
  if (const auto* value = FindUnique(key_fields, 3, kWireVarint); value != nullptr)
    request->event.key_down = value->varint != 0;
  if (const auto* value = FindUnique(key_fields, 4, kWireVarint); value != nullptr)
    request->event.repeat = value->varint != 0;
  if (const auto* value = FindUnique(key_fields, 5, kWireVarint); value != nullptr)
    request->event.shift = value->varint != 0;
  if (const auto* value = FindUnique(key_fields, 6, kWireVarint); value != nullptr)
    request->event.control = value->varint != 0;
  if (const auto* value = FindUnique(key_fields, 7, kWireVarint); value != nullptr)
    request->event.alt = value->varint != 0;
  return true;
}

bool DecodeSelectCandidate(const std::vector<std::uint8_t>& frame,
                           SelectCandidateRequest* request) {
  *request = SelectCandidateRequest{};
  std::vector<std::uint8_t> message;
  std::vector<ParsedField> fields;
  if (!Unwrap(frame, FrameKind::kSelectCandidateRequest, &message) ||
      !ParseFields(message, &fields)) return false;
  const auto* host = FindUnique(fields, 1, kWireBytes);
  const auto* session = FindUnique(fields, 2, kWireBytes);
  const auto* sequence = FindUnique(fields, 3, kWireVarint);
  const auto* revision = FindUnique(fields, 4, kWireVarint);
  const auto* candidate = FindUnique(fields, 5, kWireBytes);
  if (host == nullptr || session == nullptr || sequence == nullptr ||
      revision == nullptr || candidate == nullptr || candidate->bytes.empty()) return false;
  request->host_instance_id = FieldString(*host);
  request->session_id = FieldString(*session);
  request->request_seq = sequence->varint;
  request->expected_revision = revision->varint;
  request->candidate_id = FieldString(*candidate);
  return true;
}

bool DecodePageCandidates(const std::vector<std::uint8_t>& frame,
                          PageCandidatesRequest* request) {
  *request = PageCandidatesRequest{};
  std::vector<std::uint8_t> message;
  std::vector<ParsedField> fields;
  if (!Unwrap(frame, FrameKind::kPageCandidatesRequest, &message) ||
      !ParseFields(message, &fields)) return false;
  const auto* host = FindUnique(fields, 1, kWireBytes);
  const auto* session = FindUnique(fields, 2, kWireBytes);
  const auto* sequence = FindUnique(fields, 3, kWireVarint);
  const auto* revision = FindUnique(fields, 4, kWireVarint);
  const auto* direction = FindUnique(fields, 5, kWireVarint);
  if (host == nullptr || session == nullptr || sequence == nullptr ||
      revision == nullptr || direction == nullptr ||
      (direction->varint != 1 && direction->varint != 2)) return false;
  request->host_instance_id = FieldString(*host);
  request->session_id = FieldString(*session);
  request->request_seq = sequence->varint;
  request->expected_revision = revision->varint;
  request->backward = direction->varint == 1;
  return true;
}

bool DecodeCompositionCommand(const std::vector<std::uint8_t>& frame,
                              FrameKind kind,
                              CompositionCommandRequest* request) {
  *request = CompositionCommandRequest{};
  std::vector<std::uint8_t> message;
  std::vector<ParsedField> fields;
  if (!Unwrap(frame, kind, &message) || !ParseFields(message, &fields)) return false;
  const auto* host = FindUnique(fields, 1, kWireBytes);
  const auto* session = FindUnique(fields, 2, kWireBytes);
  const auto* sequence = FindUnique(fields, 3, kWireVarint);
  const auto* revision = FindUnique(fields, 4, kWireVarint);
  if (host == nullptr || session == nullptr || sequence == nullptr ||
      revision == nullptr || host->bytes.empty() || session->bytes.empty()) return false;
  request->host_instance_id = FieldString(*host);
  request->session_id = FieldString(*session);
  request->request_seq = sequence->varint;
  request->expected_revision = revision->varint;
  return true;
}

bool DecodeCommitComposition(const std::vector<std::uint8_t>& frame,
                             CompositionCommandRequest* request) {
  return DecodeCompositionCommand(frame, FrameKind::kCommitCompositionRequest,
                                  request);
}

bool DecodeCancelComposition(const std::vector<std::uint8_t>& frame,
                             CompositionCommandRequest* request) {
  return DecodeCompositionCommand(frame, FrameKind::kCancelCompositionRequest,
                                   request);
}

bool DecodeSetOption(const std::vector<std::uint8_t>& frame,
                     SetOptionRequest* request) {
  if (request == nullptr) return false;
  *request = SetOptionRequest{};
  std::vector<std::uint8_t> message;
  std::vector<ParsedField> fields;
  if (!Unwrap(frame, FrameKind::kSetOptionRequest, &message) ||
      !ParseFields(message, &fields) || fields.size() != 6) {
    return false;
  }
  const auto* host = FindUnique(fields, 1, kWireBytes);
  const auto* session = FindUnique(fields, 2, kWireBytes);
  const auto* sequence = FindUnique(fields, 3, kWireVarint);
  const auto* revision = FindUnique(fields, 4, kWireVarint);
  const auto* option = FindUnique(fields, 5, kWireBytes);
  const auto* enabled = FindUnique(fields, 6, kWireVarint);
  if (host == nullptr || host->bytes.empty() || host->bytes.size() > 128 ||
      session == nullptr || session->bytes.empty() || session->bytes.size() > 128 ||
      sequence == nullptr || sequence->varint == 0 || revision == nullptr ||
      option == nullptr || option->bytes.empty() || option->bytes.size() > 64 ||
      enabled == nullptr || enabled->varint > 1) {
    return false;
  }
  request->host_instance_id = FieldString(*host);
  request->session_id = FieldString(*session);
  request->request_seq = sequence->varint;
  request->expected_revision = revision->varint;
  request->option = FieldString(*option);
  request->enabled = enabled->varint != 0;
  return IsValidOptionName(request->option);
}

bool DecodeEndSession(const std::vector<std::uint8_t>& frame,
                      EndSessionRequest* request) {
  if (request == nullptr) return false;
  *request = EndSessionRequest{};
  std::vector<std::uint8_t> message;
  std::vector<ParsedField> fields;
  if (!Unwrap(frame, FrameKind::kEndSessionRequest, &message) ||
      !ParseFields(message, &fields) || fields.size() != 3) {
    return false;
  }
  const auto* host = FindUnique(fields, 1, kWireBytes);
  const auto* session = FindUnique(fields, 2, kWireBytes);
  const auto* sequence = FindUnique(fields, 3, kWireVarint);
  if (host == nullptr || host->bytes.empty() || host->bytes.size() > 128 ||
      session == nullptr || session->bytes.empty() || session->bytes.size() > 128 ||
      sequence == nullptr || sequence->varint == 0) {
    return false;
  }
  request->host_instance_id = FieldString(*host);
  request->session_id = FieldString(*session);
  request->request_seq = sequence->varint;
  return true;
}

bool DecodeSessionEnded(const std::vector<std::uint8_t>& frame,
                        SessionEnded* response) {
  if (response == nullptr) return false;
  *response = SessionEnded{};
  std::vector<std::uint8_t> message;
  std::vector<ParsedField> fields;
  if (!Unwrap(frame, FrameKind::kSessionEnded, &message) ||
      !ParseFields(message, &fields) || fields.size() != 3) {
    return false;
  }
  const auto* host = FindUnique(fields, 1, kWireBytes);
  const auto* session = FindUnique(fields, 2, kWireBytes);
  const auto* sequence = FindUnique(fields, 3, kWireVarint);
  if (host == nullptr || host->bytes.empty() || host->bytes.size() > 128 ||
      session == nullptr || session->bytes.empty() || session->bytes.size() > 128 ||
      sequence == nullptr || sequence->varint == 0) {
    return false;
  }
  response->host_instance_id = FieldString(*host);
  response->session_id = FieldString(*session);
  response->ack_request_seq = sequence->varint;
  return true;
}

bool DecodeResponseAck(const std::vector<std::uint8_t>& frame,
                       ResponseAck* acknowledgement) {
  if (acknowledgement == nullptr) return false;
  *acknowledgement = ResponseAck{};
  std::vector<std::uint8_t> message;
  std::vector<ParsedField> fields;
  if (!Unwrap(frame, FrameKind::kResponseAck, &message) ||
      !ParseFields(message, &fields) || fields.size() != 3) {
    return false;
  }
  const auto* host = FindUnique(fields, 1, kWireBytes);
  const auto* session = FindUnique(fields, 2, kWireBytes);
  const auto* sequence = FindUnique(fields, 3, kWireVarint);
  if (host == nullptr || host->bytes.empty() || host->bytes.size() > 128 ||
      session == nullptr || session->bytes.empty() || session->bytes.size() > 128 ||
      sequence == nullptr || sequence->varint == 0) {
    return false;
  }
  acknowledgement->host_instance_id = FieldString(*host);
  acknowledgement->session_id = FieldString(*session);
  acknowledgement->ack_request_seq = sequence->varint;
  return true;
}

bool DecodeSelectSnapshotCandidate(
    const std::vector<std::uint8_t>& frame,
    SelectSnapshotCandidateRequest* request) {
  *request = SelectSnapshotCandidateRequest{};
  std::vector<std::uint8_t> message;
  std::vector<ParsedField> fields;
  if (!Unwrap(frame, FrameKind::kSelectSnapshotCandidateRequest, &message) ||
      !ParseFields(message, &fields) || fields.size() != 7) {
    return false;
  }
  const auto* host = FindUnique(fields, 1, kWireBytes);
  const auto* session = FindUnique(fields, 2, kWireBytes);
  const auto* sequence = FindUnique(fields, 3, kWireVarint);
  const auto* revision = FindUnique(fields, 4, kWireVarint);
  const auto* epoch = FindUnique(fields, 5, kWireBytes);
  const auto* generation = FindUnique(fields, 6, kWireVarint);
  const auto* candidate = FindUnique(fields, 7, kWireBytes);
  if (host == nullptr || host->bytes.empty() || session == nullptr ||
      session->bytes.empty() || sequence == nullptr || revision == nullptr ||
      epoch == nullptr || generation == nullptr || generation->varint == 0 ||
      generation->varint >
          static_cast<std::uint64_t>(std::numeric_limits<std::int64_t>::max()) ||
      candidate == nullptr || candidate->bytes.empty() ||
      candidate->bytes.size() > 128 || !IsValidUtf8(candidate->bytes)) {
    return false;
  }
  request->host_instance_id = FieldString(*host);
  request->session_id = FieldString(*session);
  request->request_seq = sequence->varint;
  request->expected_revision = revision->varint;
  request->publisher_epoch = FieldString(*epoch);
  request->generation = generation->varint;
  request->candidate_id = FieldString(*candidate);
  return IsCanonicalUuidV4(request->publisher_epoch);
}

bool DecodeInsertOtp(const std::vector<std::uint8_t>& frame,
                     InsertOtpRequest* request) {
  if (request == nullptr) return false;
  *request = InsertOtpRequest{};
  std::vector<std::uint8_t> message;
  std::vector<ParsedField> fields;
  if (!Unwrap(frame, FrameKind::kInsertOtpRequest, &message) ||
      !ParseFields(message, &fields) || fields.size() != 5) return false;
  const auto* host = FindUnique(fields, 1, kWireBytes);
  const auto* session = FindUnique(fields, 2, kWireBytes);
  const auto* sequence = FindUnique(fields, 3, kWireVarint);
  const auto* revision = FindUnique(fields, 4, kWireVarint);
  const auto* context = FindUnique(fields, 5, kWireBytes);
  if (host == nullptr || host->bytes.empty() || session == nullptr ||
      session->bytes.empty() || sequence == nullptr || revision == nullptr ||
      context == nullptr) return false;
  std::vector<ParsedField> context_fields;
  if (!ParseFields(context->bytes, &context_fields) ||
      context_fields.size() != 5) return false;
  const auto* process = FindUnique(context_fields, 1, kWireVarint);
  const auto* thread = FindUnique(context_fields, 2, kWireVarint);
  const auto* window = FindUnique(context_fields, 3, kWireVarint);
  const auto* document = FindUnique(context_fields, 4, kWireBytes);
  const auto* token = FindUnique(context_fields, 5, kWireBytes);
  if (process == nullptr || process->varint == 0 ||
      process->varint > UINT32_MAX || thread == nullptr || thread->varint == 0 ||
      thread->varint > UINT32_MAX || window == nullptr || window->varint == 0 ||
      document == nullptr || document->bytes.size() != 16 || token == nullptr ||
      token->bytes.size() != 16) return false;
  request->host_instance_id = FieldString(*host);
  request->session_id = FieldString(*session);
  request->request_seq = sequence->varint;
  request->expected_revision = revision->varint;
  request->context.process_id = static_cast<std::uint32_t>(process->varint);
  request->context.thread_id = static_cast<std::uint32_t>(thread->varint);
  request->context.window_handle = window->varint;
  std::copy(document->bytes.begin(), document->bytes.end(),
            request->context.document_token.begin());
  std::copy(token->bytes.begin(), token->bytes.end(),
            request->context.context_token.begin());
  const auto canonical = [](const auto& value) {
    return (value[6] & 0xf0U) == 0x40U && (value[8] & 0xc0U) == 0x80U;
  };
  return canonical(request->context.document_token) &&
         canonical(request->context.context_token);
}

bool DecodeEngineState(const std::vector<std::uint8_t>& frame, EngineState* state) {
  *state = EngineState{};
  std::vector<std::uint8_t> message;
  std::vector<ParsedField> fields;
  if (!Unwrap(frame, FrameKind::kEngineState, &message) ||
      !ParseFields(message, &fields)) return false;
  const auto* host = FindUnique(fields, 1, kWireBytes);
  const auto* session = FindUnique(fields, 2, kWireBytes);
  const auto* sequence = FindUnique(fields, 3, kWireVarint);
  const auto* revision = FindUnique(fields, 4, kWireVarint);
  if (host == nullptr || session == nullptr || sequence == nullptr || revision == nullptr)
    return false;
  state->host_instance_id = FieldString(*host);
  state->session_id = FieldString(*session);
  state->ack_request_seq = sequence->varint;
  state->revision = revision->varint;
  if (const auto* value = FindUnique(fields, 5, kWireVarint); value != nullptr)
    state->handled = value->varint != 0;
  if (const auto* value = FindUnique(fields, 6, kWireBytes); value != nullptr &&
      !WideFromUtf8(*value, &state->preedit)) return false;
  if (const auto* value = FindUnique(fields, 7, kWireVarint); value != nullptr)
    state->caret_utf16 = static_cast<std::uint32_t>(value->varint);
  for (const auto& field : fields) {
    if (field.number != 9) continue;
    if (field.wire_type != kWireBytes) return false;
    std::vector<ParsedField> candidate_fields;
    if (!ParseFields(field.bytes, &candidate_fields)) return false;
    const auto* id = FindUnique(candidate_fields, 1, kWireBytes);
    const auto* text = FindUnique(candidate_fields, 2, kWireBytes);
    const auto* source = FindUnique(candidate_fields, 4, kWireVarint);
    if (id == nullptr || id->bytes.empty() || text == nullptr || source == nullptr ||
        source->varint != 1) return false;
    EngineCandidate candidate;
    candidate.candidate_id = FieldString(*id);
    if (!WideFromUtf8(*text, &candidate.text)) return false;
    if (const auto* comment = FindUnique(candidate_fields, 3, kWireBytes);
        comment != nullptr && !WideFromUtf8(*comment, &candidate.comment)) return false;
    state->candidates.push_back(std::move(candidate));
  }
  if (const auto* page = FindUnique(fields, 10, kWireBytes); page != nullptr) {
    std::vector<ParsedField> page_fields;
    if (!ParseFields(page->bytes, &page_fields)) return false;
    if (const auto* value = FindUnique(page_fields, 1, kWireVarint); value != nullptr)
      state->page_index = static_cast<std::uint32_t>(value->varint);
    if (const auto* value = FindUnique(page_fields, 2, kWireVarint); value != nullptr)
      state->page_size = static_cast<std::uint32_t>(value->varint);
    if (const auto* value = FindUnique(page_fields, 3, kWireVarint); value != nullptr)
      state->has_previous_page = value->varint != 0;
    if (const auto* value = FindUnique(page_fields, 4, kWireVarint); value != nullptr)
      state->has_next_page = value->varint != 0;
  }
  if (const auto* value = FindUnique(fields, 11, kWireBytes); value != nullptr) {
    std::wstring commit;
    if (!WideFromUtf8(*value, &commit)) return false;
    state->commit_text = std::move(commit);
  }
  if (const auto* value = FindUnique(fields, 12, kWireVarint); value != nullptr)
    state->composition_active = value->varint != 0;
  if (const auto* value = FindUnique(fields, 13, kWireVarint); value != nullptr)
    state->mode = static_cast<std::uint32_t>(value->varint);
  std::size_t snapshot_count = 0;
  for (const auto& field : fields) snapshot_count += field.number == 20 ? 1 : 0;
  if (snapshot_count > 1) return false;
  if (snapshot_count == 1) {
    const auto* surface = FindUnique(fields, 20, kWireBytes);
    if (surface == nullptr ||
        !DecodeSnapshotSurface(*surface, &state->snapshot_surface)) {
      return false;
    }
  }
  return state->caret_utf16 <= state->preedit.size();
}

bool ReadFrame(HANDLE pipe, std::vector<std::uint8_t>* payload) {
  std::array<std::uint8_t, 4> prefix{};
  if (!ReadExact(pipe, prefix.data(), static_cast<DWORD>(prefix.size()))) return false;
  const std::uint32_t size = (static_cast<std::uint32_t>(prefix[0]) << 24) |
                             (static_cast<std::uint32_t>(prefix[1]) << 16) |
                             (static_cast<std::uint32_t>(prefix[2]) << 8) |
                             static_cast<std::uint32_t>(prefix[3]);
  if (size == 0 || size > kMaximumFrameBytes) return false;
  payload->resize(size);
  return ReadExact(pipe, payload->data(), size);
}

bool WriteFrame(HANDLE pipe, const std::vector<std::uint8_t>& payload) {
  if (payload.empty() || payload.size() > kMaximumFrameBytes ||
      payload.size() > std::numeric_limits<DWORD>::max()) return false;
  const auto size = static_cast<std::uint32_t>(payload.size());
  const std::array<std::uint8_t, 4> prefix{
      static_cast<std::uint8_t>(size >> 24), static_cast<std::uint8_t>(size >> 16),
      static_cast<std::uint8_t>(size >> 8), static_cast<std::uint8_t>(size)};
  return WriteExact(pipe, prefix.data(), static_cast<DWORD>(prefix.size())) &&
         WriteExact(pipe, payload.data(), size) && FlushFileBuffers(pipe) != FALSE;
}

bool ReadFrameUntil(HANDLE pipe, std::vector<std::uint8_t>* payload,
                    ULONGLONG deadline_tick) {
  std::array<std::uint8_t, 4> prefix{};
  if (!ReadExactUntil(pipe, prefix.data(), static_cast<DWORD>(prefix.size()),
                      deadline_tick)) {
    return false;
  }
  const std::uint32_t size = (static_cast<std::uint32_t>(prefix[0]) << 24) |
                             (static_cast<std::uint32_t>(prefix[1]) << 16) |
                             (static_cast<std::uint32_t>(prefix[2]) << 8) |
                             static_cast<std::uint32_t>(prefix[3]);
  if (size == 0 || size > kMaximumFrameBytes) return false;
  payload->resize(size);
  return ReadExactUntil(pipe, payload->data(), size, deadline_tick);
}

bool WriteFrameUntil(HANDLE pipe, const std::vector<std::uint8_t>& payload,
                     ULONGLONG deadline_tick) {
  if (payload.empty() || payload.size() > kMaximumFrameBytes ||
      payload.size() > std::numeric_limits<DWORD>::max()) return false;
  const auto size = static_cast<std::uint32_t>(payload.size());
  const std::array<std::uint8_t, 4> prefix{
      static_cast<std::uint8_t>(size >> 24), static_cast<std::uint8_t>(size >> 16),
      static_cast<std::uint8_t>(size >> 8), static_cast<std::uint8_t>(size)};
  return WriteExactUntil(pipe, prefix.data(), static_cast<DWORD>(prefix.size()),
                         deadline_tick) &&
         WriteExactUntil(pipe, payload.data(), size, deadline_tick);
}

std::string NewOpaqueId() {
  GUID value{};
  if (FAILED(CoCreateGuid(&value))) return {};
  wchar_t wide[40]{};
  if (StringFromGUID2(value, wide, static_cast<int>(std::size(wide))) <= 0) return {};
  std::string output;
  for (const wchar_t ch : std::wstring(wide)) {
    if (ch != L'{' && ch != L'}' && ch != L'-')
      output.push_back(static_cast<char>(ch >= L'A' && ch <= L'F' ? ch + 32 : ch));
  }
  return output;
}

std::wstring ExpectedImeHostServerPath() {
  using namespace clipvault::windows::trust;
  const std::wstring module_directory = ParentDirectory(CurrentModulePath());
  const std::wstring architecture_directory = FileName(module_directory);
  if (architecture_directory != L"x64" &&
      architecture_directory != L"x86") {
    // Native tests place the client and Host targets in one private build
    // directory.  The isolated test escape hatch may skip signatures, but it
    // still names the exact executable rather than trusting any process in
    // that directory.
    if (ExplicitUnsignedTestTrustEnabled(LocalTestNamespaceSuffix())) {
      return JoinPath(module_directory, L"ClipVaultImeHost.exe");
    }
    return {};
  }
  const std::wstring package_directory = ParentDirectory(module_directory);
  return JoinPath(JoinPath(package_directory, L"host-x64"),
                  L"ClipVaultImeHost.exe");
}

std::wstring PipeNameForCurrentSession() {
  DWORD session_id = 0;
  ProcessIdToSessionId(GetCurrentProcessId(), &session_id);
  return L"\\\\.\\pipe\\ClipVaultImeV2-" + std::to_wstring(session_id) +
         LocalTestNamespaceSuffix();
}

std::wstring LocalTestNamespaceSuffix() {
  wchar_t value[65]{};
  const DWORD length = GetEnvironmentVariableW(L"CLIPVAULT_IME_TEST_NAMESPACE",
                                                value, std::size(value));
  if (length == 0 || length >= std::size(value)) return {};
  for (DWORD index = 0; index < length; ++index) {
    const wchar_t ch = value[index];
    if (!((ch >= L'a' && ch <= L'z') || (ch >= L'A' && ch <= L'Z') ||
          (ch >= L'0' && ch <= L'9') || ch == L'-' || ch == L'_')) return {};
  }
  return L"-" + std::wstring(value, length);
}

bool ResponseProjectionLedger::Begin(
    const std::string& host_instance_id,
    const std::string& session_id) {
  if (host_instance_id.empty() || session_id.empty()) return false;
  host_instance_id_ = host_instance_id;
  session_id_ = session_id;
  high_water_mark_ = 0;
  return true;
}

ResponseReservation ResponseProjectionLedger::Reserve(
    const std::string& host_instance_id, const std::string& session_id,
    std::uint64_t ack_request_seq) noexcept {
  if (host_instance_id != host_instance_id_ || session_id != session_id_ ||
      ack_request_seq == 0) {
    return ResponseReservation::kInvalid;
  }
  if (ack_request_seq <= high_water_mark_)
    return ResponseReservation::kDuplicate;
  if (ack_request_seq != high_water_mark_ + 1)
    return ResponseReservation::kInvalid;
  // Reserve before the caller can expose EngineState to a TSF edit session.
  high_water_mark_ = ack_request_seq;
  return ResponseReservation::kReserved;
}

void ResponseProjectionLedger::End() noexcept {
  host_instance_id_.clear();
  session_id_.clear();
  high_water_mark_ = 0;
}

PipeEngineClient::~PipeEngineClient() { Disconnect(); }

bool PipeEngineClient::Connect(DWORD wait_milliseconds) {
  Disconnect();
  const auto pipe_name = PipeNameForCurrentSession();
  const ULONGLONG deadline = GetTickCount64() + wait_milliseconds;
  do {
    pipe_ = CreateFileW(
        pipe_name.c_str(), GENERIC_READ | GENERIC_WRITE, 0, nullptr,
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OVERLAPPED | SECURITY_SQOS_PRESENT |
            SECURITY_IDENTIFICATION,
        nullptr);
    if (pipe_ != INVALID_HANDLE_VALUE) break;
    const DWORD error = GetLastError();
    if (error != ERROR_FILE_NOT_FOUND && error != ERROR_PIPE_BUSY) return false;
    const ULONGLONG now = GetTickCount64();
    if (now >= deadline) return false;
    const DWORD remaining = static_cast<DWORD>(std::min<ULONGLONG>(deadline - now, 50));
    if (error == ERROR_PIPE_BUSY) {
      WaitNamedPipeW(pipe_name.c_str(), remaining);
    } else {
      Sleep(std::min<DWORD>(remaining, 10));
    }
  } while (GetTickCount64() < deadline);
  if (pipe_ == INVALID_HANDLE_VALUE) return false;
  if (!clipvault::windows::trust::VerifyNamedPipeServer(
          pipe_, ExpectedImeHostServerPath(), LocalTestNamespaceSuffix())) {
    Disconnect();
    return false;
  }
  const auto client_id = NewOpaqueId();
  std::vector<std::uint8_t> response;
  if (client_id.empty() ||
      !ExchangeUntil(EncodeClientHello(client_id), &response, deadline) ||
      !DecodeHostHello(response, &host_instance_id_)) {
    Disconnect();
    return false;
  }
  return true;
}

void PipeEngineClient::CloseTransport() noexcept {
  if (pipe_ != INVALID_HANDLE_VALUE) {
    CancelIoEx(pipe_, nullptr);
    CloseHandle(pipe_);
    pipe_ = INVALID_HANDLE_VALUE;
  }
  host_instance_id_.clear();
  session_id_.clear();
  next_request_seq_ = 1;
  revision_ = 0;
  projection_ledger_.End();
}

void PipeEngineClient::Disconnect() noexcept {
  if (connected() && !session_id_.empty()) {
    constexpr DWORD kDisconnectEndSessionBudgetMilliseconds = 20;
    try {
      EndSession(kDisconnectEndSessionBudgetMilliseconds);
    } catch (...) {
      // Destruction/reset must remain noexcept. Closing the authenticated pipe
      // still retires all Host-side session state.
    }
  }
  CloseTransport();
}

bool PipeEngineClient::ExchangeUntil(const std::vector<std::uint8_t>& request,
                                     std::vector<std::uint8_t>* response,
                                     ULONGLONG deadline_tick) {
  return connected() && !request.empty() &&
         WriteFrameUntil(pipe_, request, deadline_tick) &&
         ReadFrameUntil(pipe_, response, deadline_tick);
}

bool PipeEngineClient::SendResponseAck(std::uint64_t ack_request_seq,
                                       ULONGLONG deadline_tick) noexcept {
  const ResponseAck acknowledgement{host_instance_id_, session_id_,
                                    ack_request_seq};
  const auto encoded = EncodeResponseAck(acknowledgement);
  return connected() && !encoded.empty() &&
         WriteFrameUntil(pipe_, encoded, deadline_tick);
}

bool PipeEngineClient::AcceptState(const std::vector<std::uint8_t>& response,
                                   EngineState* state,
                                   ULONGLONG deadline_tick) {
  if (!DecodeEngineState(response, state) ||
      state->host_instance_id != host_instance_id_ ||
      state->session_id != session_id_ ||
      state->ack_request_seq != next_request_seq_) {
    return false;
  }
  if (projection_ledger_.Reserve(state->host_instance_id, state->session_id,
                                 state->ack_request_seq) !=
      ResponseReservation::kReserved) {
    return false;
  }
  revision_ = state->revision;
  ++next_request_seq_;
  return SendResponseAck(state->ack_request_seq, deadline_tick);
}

bool PipeEngineClient::StartSession(EngineState* state,
                                    DWORD budget_milliseconds) {
  InputContext ordinary;
  ordinary.field_kind = InputFieldKind::kText;
  ordinary.incognito = false;
  ordinary.learning_allowed = true;
  ordinary.clipvault_allowed = true;
  return StartSession(ordinary, state, budget_milliseconds);
}

bool PipeEngineClient::StartSession(const InputContext& context,
                                    EngineState* state,
                                    DWORD budget_milliseconds) {
  if (!connected()) return false;
  if (!session_id_.empty() && !EndSession(budget_milliseconds)) {
    CloseTransport();
    return false;
  }
  // Request sequences and revisions are scoped to one engine session. A new
  // context on the same authenticated pipe must not inherit the prior
  // session's ledger position.
  next_request_seq_ = 1;
  revision_ = 0;
  session_id_ = NewOpaqueId();
  if (!projection_ledger_.Begin(host_instance_id_, session_id_)) {
    CloseTransport();
    return false;
  }
  StartSessionRequest request{host_instance_id_, session_id_, next_request_seq_,
                              context};
  std::vector<std::uint8_t> response;
  const ULONGLONG deadline = GetTickCount64() + budget_milliseconds;
  if (session_id_.empty() ||
      !ExchangeUntil(EncodeStartSession(request), &response, deadline) ||
      !AcceptState(response, state, deadline)) {
    Disconnect();
    return false;
  }
  return true;
}

bool PipeEngineClient::ProcessKey(const KeyEvent& event, EngineState* state,
                                  DWORD budget_milliseconds) {
  if (!connected() || session_id_.empty()) return false;
  ProcessKeyRequest request{host_instance_id_, session_id_, next_request_seq_,
                            revision_, event};
  const auto encoded = EncodeProcessKey(request);
  std::vector<std::uint8_t> response;
  const ULONGLONG deadline = GetTickCount64() + budget_milliseconds;
  if (!ExchangeUntil(encoded, &response, deadline) ||
      !AcceptState(response, state, deadline)) {
    Disconnect();
    return false;
  }
  return true;
}

bool PipeEngineClient::SelectCandidate(const std::string& candidate_id,
                                       EngineState* state,
                                       DWORD budget_milliseconds) {
  if (!connected() || session_id_.empty() || candidate_id.empty()) return false;
  SelectCandidateRequest request{host_instance_id_, session_id_, next_request_seq_,
                                 revision_, candidate_id};
  std::vector<std::uint8_t> response;
  const ULONGLONG deadline = GetTickCount64() + budget_milliseconds;
  if (!ExchangeUntil(EncodeSelectCandidate(request), &response, deadline) ||
      !AcceptState(response, state, deadline)) {
    Disconnect();
    return false;
  }
  return true;
}

bool PipeEngineClient::PageCandidates(bool backward, EngineState* state,
                                      DWORD budget_milliseconds) {
  if (!connected() || session_id_.empty()) return false;
  PageCandidatesRequest request{host_instance_id_, session_id_, next_request_seq_,
                                revision_, backward};
  std::vector<std::uint8_t> response;
  const ULONGLONG deadline = GetTickCount64() + budget_milliseconds;
  if (!ExchangeUntil(EncodePageCandidates(request), &response, deadline) ||
      !AcceptState(response, state, deadline)) {
    Disconnect();
    return false;
  }
  return true;
}

bool PipeEngineClient::CommitComposition(EngineState* state,
                                         DWORD budget_milliseconds) {
  if (!connected() || session_id_.empty()) return false;
  CompositionCommandRequest request{host_instance_id_, session_id_,
                                    next_request_seq_, revision_};
  std::vector<std::uint8_t> response;
  const ULONGLONG deadline = GetTickCount64() + budget_milliseconds;
  if (!ExchangeUntil(EncodeCommitComposition(request), &response, deadline) ||
      !AcceptState(response, state, deadline)) {
    Disconnect();
    return false;
  }
  return true;
}

bool PipeEngineClient::CancelComposition(EngineState* state,
                                         DWORD budget_milliseconds) {
  if (!connected() || session_id_.empty()) return false;
  CompositionCommandRequest request{host_instance_id_, session_id_,
                                    next_request_seq_, revision_};
  std::vector<std::uint8_t> response;
  const ULONGLONG deadline = GetTickCount64() + budget_milliseconds;
  if (!ExchangeUntil(EncodeCancelComposition(request), &response, deadline) ||
      !AcceptState(response, state, deadline)) {
    Disconnect();
    return false;
  }
  return true;
}

bool PipeEngineClient::SetOption(const std::string& option, bool enabled,
                                 EngineState* state,
                                 DWORD budget_milliseconds) {
  if (!connected() || session_id_.empty()) return false;
  SetOptionRequest request{host_instance_id_, session_id_, next_request_seq_,
                           revision_, option, enabled};
  const auto encoded = EncodeSetOption(request);
  std::vector<std::uint8_t> response;
  const ULONGLONG deadline = GetTickCount64() + budget_milliseconds;
  if (encoded.empty() || !ExchangeUntil(encoded, &response, deadline) ||
      !AcceptState(response, state, deadline)) {
    Disconnect();
    return false;
  }
  return true;
}

bool PipeEngineClient::EndSession(DWORD budget_milliseconds) {
  if (!connected() || session_id_.empty()) return true;
  const EndSessionRequest request{host_instance_id_, session_id_,
                                  next_request_seq_};
  const auto encoded = EncodeEndSession(request);
  std::vector<std::uint8_t> response_frame;
  const ULONGLONG deadline = GetTickCount64() + budget_milliseconds;
  SessionEnded response;
  if (encoded.empty() ||
      !ExchangeUntil(encoded, &response_frame, deadline) ||
      !DecodeSessionEnded(response_frame, &response) ||
      response.host_instance_id != host_instance_id_ ||
      response.session_id != session_id_ ||
      response.ack_request_seq != next_request_seq_) {
    return false;
  }
  // SessionEnded has no content-bearing state, but authenticate delivery so
  // the Host can follow the same bounded response lifecycle.
  if (!SendResponseAck(response.ack_request_seq, deadline)) return false;
  session_id_.clear();
  next_request_seq_ = 1;
  revision_ = 0;
  projection_ledger_.End();
  return true;
}

bool PipeEngineClient::SelectSnapshotCandidate(
    const std::string& publisher_epoch, std::uint64_t generation,
    const std::string& candidate_id, EngineState* state,
    DWORD budget_milliseconds) {
  if (!connected() || session_id_.empty() || candidate_id.empty()) return false;
  SelectSnapshotCandidateRequest request{
      host_instance_id_, session_id_, next_request_seq_, revision_,
      publisher_epoch, generation, candidate_id};
  const auto encoded = EncodeSelectSnapshotCandidate(request);
  std::vector<std::uint8_t> response;
  const ULONGLONG deadline = GetTickCount64() + budget_milliseconds;
  if (encoded.empty() || !ExchangeUntil(encoded, &response, deadline) ||
      !AcceptState(response, state, deadline)) {
    Disconnect();
    return false;
  }
  return true;
}

bool PipeEngineClient::InsertOtp(const OtpContextBinding& context,
                                 EngineState* state,
                                 DWORD budget_milliseconds) {
  if (!connected() || session_id_.empty()) return false;
  std::vector<std::uint8_t> encoded;
  std::vector<std::uint8_t> response;
  struct SensitiveResponseGuard final {
    std::vector<std::uint8_t>* value;
    ~SensitiveResponseGuard() {
      if (value != nullptr && !value->empty())
        SecureZeroMemory(value->data(), value->size());
    }
  } response_guard{&response};
  const ULONGLONG deadline = GetTickCount64() + budget_milliseconds;
  bool accepted = false;
  try {
    const InsertOtpRequest request{host_instance_id_, session_id_,
                                   next_request_seq_, revision_, context};
    encoded = EncodeInsertOtp(request);
    accepted = !encoded.empty() &&
               ExchangeUntil(encoded, &response, deadline) &&
               AcceptState(response, state, deadline);
  } catch (...) {
    // Keep exceptions from allocation/decoding inside the native transport
    // boundary. The guard still erases any response bytes already received.
  }
  if (!response.empty()) {
    SecureZeroMemory(response.data(), response.size());
    response.clear();
  }
  if (!accepted) {
    // AcceptState can decode the OTP before its acknowledgement write fails.
    // Clear that partially accepted plaintext before retiring the transport.
    if (state != nullptr && state->commit_text.has_value() &&
        !state->commit_text->empty()) {
      SecureZeroMemory(state->commit_text->data(),
                       state->commit_text->size() * sizeof(wchar_t));
      state->commit_text.reset();
    }
    Disconnect();
    return false;
  }
  return true;
}

}  // namespace clipvault::ime
