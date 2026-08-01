#include "protocol.h"

#include <windows.h>

#include <cwctype>
#include <iostream>
#include <string>
#include <vector>

namespace {

std::wstring Quote(const std::wstring& value) { return L"\"" + value + L"\""; }

bool Expect(bool condition, const char* label) {
  if (!condition) std::cerr << "FAILED: " << label << '\n';
  return condition;
}

bool Exchange(HANDLE pipe, const std::vector<std::uint8_t>& request,
              std::vector<std::uint8_t>* response) {
  return !request.empty() && clipvault::ime::WriteFrame(pipe, request) &&
         clipvault::ime::ReadFrame(pipe, response);
}

bool Acknowledge(HANDLE pipe, const std::string& host,
                 const std::string& session, std::uint64_t sequence) {
  return clipvault::ime::WriteFrame(
      pipe, clipvault::ime::EncodeResponseAck(
                clipvault::ime::ResponseAck{host, session, sequence}));
}

}  // namespace

int wmain(int argc, wchar_t** argv) {
  using namespace clipvault::ime;
  if (argc != 2) return 2;
  const std::wstring test_namespace =
      L"engine-v2-" + std::to_wstring(GetCurrentProcessId()) + L"-" +
      std::to_wstring(GetTickCount64());
  if (!SetEnvironmentVariableW(L"CLIPVAULT_IME_TEST_NAMESPACE",
                               test_namespace.c_str())) {
    return 3;
  }

  std::wstring command = Quote(argv[1]) + L" --once --echo";
  STARTUPINFOW startup{sizeof(STARTUPINFOW)};
  PROCESS_INFORMATION process{};
  if (!CreateProcessW(argv[1], command.data(), nullptr, nullptr, FALSE,
                      CREATE_NO_WINDOW, nullptr, nullptr, &startup, &process)) {
    return 4;
  }

  HANDLE pipe = INVALID_HANDLE_VALUE;
  const ULONGLONG connect_deadline = GetTickCount64() + 3000;
  do {
    pipe = CreateFileW(PipeNameForCurrentSession().c_str(),
                       GENERIC_READ | GENERIC_WRITE, 0, nullptr, OPEN_EXISTING,
                       FILE_ATTRIBUTE_NORMAL, nullptr);
    if (pipe != INVALID_HANDLE_VALUE) break;
    Sleep(5);
  } while (GetTickCount64() < connect_deadline);

  bool ok = Expect(pipe != INVALID_HANDLE_VALUE, "connect raw Engine V2 pipe");
  const std::string client_id = "engine-v2-semantics-client";
  std::vector<std::uint8_t> response;
  std::string host;
  ok = ok && Exchange(pipe, EncodeClientHello(client_id), &response) &&
       DecodeHostHello(response, &host) && !host.empty();

  const std::string session = "engine-v2-semantics-session";
  InputContext context;
  context.field_kind = InputFieldKind::kText;
  context.incognito = false;
  context.learning_allowed = true;
  context.clipvault_allowed = true;
  const auto start = EncodeStartSession(
      StartSessionRequest{host, session, 1, context});
  std::vector<std::uint8_t> start_first;
  std::vector<std::uint8_t> start_duplicate;
  EngineState state;
  ok = ok && Exchange(pipe, start, &start_first) &&
       DecodeEngineState(start_first, &state) && state.revision == 0 &&
       state.ack_request_seq == 1 && Exchange(pipe, start, &start_duplicate) &&
       Expect(start_first == start_duplicate,
              "ENG2-V003 duplicate Start returns cached bytes") &&
       Acknowledge(pipe, host, session, 1);

  const auto set_option = EncodeSetOption(
      SetOptionRequest{host, session, 2, 0, "ascii_mode", false});
  ok = ok && Exchange(pipe, set_option, &response) &&
       DecodeEngineState(response, &state) && state.ack_request_seq == 2 &&
       state.revision == 1 && Acknowledge(pipe, host, session, 2);

  std::uint64_t sequence = 3;
  for (const wchar_t value : std::wstring(L"ab")) {
    KeyEvent event;
    event.virtual_key = static_cast<std::uint32_t>(std::towupper(value));
    event.text.assign(1, value);
    const auto key = EncodeProcessKey(
        ProcessKeyRequest{host, session, sequence, state.revision, event});
    ok = ok && Exchange(pipe, key, &response) &&
         DecodeEngineState(response, &state) &&
         state.ack_request_seq == sequence &&
         Acknowledge(pipe, host, session, sequence);
    ++sequence;
  }

  const auto commit = EncodeCommitComposition(CompositionCommandRequest{
      host, session, sequence, state.revision});
  std::vector<std::uint8_t> commit_first;
  std::vector<std::uint8_t> commit_duplicate;
  ok = ok && Exchange(pipe, commit, &commit_first) &&
       DecodeEngineState(commit_first, &state) &&
       state.commit_text == L"ab" && state.ack_request_seq == sequence &&
       Exchange(pipe, commit, &commit_duplicate) &&
       Expect(commit_first == commit_duplicate,
              "ENG2-V003 duplicate commit returns cached transition");

  ResponseProjectionLedger projection;
  int editor_commit_count = 0;
  ok = ok && projection.Begin(host, session);
  for (std::uint64_t accepted = 1; ok && accepted <= sequence; ++accepted) {
    const auto reserved = projection.Reserve(host, session, accepted);
    ok = reserved == ResponseReservation::kReserved;
    if (accepted == sequence && reserved == ResponseReservation::kReserved)
      ++editor_commit_count;
  }
  if (projection.Reserve(host, session, sequence) ==
      ResponseReservation::kReserved) {
    ++editor_commit_count;
  }
  ok = ok && Expect(editor_commit_count == 1,
                    "ENG2-V003 cached commit projected at most once") &&
       projection.live_session_count() == 1 &&
       Acknowledge(pipe, host, session, sequence);

  ++sequence;
  const auto end =
      EncodeEndSession(EndSessionRequest{host, session, sequence});
  std::vector<std::uint8_t> end_first;
  std::vector<std::uint8_t> end_duplicate;
  SessionEnded ended;
  ok = ok && Exchange(pipe, end, &end_first) &&
       DecodeSessionEnded(end_first, &ended) &&
       ended.ack_request_seq == sequence &&
       Exchange(pipe, end, &end_duplicate) &&
       Expect(end_first == end_duplicate,
              "ENG2-V008 duplicate EndSession is idempotent") &&
       Acknowledge(pipe, host, session, sequence);
  projection.End();
  ok = ok && Expect(projection.live_session_count() == 0,
                    "ENG2-V008 client ledger cleared on EndSession");

  KeyEvent stale_event;
  stale_event.virtual_key = 'Z';
  stale_event.text = L"z";
  const auto stale = EncodeProcessKey(ProcessKeyRequest{
      host, session, sequence + 1, state.revision, stale_event});
  std::vector<std::uint8_t> unexpected;
  const bool stale_received =
      WriteFrame(pipe, stale) && ReadFrame(pipe, &unexpected);
  ok = ok && Expect(!stale_received,
                    "ENG2-V008 ended session rejects old mutations");

  if (pipe != INVALID_HANDLE_VALUE) CloseHandle(pipe);
  const DWORD wait = WaitForSingleObject(process.hProcess, 5000);
  if (wait == WAIT_TIMEOUT) {
    TerminateProcess(process.hProcess, 5);
    WaitForSingleObject(process.hProcess, 3000);
  }
  DWORD exit_code = 99;
  GetExitCodeProcess(process.hProcess, &exit_code);
  CloseHandle(process.hThread);
  CloseHandle(process.hProcess);
  return ok && exit_code == 0 ? 0 : 1;
}
