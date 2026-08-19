#pragma once

#include <windows.h>

#include <array>
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace clipvault::ime {

inline constexpr std::uint32_t kProtocolVersion = 2;
inline constexpr std::uint32_t kMaximumFrameBytes = 1'048'576;
inline constexpr DWORD kDefaultRpcBudgetMilliseconds = 40;

enum class FrameKind : std::uint32_t {
  kClientHello = 10,
  kHostHello = 11,
  kStartSessionRequest = 20,
  kProcessKeyRequest = 21,
  kSelectCandidateRequest = 22,
  kPageCandidatesRequest = 23,
  kCommitCompositionRequest = 24,
  kCancelCompositionRequest = 25,
  kSelectSnapshotCandidateRequest = 28,
  kInsertOtpRequest = 29,
  kEngineState = 30,
  kErrorResponse = 32,
};

struct KeyEvent final {
  std::uint32_t virtual_key = 0;
  std::wstring text;
  bool key_down = true;
  bool repeat = false;
  bool shift = false;
  bool control = false;
  bool alt = false;
};

enum class InputFieldKind : std::uint32_t {
  kUnknown = 0,
  kText = 1,
  kMultiline = 2,
  kEmail = 3,
  kUrl = 4,
  kNumber = 5,
  kPhone = 6,
  kPassword = 7,
  kOtp = 8,
};

enum class InputAction : std::uint32_t {
  kNone = 0,
  kEnter = 1,
  kDone = 2,
  kGo = 3,
  kNext = 4,
  kSearch = 5,
  kSend = 6,
};

struct InputContext final {
  InputFieldKind field_kind = InputFieldKind::kUnknown;
  InputAction action = InputAction::kNone;
  bool incognito = true;
  bool learning_allowed = false;
  bool clipvault_allowed = false;
  std::string app_scope;

  bool operator==(const InputContext&) const = default;
};

struct StartSessionRequest final {
  std::string host_instance_id;
  std::string session_id;
  std::uint64_t request_seq = 1;
  InputContext context;
};

struct ProcessKeyRequest final {
  std::string host_instance_id;
  std::string session_id;
  std::uint64_t request_seq = 0;
  std::uint64_t expected_revision = 0;
  KeyEvent event;
};

struct SelectCandidateRequest final {
  std::string host_instance_id;
  std::string session_id;
  std::uint64_t request_seq = 0;
  std::uint64_t expected_revision = 0;
  std::string candidate_id;
};

struct PageCandidatesRequest final {
  std::string host_instance_id;
  std::string session_id;
  std::uint64_t request_seq = 0;
  std::uint64_t expected_revision = 0;
  bool backward = false;
};

struct CompositionCommandRequest final {
  std::string host_instance_id;
  std::string session_id;
  std::uint64_t request_seq = 0;
  std::uint64_t expected_revision = 0;
};

struct SelectSnapshotCandidateRequest final {
  std::string host_instance_id;
  std::string session_id;
  std::uint64_t request_seq = 0;
  std::uint64_t expected_revision = 0;
  std::string publisher_epoch;
  std::uint64_t generation = 0;
  std::string candidate_id;
};

struct OtpContextBinding final {
  std::uint32_t process_id = 0;
  std::uint32_t thread_id = 0;
  std::uint64_t window_handle = 0;
  std::array<std::uint8_t, 16> document_token{};
  std::array<std::uint8_t, 16> context_token{};

  bool operator==(const OtpContextBinding&) const = default;
};

struct InsertOtpRequest final {
  std::string host_instance_id;
  std::string session_id;
  std::uint64_t request_seq = 0;
  std::uint64_t expected_revision = 0;
  OtpContextBinding context;
};

struct EngineCandidate final {
  std::string candidate_id;
  std::wstring text;
  std::wstring comment;
};

struct SnapshotCandidate final {
  std::string candidate_id;
  std::uint32_t source = 0;
  std::wstring label;
  std::wstring text;
};

struct SnapshotSurface final {
  std::string publisher_epoch;
  std::uint64_t generation = 0;
  std::uint64_t expires_at_ms = 0;
  std::vector<SnapshotCandidate> candidates;

  [[nodiscard]] bool empty() const noexcept { return candidates.empty(); }
};

struct EngineState final {
  std::string host_instance_id;
  std::string session_id;
  std::uint64_t ack_request_seq = 0;
  std::uint64_t revision = 0;
  bool handled = false;
  std::wstring preedit;
  std::uint32_t caret_utf16 = 0;
  std::optional<std::wstring> commit_text;
  bool composition_active = false;
  std::uint32_t mode = 1;
  std::vector<EngineCandidate> candidates;
  std::uint32_t page_index = 0;
  std::uint32_t page_size = 0;
  bool has_previous_page = false;
  bool has_next_page = false;
  SnapshotSurface snapshot_surface;
};

std::vector<std::uint8_t> EncodeClientHello(const std::string& client_instance_id);
std::vector<std::uint8_t> EncodeHostHello(const std::string& host_instance_id);
std::vector<std::uint8_t> EncodeStartSession(const StartSessionRequest& request);
std::vector<std::uint8_t> EncodeProcessKey(const ProcessKeyRequest& request);
std::vector<std::uint8_t> EncodeSelectCandidate(const SelectCandidateRequest& request);
std::vector<std::uint8_t> EncodePageCandidates(const PageCandidatesRequest& request);
std::vector<std::uint8_t> EncodeCommitComposition(
    const CompositionCommandRequest& request);
std::vector<std::uint8_t> EncodeCancelComposition(
    const CompositionCommandRequest& request);
std::vector<std::uint8_t> EncodeSelectSnapshotCandidate(
    const SelectSnapshotCandidateRequest& request);
std::vector<std::uint8_t> EncodeInsertOtp(const InsertOtpRequest& request);
std::vector<std::uint8_t> EncodeEngineState(const EngineState& state);

bool DecodeClientHello(const std::vector<std::uint8_t>& frame,
                       std::string* client_instance_id);
bool DecodeHostHello(const std::vector<std::uint8_t>& frame,
                     std::string* host_instance_id);
bool DecodeStartSession(const std::vector<std::uint8_t>& frame,
                        StartSessionRequest* request);
bool DecodeProcessKey(const std::vector<std::uint8_t>& frame,
                      ProcessKeyRequest* request);
bool DecodeSelectCandidate(const std::vector<std::uint8_t>& frame,
                           SelectCandidateRequest* request);
bool DecodePageCandidates(const std::vector<std::uint8_t>& frame,
                          PageCandidatesRequest* request);
bool DecodeCommitComposition(const std::vector<std::uint8_t>& frame,
                             CompositionCommandRequest* request);
bool DecodeCancelComposition(const std::vector<std::uint8_t>& frame,
                             CompositionCommandRequest* request);
bool DecodeSelectSnapshotCandidate(
    const std::vector<std::uint8_t>& frame,
    SelectSnapshotCandidateRequest* request);
bool DecodeInsertOtp(const std::vector<std::uint8_t>& frame,
                     InsertOtpRequest* request);
bool DecodeEngineState(const std::vector<std::uint8_t>& frame, EngineState* state);

bool ReadFrame(HANDLE pipe, std::vector<std::uint8_t>* payload);
bool WriteFrame(HANDLE pipe, const std::vector<std::uint8_t>& payload);
// The deadline variants require a handle opened with FILE_FLAG_OVERLAPPED.
// deadline_tick is an absolute GetTickCount64() value shared by the whole
// framed operation, including prefix and payload.
bool ReadFrameUntil(HANDLE pipe, std::vector<std::uint8_t>* payload,
                    ULONGLONG deadline_tick);
bool WriteFrameUntil(HANDLE pipe, const std::vector<std::uint8_t>& payload,
                     ULONGLONG deadline_tick);

std::string NewOpaqueId();
std::wstring PipeNameForCurrentSession();
std::wstring LocalTestNamespaceSuffix();

class PipeEngineClient final {
 public:
  PipeEngineClient() = default;
  ~PipeEngineClient();
  PipeEngineClient(const PipeEngineClient&) = delete;
  PipeEngineClient& operator=(const PipeEngineClient&) = delete;

  bool Connect(DWORD wait_milliseconds);
  void Disconnect() noexcept;
  [[nodiscard]] bool connected() const noexcept { return pipe_ != INVALID_HANDLE_VALUE; }
  [[nodiscard]] const std::string& host_instance_id() const noexcept {
    return host_instance_id_;
  }

  bool StartSession(EngineState* state,
                    DWORD budget_milliseconds = kDefaultRpcBudgetMilliseconds);
  bool StartSession(const InputContext& context, EngineState* state,
                    DWORD budget_milliseconds = kDefaultRpcBudgetMilliseconds);
  bool ProcessKey(const KeyEvent& event, EngineState* state,
                  DWORD budget_milliseconds = kDefaultRpcBudgetMilliseconds);
  bool SelectCandidate(
      const std::string& candidate_id, EngineState* state,
      DWORD budget_milliseconds = kDefaultRpcBudgetMilliseconds);
  bool PageCandidates(bool backward, EngineState* state,
                      DWORD budget_milliseconds = kDefaultRpcBudgetMilliseconds);
  bool CommitComposition(
      EngineState* state,
      DWORD budget_milliseconds = kDefaultRpcBudgetMilliseconds);
  bool CancelComposition(
      EngineState* state,
      DWORD budget_milliseconds = kDefaultRpcBudgetMilliseconds);
  bool SelectSnapshotCandidate(
      const std::string& publisher_epoch, std::uint64_t generation,
      const std::string& candidate_id, EngineState* state,
      DWORD budget_milliseconds = kDefaultRpcBudgetMilliseconds);
  bool InsertOtp(
      const OtpContextBinding& context, EngineState* state,
      DWORD budget_milliseconds = kDefaultRpcBudgetMilliseconds);

 private:
  bool ExchangeUntil(const std::vector<std::uint8_t>& request,
                     std::vector<std::uint8_t>* response,
                     ULONGLONG deadline_tick);
  bool AcceptState(const std::vector<std::uint8_t>& response,
                   EngineState* state);
  HANDLE pipe_ = INVALID_HANDLE_VALUE;
  std::string host_instance_id_;
  std::string session_id_;
  std::uint64_t next_request_seq_ = 1;
  std::uint64_t revision_ = 0;
};

}  // namespace clipvault::ime
