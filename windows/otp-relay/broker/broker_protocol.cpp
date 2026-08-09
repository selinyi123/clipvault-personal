#include "broker_protocol.h"

#include "../../ime/common/pipe_peer_trust.h"

#include <algorithm>
#include <array>
#include <limits>
#include <string>

namespace clipvault::otp::broker {
namespace {

constexpr std::array<std::uint8_t, 4> kMagic{'C', 'V', 'O', 'B'};
constexpr std::size_t kHeaderSize = 8;

template <typename ByteContainer>
void SecureEraseWireBuffer(ByteContainer& bytes) noexcept {
  if (!bytes.empty()) {
    SecureZeroMemory(
        bytes.data(),
        bytes.size() * sizeof(typename ByteContainer::value_type));
  }
}

void AppendU32(std::vector<std::uint8_t>* output, std::uint32_t value) {
  output->push_back(static_cast<std::uint8_t>(value >> 24));
  output->push_back(static_cast<std::uint8_t>(value >> 16));
  output->push_back(static_cast<std::uint8_t>(value >> 8));
  output->push_back(static_cast<std::uint8_t>(value));
}

void AppendU64(std::vector<std::uint8_t>* output, std::uint64_t value) {
  for (int shift = 56; shift >= 0; shift -= 8) {
    output->push_back(static_cast<std::uint8_t>(value >> shift));
  }
}

template <std::size_t Size>
void AppendArray(std::vector<std::uint8_t>* output,
                 const std::array<std::uint8_t, Size>& value) {
  output->insert(output->end(), value.begin(), value.end());
}

void AppendHeader(std::vector<std::uint8_t>* output, BrokerOperation operation) {
  output->insert(output->end(), kMagic.begin(), kMagic.end());
  output->push_back(kBrokerProtocolVersion);
  output->push_back(static_cast<std::uint8_t>(operation));
  output->push_back(0);
  output->push_back(0);
}

bool ReadU32(const std::vector<std::uint8_t>& input, std::size_t* cursor,
             std::uint32_t* value) {
  if (input.size() - *cursor < 4) return false;
  *value = (static_cast<std::uint32_t>(input[*cursor]) << 24) |
           (static_cast<std::uint32_t>(input[*cursor + 1]) << 16) |
           (static_cast<std::uint32_t>(input[*cursor + 2]) << 8) |
           static_cast<std::uint32_t>(input[*cursor + 3]);
  *cursor += 4;
  return true;
}

bool ReadU64(const std::vector<std::uint8_t>& input, std::size_t* cursor,
             std::uint64_t* value) {
  if (input.size() - *cursor < 8) return false;
  std::uint64_t result = 0;
  for (int index = 0; index < 8; ++index) {
    result = (result << 8) | input[*cursor + index];
  }
  *cursor += 8;
  *value = result;
  return true;
}

template <std::size_t Size>
bool ReadArray(const std::vector<std::uint8_t>& input, std::size_t* cursor,
               std::array<std::uint8_t, Size>* value) {
  if (input.size() - *cursor < Size) return false;
  std::copy_n(input.begin() + static_cast<std::ptrdiff_t>(*cursor), Size,
              value->begin());
  *cursor += Size;
  return true;
}

bool ReadHeader(const std::vector<std::uint8_t>& input,
                BrokerOperation expected, std::size_t* cursor) {
  if (input.size() < kHeaderSize ||
      !std::equal(kMagic.begin(), kMagic.end(), input.begin()) ||
      input[4] != kBrokerProtocolVersion ||
      input[5] != static_cast<std::uint8_t>(expected) || input[6] != 0 ||
      input[7] != 0) {
    return false;
  }
  *cursor = kHeaderSize;
  return true;
}

void AppendContext(std::vector<std::uint8_t>* output,
                   const ContextBinding& context) {
  AppendU32(output, context.process_id);
  AppendU32(output, context.thread_id);
  AppendU64(output, context.window_handle);
  AppendArray(output, context.document_token);
  AppendArray(output, context.context_token);
}

bool ReadContext(const std::vector<std::uint8_t>& input, std::size_t* cursor,
                 ContextBinding* context) {
  return ReadU32(input, cursor, &context->process_id) &&
         ReadU32(input, cursor, &context->thread_id) &&
         ReadU64(input, cursor, &context->window_handle) &&
         ReadArray(input, cursor, &context->document_token) &&
         ReadArray(input, cursor, &context->context_token);
}

DWORD Remaining(ULONGLONG deadline_tick) noexcept {
  const ULONGLONG now = GetTickCount64();
  if (now >= deadline_tick) return 0;
  return static_cast<DWORD>(
      std::min<ULONGLONG>(deadline_tick - now, MAXDWORD));
}

bool FinishOverlapped(HANDLE pipe, OVERLAPPED* overlapped,
                      ULONGLONG deadline_tick, DWORD* transferred) {
  const DWORD wait = WaitForSingleObject(overlapped->hEvent,
                                         Remaining(deadline_tick));
  if (wait != WAIT_OBJECT_0) {
    CancelIoEx(pipe, overlapped);
    GetOverlappedResult(pipe, overlapped, transferred, TRUE);
    return false;
  }
  return GetOverlappedResult(pipe, overlapped, transferred, FALSE) != FALSE;
}

bool TransferExactUntil(HANDLE pipe, std::uint8_t* data, DWORD size,
                        ULONGLONG deadline_tick, bool write) {
  HANDLE event = CreateEventW(nullptr, TRUE, FALSE, nullptr);
  if (event == nullptr) return false;
  DWORD offset = 0;
  bool success = true;
  while (offset < size) {
    ResetEvent(event);
    OVERLAPPED overlapped{};
    overlapped.hEvent = event;
    DWORD transferred = 0;
    const BOOL completed =
        write ? WriteFile(pipe, data + offset, size - offset, &transferred,
                          &overlapped)
              : ReadFile(pipe, data + offset, size - offset, &transferred,
                         &overlapped);
    if (!completed &&
        (GetLastError() != ERROR_IO_PENDING ||
         !FinishOverlapped(pipe, &overlapped, deadline_tick, &transferred))) {
      success = false;
      break;
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

std::wstring TestSuffix() {
  wchar_t value[65]{};
  const DWORD length = GetEnvironmentVariableW(L"CLIPVAULT_OTP_TEST_NAMESPACE",
                                                value, std::size(value));
  if (length == 0 || length >= std::size(value)) return {};
  for (DWORD index = 0; index < length; ++index) {
    const wchar_t ch = value[index];
    if (!((ch >= L'a' && ch <= L'z') || (ch >= L'A' && ch <= L'Z') ||
          (ch >= L'0' && ch <= L'9') || ch == L'-' || ch == L'_')) {
      return {};
    }
  }
  return L"-" + std::wstring(value, length);
}

std::wstring ExpectedOtpBrokerServerPath() {
  using namespace clipvault::windows::trust;
  const std::wstring module_directory = ParentDirectory(CurrentModulePath());
  if (FileName(module_directory) != L"host-x64") {
    if (ExplicitUnsignedTestTrustEnabled(TestSuffix())) {
      return JoinPath(module_directory, L"ClipVaultOtpBroker.exe");
    }
    return {};
  }
  const std::wstring package_directory = ParentDirectory(module_directory);
  return JoinPath(JoinPath(package_directory, L"otp-broker"),
                  L"ClipVaultOtpBroker.exe");
}

}  // namespace

std::vector<std::uint8_t> EncodeOffer(const OpaqueEnvelope& envelope) {
  if (envelope.version != 1 || envelope.algorithm != 1 ||
      envelope.sequence == 0 || envelope.ciphertext.size() < 4 ||
      envelope.ciphertext.size() > 8) {
    return {};
  }
  std::vector<std::uint8_t> output;
  output.reserve(128);
  AppendHeader(&output, BrokerOperation::kOffer);
  output.push_back(envelope.version);
  output.push_back(envelope.algorithm);
  AppendArray(&output, envelope.session_epoch);
  AppendArray(&output, envelope.event_id);
  AppendArray(&output, envelope.sender_device);
  AppendArray(&output, envelope.target_device);
  AppendU64(&output, envelope.sequence);
  AppendU64(&output, envelope.issued_at_ms);
  AppendU64(&output, envelope.expires_at_ms);
  AppendArray(&output, envelope.nonce);
  output.push_back(static_cast<std::uint8_t>(envelope.ciphertext.size()));
  output.insert(output.end(), envelope.ciphertext.begin(),
                envelope.ciphertext.end());
  AppendArray(&output, envelope.authentication_tag);
  return output;
}

bool DecodeOffer(const std::vector<std::uint8_t>& frame,
                 OpaqueEnvelope* envelope) {
  if (envelope == nullptr) return false;
  std::size_t cursor = 0;
  OpaqueEnvelope decoded;
  if (!ReadHeader(frame, BrokerOperation::kOffer, &cursor) ||
      frame.size() - cursor < 2) {
    return false;
  }
  decoded.version = frame[cursor++];
  decoded.algorithm = frame[cursor++];
  if (decoded.version != 1 || decoded.algorithm != 1 ||
      !ReadArray(frame, &cursor, &decoded.session_epoch) ||
      !ReadArray(frame, &cursor, &decoded.event_id) ||
      !ReadArray(frame, &cursor, &decoded.sender_device) ||
      !ReadArray(frame, &cursor, &decoded.target_device) ||
      !ReadU64(frame, &cursor, &decoded.sequence) ||
      !ReadU64(frame, &cursor, &decoded.issued_at_ms) ||
      !ReadU64(frame, &cursor, &decoded.expires_at_ms) ||
      !ReadArray(frame, &cursor, &decoded.nonce) || cursor >= frame.size()) {
    return false;
  }
  const std::size_t ciphertext_size = frame[cursor++];
  if (ciphertext_size < 4 || ciphertext_size > 8 ||
      frame.size() - cursor != ciphertext_size + decoded.authentication_tag.size()) {
    return false;
  }
  decoded.ciphertext.assign(
      frame.begin() + static_cast<std::ptrdiff_t>(cursor),
      frame.begin() + static_cast<std::ptrdiff_t>(cursor + ciphertext_size));
  cursor += ciphertext_size;
  if (!ReadArray(frame, &cursor, &decoded.authentication_tag) ||
      cursor != frame.size()) {
    if (!decoded.ciphertext.empty())
      SecureZeroMemory(decoded.ciphertext.data(), decoded.ciphertext.size());
    return false;
  }
  *envelope = std::move(decoded);
  return true;
}

std::vector<std::uint8_t> EncodeArm(const ArmRequest& request) {
  std::vector<std::uint8_t> output;
  AppendHeader(&output, BrokerOperation::kArm);
  AppendArray(&output, request.event_id);
  AppendContext(&output, request.context);
  return output;
}

bool DecodeArm(const std::vector<std::uint8_t>& frame, ArmRequest* request) {
  if (request == nullptr) return false;
  std::size_t cursor = 0;
  ArmRequest decoded;
  if (!ReadHeader(frame, BrokerOperation::kArm, &cursor) ||
      !ReadArray(frame, &cursor, &decoded.event_id) ||
      !ReadContext(frame, &cursor, &decoded.context) || cursor != frame.size()) {
    return false;
  }
  *request = decoded;
  return true;
}

std::vector<std::uint8_t> EncodeArmLatest(const ContextBinding& context) {
  std::vector<std::uint8_t> output;
  AppendHeader(&output, BrokerOperation::kArmLatest);
  AppendContext(&output, context);
  return output;
}

bool DecodeArmLatest(const std::vector<std::uint8_t>& frame,
                     ContextBinding* context) {
  if (context == nullptr) return false;
  std::size_t cursor = 0;
  ContextBinding decoded;
  if (!ReadHeader(frame, BrokerOperation::kArmLatest, &cursor) ||
      !ReadContext(frame, &cursor, &decoded) || cursor != frame.size()) {
    return false;
  }
  *context = decoded;
  return true;
}

std::vector<std::uint8_t> EncodeConsume(const ConsumeRequest& request) {
  std::vector<std::uint8_t> output;
  AppendHeader(&output, BrokerOperation::kConsume);
  AppendArray(&output, request.claim_id);
  AppendContext(&output, request.context);
  return output;
}

bool DecodeConsume(const std::vector<std::uint8_t>& frame,
                   ConsumeRequest* request) {
  if (request == nullptr) return false;
  std::size_t cursor = 0;
  ConsumeRequest decoded;
  if (!ReadHeader(frame, BrokerOperation::kConsume, &cursor) ||
      !ReadArray(frame, &cursor, &decoded.claim_id) ||
      !ReadContext(frame, &cursor, &decoded.context) || cursor != frame.size()) {
    return false;
  }
  *request = decoded;
  return true;
}

std::vector<std::uint8_t> EncodeDismiss(const crypto::UuidBytes& event_id) {
  std::vector<std::uint8_t> output;
  AppendHeader(&output, BrokerOperation::kDismiss);
  AppendArray(&output, event_id);
  return output;
}

bool DecodeDismiss(const std::vector<std::uint8_t>& frame,
                   crypto::UuidBytes* event_id) {
  if (event_id == nullptr) return false;
  std::size_t cursor = 0;
  return ReadHeader(frame, BrokerOperation::kDismiss, &cursor) &&
         ReadArray(frame, &cursor, event_id) && cursor == frame.size();
}

std::vector<std::uint8_t> EncodeRevokeSession(
    const crypto::UuidBytes& session_epoch) {
  std::vector<std::uint8_t> output;
  AppendHeader(&output, BrokerOperation::kRevokeSession);
  AppendArray(&output, session_epoch);
  return output;
}

bool DecodeRevokeSession(const std::vector<std::uint8_t>& frame,
                         crypto::UuidBytes* session_epoch) {
  if (session_epoch == nullptr) return false;
  std::size_t cursor = 0;
  return ReadHeader(frame, BrokerOperation::kRevokeSession, &cursor) &&
         ReadArray(frame, &cursor, session_epoch) && cursor == frame.size();
}

std::vector<std::uint8_t> EncodeResponse(const BrokerResponse& response) {
  const bool consumed = response.status == BrokerStatus::kConsumed;
  if ((consumed &&
       (response.secret.size() < 4 || response.secret.size() > 8)) ||
      (!consumed && !response.secret.empty())) {
    return {};
  }
  std::vector<std::uint8_t> output;
  AppendHeader(&output, BrokerOperation::kResponse);
  output.push_back(static_cast<std::uint8_t>(response.status));
  AppendArray(&output, response.claim_id);
  output.push_back(static_cast<std::uint8_t>(response.secret.size()));
  output.insert(output.end(), response.secret.begin(), response.secret.end());
  return output;
}

bool DecodeResponse(const std::vector<std::uint8_t>& frame,
                    BrokerResponse* response) {
  if (response == nullptr) return false;
  // Callers may reuse one response object across pipe exchanges. Never leave
  // a previously consumed OTP or claim reachable when the next frame fails
  // strict decoding before assignment.
  SecureEraseWireBuffer(response->claim_id);
  SecureEraseWireBuffer(response->secret);
  response->secret.clear();
  response->status = BrokerStatus::kRejected;
  std::size_t cursor = 0;
  BrokerResponse decoded;
  const auto reject = [&decoded]() {
    SecureEraseWireBuffer(decoded.claim_id);
    SecureEraseWireBuffer(decoded.secret);
    decoded.secret.clear();
    return false;
  };
  if (!ReadHeader(frame, BrokerOperation::kResponse, &cursor) ||
      cursor >= frame.size()) {
    return reject();
  }
  const auto raw_status = frame[cursor++];
  if (raw_status < static_cast<std::uint8_t>(BrokerStatus::kAccepted) ||
      raw_status > static_cast<std::uint8_t>(
                       BrokerStatus::kRotationRequired) ||
      !ReadArray(frame, &cursor, &decoded.claim_id) || cursor >= frame.size()) {
    return reject();
  }
  decoded.status = static_cast<BrokerStatus>(raw_status);
  const std::size_t secret_size = frame[cursor++];
  const bool consumed = decoded.status == BrokerStatus::kConsumed;
  if (secret_size > 8 || frame.size() - cursor != secret_size ||
      (consumed && secret_size < 4) || (!consumed && secret_size != 0)) {
    return reject();
  }
  decoded.secret.assign(frame.begin() + static_cast<std::ptrdiff_t>(cursor),
                        frame.end());
  *response = std::move(decoded);
  SecureEraseWireBuffer(decoded.claim_id);
  SecureEraseWireBuffer(decoded.secret);
  decoded.secret.clear();
  return true;
}

bool ReadBrokerFrameUntil(HANDLE pipe, std::vector<std::uint8_t>* payload,
                          ULONGLONG deadline_tick) {
  if (payload == nullptr) return false;
  std::array<std::uint8_t, 4> prefix{};
  if (!TransferExactUntil(pipe, prefix.data(), static_cast<DWORD>(prefix.size()),
                          deadline_tick, false)) {
    return false;
  }
  const std::uint32_t size = (static_cast<std::uint32_t>(prefix[0]) << 24) |
                             (static_cast<std::uint32_t>(prefix[1]) << 16) |
                             (static_cast<std::uint32_t>(prefix[2]) << 8) |
                             static_cast<std::uint32_t>(prefix[3]);
  if (size == 0 || size > kMaximumBrokerFrameBytes) return false;
  payload->resize(size);
  return TransferExactUntil(pipe, payload->data(), size, deadline_tick, false);
}

bool WriteBrokerFrameUntil(HANDLE pipe,
                           const std::vector<std::uint8_t>& payload,
                           ULONGLONG deadline_tick) {
  if (payload.empty() || payload.size() > kMaximumBrokerFrameBytes ||
      payload.size() > std::numeric_limits<std::uint32_t>::max()) {
    return false;
  }
  const auto size = static_cast<std::uint32_t>(payload.size());
  std::array<std::uint8_t, 4> prefix{
      static_cast<std::uint8_t>(size >> 24),
      static_cast<std::uint8_t>(size >> 16),
      static_cast<std::uint8_t>(size >> 8), static_cast<std::uint8_t>(size)};
  return TransferExactUntil(pipe, prefix.data(), static_cast<DWORD>(prefix.size()),
                            deadline_tick, true) &&
         TransferExactUntil(pipe, const_cast<std::uint8_t*>(payload.data()), size,
                            deadline_tick, true);
}

std::wstring BrokerPipeNameForCurrentSession() {
  DWORD session_id = 0;
  ProcessIdToSessionId(GetCurrentProcessId(), &session_id);
  return L"\\\\.\\pipe\\ClipVaultOtpBrokerV1-" +
         std::to_wstring(session_id) + TestSuffix();
}

BrokerPipeClient::~BrokerPipeClient() { Close(); }

bool BrokerPipeClient::ConnectUntil(ULONGLONG deadline_tick) {
  Close();
  const auto pipe_name = BrokerPipeNameForCurrentSession();
  do {
    pipe_ = CreateFileW(
        pipe_name.c_str(), GENERIC_READ | GENERIC_WRITE, 0, nullptr,
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OVERLAPPED | SECURITY_SQOS_PRESENT |
            SECURITY_IDENTIFICATION,
        nullptr);
    if (pipe_ != INVALID_HANDLE_VALUE) {
      if (clipvault::windows::trust::VerifyNamedPipeServer(
              pipe_, ExpectedOtpBrokerServerPath(), TestSuffix())) {
        return true;
      }
      Close();
      return false;
    }
    const DWORD error = GetLastError();
    if (error != ERROR_FILE_NOT_FOUND && error != ERROR_PIPE_BUSY) return false;
    const DWORD remaining = Remaining(deadline_tick);
    if (remaining == 0) return false;
    if (error == ERROR_PIPE_BUSY) {
      WaitNamedPipeW(pipe_name.c_str(), std::min<DWORD>(remaining, 25));
    } else {
      Sleep(std::min<DWORD>(remaining, 5));
    }
  } while (GetTickCount64() < deadline_tick);
  return false;
}

bool BrokerPipeClient::ExchangeUntil(const std::vector<std::uint8_t>& request,
                                     BrokerResponse* response,
                                     ULONGLONG deadline_tick) {
  if (response != nullptr) {
    SecureEraseWireBuffer(response->claim_id);
    SecureEraseWireBuffer(response->secret);
    response->secret.clear();
    response->status = BrokerStatus::kRejected;
  }
  std::vector<std::uint8_t> frame;
  const bool success =
      pipe_ != INVALID_HANDLE_VALUE && response != nullptr && !request.empty() &&
      WriteBrokerFrameUntil(pipe_, request, deadline_tick) &&
      ReadBrokerFrameUntil(pipe_, &frame, deadline_tick) &&
      DecodeResponse(frame, response);
  // A consumed response frame contains the one-use OTP plaintext in addition
  // to BrokerResponse::secret.  Wipe this raw duplicate on every path.
  SecureEraseWireBuffer(frame);
  if (!success) {
    Close();
    return false;
  }
  return true;
}

void BrokerPipeClient::Close() noexcept {
  if (pipe_ != INVALID_HANDLE_VALUE) {
    CancelIoEx(pipe_, nullptr);
    CloseHandle(pipe_);
    pipe_ = INVALID_HANDLE_VALUE;
  }
}

}  // namespace clipvault::otp::broker
