#include "protocol.h"

#include <windows.h>

#include <iostream>

namespace {

bool Expect(bool condition, const char* label) {
  if (!condition) std::cerr << "FAILED: " << label << '\n';
  return condition;
}

}  // namespace

int main() {
  using namespace clipvault::ime;
  bool ok = true;

  const std::string client_id = "0123456789abcdef";
  std::string decoded_client;
  ok &= Expect(DecodeClientHello(EncodeClientHello(client_id), &decoded_client) &&
                   decoded_client == client_id,
               "ClientHello round trip");

  const std::string host_id = "fedcba9876543210";
  std::string decoded_host;
  ok &= Expect(DecodeHostHello(EncodeHostHello(host_id), &decoded_host) &&
                   decoded_host == host_id,
               "HostHello round trip");

  InputContext ordinary_context;
  ordinary_context.field_kind = InputFieldKind::kText;
  ordinary_context.action = InputAction::kDone;
  ordinary_context.incognito = false;
  ordinary_context.learning_allowed = true;
  ordinary_context.clipvault_allowed = true;
  StartSessionRequest start{host_id, "session-1", 1, ordinary_context};
  StartSessionRequest decoded_start;
  ok &= Expect(DecodeStartSession(EncodeStartSession(start), &decoded_start) &&
                   decoded_start.host_instance_id == host_id &&
                   decoded_start.session_id == start.session_id &&
                   decoded_start.request_seq == 1 &&
                   decoded_start.context == ordinary_context,
               "StartSession round trip");

  InputContext private_context;
  private_context.field_kind = InputFieldKind::kPassword;
  private_context.action = InputAction::kDone;
  private_context.incognito = true;
  StartSessionRequest private_start{host_id, "session-private", 1,
                                    private_context};
  ok &= Expect(DecodeStartSession(EncodeStartSession(private_start),
                                  &decoded_start) &&
                   decoded_start.context == private_context,
               "private StartSession round trip");
  private_start.context.learning_allowed = true;
  ok &= Expect(!DecodeStartSession(EncodeStartSession(private_start),
                                   &decoded_start),
               "password learning request rejected");
  InputContext unknown_context;
  StartSessionRequest unknown_start{host_id, "session-unknown", 1,
                                    unknown_context};
  ok &= Expect(DecodeStartSession(EncodeStartSession(unknown_start),
                                  &decoded_start) &&
                   decoded_start.context == unknown_context,
               "unknown field fails privacy closed");

  ProcessKeyRequest key{host_id, "session-1", 2, 0,
                        KeyEvent{'A', L"a", true, false, false, false, false}};
  ProcessKeyRequest decoded_key;
  ok &= Expect(DecodeProcessKey(EncodeProcessKey(key), &decoded_key) &&
                   decoded_key.event.text == L"a" && decoded_key.request_seq == 2,
               "ProcessKey round trip");

  SelectCandidateRequest select{host_id, "session-1", 3, 1, "candidate-7"};
  SelectCandidateRequest decoded_select;
  ok &= Expect(DecodeSelectCandidate(EncodeSelectCandidate(select), &decoded_select) &&
                   decoded_select.candidate_id == select.candidate_id &&
                   decoded_select.expected_revision == 1,
               "SelectCandidate stable ID round trip");

  PageCandidatesRequest page{host_id, "session-1", 4, 2, true};
  PageCandidatesRequest decoded_page;
  ok &= Expect(DecodePageCandidates(EncodePageCandidates(page), &decoded_page) &&
                   decoded_page.backward && decoded_page.request_seq == 4,
               "PageCandidates round trip");

  CompositionCommandRequest command{host_id, "session-1", 5, 3};
  CompositionCommandRequest decoded_command;
  ok &= Expect(DecodeCommitComposition(EncodeCommitComposition(command),
                                       &decoded_command) &&
                   decoded_command.request_seq == 5 &&
                   decoded_command.expected_revision == 3,
               "CommitComposition round trip");
  ok &= Expect(DecodeCancelComposition(EncodeCancelComposition(command),
                                       &decoded_command) &&
                   decoded_command.session_id == command.session_id,
               "CancelComposition round trip");

  const std::string publisher_epoch =
      "01234567-89ab-4def-8abc-0123456789ab";
  SelectSnapshotCandidateRequest snapshot_select{
      host_id, "session-1", 6, 4, publisher_epoch, 7, "memory:address"};
  SelectSnapshotCandidateRequest decoded_snapshot_select;
  ok &= Expect(
      DecodeSelectSnapshotCandidate(
          EncodeSelectSnapshotCandidate(snapshot_select),
          &decoded_snapshot_select) &&
          decoded_snapshot_select.publisher_epoch == publisher_epoch &&
          decoded_snapshot_select.generation == 7 &&
          decoded_snapshot_select.candidate_id == "memory:address",
      "Snapshot candidate stable identity round trip");

  OtpContextBinding otp_context{
      .process_id = 4242,
      .thread_id = 77,
      .window_handle = 0x12345678,
      .document_token = {0x55, 0x55, 0x55, 0x55, 0x55, 0x55, 0x45, 0x55,
                         0x85, 0x55, 0x55, 0x55, 0x55, 0x55, 0x55, 0x55},
      .context_token = {0x66, 0x66, 0x66, 0x66, 0x66, 0x66, 0x46, 0x66,
                        0x86, 0x66, 0x66, 0x66, 0x66, 0x66, 0x66, 0x66},
  };
  InsertOtpRequest otp_request{host_id, "session-1", 7, 5, otp_context};
  InsertOtpRequest decoded_otp;
  ok &= Expect(DecodeInsertOtp(EncodeInsertOtp(otp_request), &decoded_otp) &&
                   decoded_otp.host_instance_id == host_id &&
                   decoded_otp.session_id == "session-1" &&
                   decoded_otp.request_seq == 7 &&
                   decoded_otp.expected_revision == 5 &&
                   decoded_otp.context == otp_context,
               "OTP process/thread/window/document/context binding round trip");
  otp_request.context.document_token[6] = 0;
  ok &= Expect(EncodeInsertOtp(otp_request).empty(),
               "non-UUID OTP context token rejected");

  EngineState state;
  state.host_instance_id = host_id;
  state.session_id = "session-1";
  state.ack_request_seq = 3;
  state.revision = 2;
  state.handled = true;
  state.preedit = L"ni\U0001F642";
  state.caret_utf16 = static_cast<std::uint32_t>(state.preedit.size());
  state.composition_active = true;
  state.mode = 3;
  state.candidates.push_back({"candidate-7", L"你好", L"ni hao"});
  state.page_index = 2;
  state.page_size = 5;
  state.has_previous_page = true;
  state.has_next_page = true;
  state.snapshot_surface.publisher_epoch = publisher_epoch;
  state.snapshot_surface.generation = 7;
  state.snapshot_surface.expires_at_ms = 4'000'000'000'000ULL;
  state.snapshot_surface.candidates.push_back(
      {"memory:address", 1, L"Address", L"123 Example Street"});
  EngineState decoded_state;
  ok &= Expect(DecodeEngineState(EncodeEngineState(state), &decoded_state) &&
                   decoded_state.preedit == state.preedit &&
                   decoded_state.caret_utf16 == state.caret_utf16 &&
                   decoded_state.candidates.size() == 1 &&
                   decoded_state.candidates.front().candidate_id == "candidate-7" &&
                   decoded_state.candidates.front().text == L"你好" &&
                   decoded_state.page_index == 2 && decoded_state.page_size == 5 &&
                   decoded_state.has_previous_page && decoded_state.has_next_page &&
                   decoded_state.snapshot_surface.publisher_epoch ==
                       publisher_epoch &&
                   decoded_state.snapshot_surface.generation == 7 &&
                   decoded_state.snapshot_surface.candidates.size() == 1 &&
                   decoded_state.snapshot_surface.candidates.front().text ==
                       L"123 Example Street",
               "EngineState UTF-16 round trip");

  auto duplicate_snapshot = state;
  duplicate_snapshot.snapshot_surface.candidates.push_back(
      duplicate_snapshot.snapshot_surface.candidates.front());
  ok &= Expect(EncodeEngineState(duplicate_snapshot).empty(),
               "duplicate snapshot candidate ID rejected");

  auto malformed = EncodeHostHello(host_id);
  malformed.push_back(0x80);
  ok &= Expect(!DecodeHostHello(malformed, &decoded_host),
               "trailing malformed protobuf byte rejected");

  return ok ? 0 : 1;
}
