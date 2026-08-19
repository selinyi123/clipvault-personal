#include "diagnostics.h"
#include "otp_broker_client.h"
#include "protocol.h"
#include "rime_engine.h"
#include "runtime_snapshot.h"

#include <sddl.h>

#include <algorithm>
#include <atomic>
#include <array>
#include <cwchar>
#include <cwctype>
#include <optional>
#include <string>
#include <thread>
#include <unordered_map>
#include <utility>
#include <vector>

namespace {

using namespace clipvault::ime;

struct Session final {
  std::uint64_t revision = 0;
  std::uint64_t last_request_seq = 1;
  std::uint64_t rime_session_id = 0;
  std::wstring echo_preedit;
  std::vector<std::string> candidate_ids;
  std::vector<std::wstring> candidate_texts;
  std::uint32_t candidate_page = 0;
  InputContext input_context;
  std::shared_ptr<RuntimeSnapshotCoordinator::SessionHandle> snapshot;
};

struct SessionRegistry final {
  SessionRegistry(RimeEngine* engine,
                  RuntimeSnapshotCoordinator* snapshot_coordinator)
      : engine(engine), snapshot_coordinator(snapshot_coordinator) {}
  ~SessionRegistry() {
    for (const auto& item : sessions) {
      snapshot_coordinator->Invalidate(item.second.snapshot);
      engine->DestroySession(item.second.rime_session_id);
    }
  }
  RimeEngine* engine;
  RuntimeSnapshotCoordinator* snapshot_coordinator;
  std::unordered_map<std::string, Session> sessions;
};

struct LocalSecurity final {
  PSECURITY_DESCRIPTOR descriptor = nullptr;
  SECURITY_ATTRIBUTES attributes{sizeof(SECURITY_ATTRIBUTES), nullptr, FALSE};

  ~LocalSecurity() {
    if (descriptor != nullptr) LocalFree(descriptor);
  }

  bool Initialize() {
    HANDLE token = nullptr;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token)) return false;
    DWORD required = 0;
    GetTokenInformation(token, TokenUser, nullptr, 0, &required);
    std::vector<std::uint8_t> storage(required);
    const bool read = required != 0 &&
                      GetTokenInformation(token, TokenUser, storage.data(), required,
                                          &required) != FALSE;
    CloseHandle(token);
    if (!read) return false;
    const auto* user = reinterpret_cast<const TOKEN_USER*>(storage.data());
    LPWSTR sid = nullptr;
    if (!ConvertSidToStringSidW(user->User.Sid, &sid)) return false;
    const std::wstring sddl = L"D:P(A;;GA;;;SY)(A;;GA;;;" + std::wstring(sid) + L")";
    LocalFree(sid);
    if (!ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl.c_str(), SDDL_REVISION_1, &descriptor, nullptr)) return false;
    attributes.lpSecurityDescriptor = descriptor;
    return true;
  }
};

std::wstring ExecutableDirectory() {
  std::array<wchar_t, 32768> path{};
  const DWORD length = GetModuleFileNameW(nullptr, path.data(),
                                          static_cast<DWORD>(path.size()));
  if (length == 0 || length >= static_cast<DWORD>(path.size())) return {};
  std::wstring value(path.data(), length);
  const auto separator = value.find_last_of(L"\\/");
  return separator == std::wstring::npos ? std::wstring{} : value.substr(0, separator);
}

HANDLE CreatePipe(LocalSecurity* security, bool first_instance) {
  DWORD open_mode = PIPE_ACCESS_DUPLEX;
  if (first_instance) open_mode |= FILE_FLAG_FIRST_PIPE_INSTANCE;
  return CreateNamedPipeW(
      PipeNameForCurrentSession().c_str(), open_mode,
      PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
      PIPE_UNLIMITED_INSTANCES, kMaximumFrameBytes + 4, kMaximumFrameBytes + 4,
      0, &security->attributes);
}

void ApplyEnvelope(const std::string& host_instance_id,
                   const std::string& session_id, Session* session,
                   std::uint64_t request_seq, EngineState* state) {
  state->host_instance_id = host_instance_id;
  state->session_id = session_id;
  state->ack_request_seq = request_seq;
  state->revision = ++session->revision;
  session->last_request_seq = request_seq;

  bool same_candidates = state->page_index == session->candidate_page &&
                         state->candidates.size() == session->candidate_texts.size();
  if (same_candidates) {
    for (std::size_t index = 0; index < state->candidates.size(); ++index) {
      if (state->candidates[index].text != session->candidate_texts[index]) {
        same_candidates = false;
        break;
      }
    }
  }
  if (!same_candidates) {
    session->candidate_ids.clear();
    session->candidate_texts.clear();
    session->candidate_page = state->page_index;
    for (const auto& candidate : state->candidates) {
      session->candidate_ids.push_back(NewOpaqueId());
      session->candidate_texts.push_back(candidate.text);
    }
  }
  for (std::size_t index = 0; index < state->candidates.size(); ++index)
    state->candidates[index].candidate_id = session->candidate_ids[index];
}

bool PopulateEchoState(Session* session, const KeyEvent* event,
                       std::optional<std::size_t> selection, EngineState* state) {
  *state = EngineState{};
  state->handled = true;
  if (selection.has_value()) {
    if (*selection != 0 || session->echo_preedit.empty()) return false;
    state->commit_text = session->echo_preedit;
    session->echo_preedit.clear();
  } else if (event != nullptr && !event->text.empty() && !event->control && !event->alt) {
    session->echo_preedit.append(event->text);
  } else if (event != nullptr && event->virtual_key == VK_BACK) {
    if (!session->echo_preedit.empty()) session->echo_preedit.pop_back();
  } else if (event != nullptr &&
             (event->virtual_key == VK_SPACE || event->virtual_key == VK_RETURN)) {
    if (!session->echo_preedit.empty()) {
      state->commit_text = session->echo_preedit;
      session->echo_preedit.clear();
    } else {
      state->handled = false;
    }
  } else if (event != nullptr && event->virtual_key == VK_ESCAPE) {
    session->echo_preedit.clear();
  } else if (event == nullptr) {
    state->handled = false;
  } else {
    state->handled = false;
  }
  state->preedit = session->echo_preedit;
  state->caret_utf16 = static_cast<std::uint32_t>(session->echo_preedit.size());
  state->composition_active = !session->echo_preedit.empty();
  if (state->composition_active) {
    state->candidates.push_back(EngineCandidate{"", session->echo_preedit, L""});
    state->page_size = 1;
  }
  state->mode = state->candidates.empty() ? (state->composition_active ? 2 : 1) : 3;
  return true;
}

bool PopulateUnavailableState(EngineState* state) {
  *state = EngineState{};
  state->handled = false;
  state->mode = 1;
  return true;
}

bool PromoteEchoSessionToRime(Session* session, RimeEngine* rime) {
  if (session->rime_session_id != 0 || !rime->available()) return false;
  constexpr std::size_t kMaximumReplayCharacters = 64;
  if (session->echo_preedit.size() > kMaximumReplayCharacters) return false;
  const std::uint64_t rime_session = rime->CreateSession(session->input_context);
  if (rime_session == 0) return false;
  EngineState replay_state;
  bool replayed = true;
  for (const wchar_t value : session->echo_preedit) {
    KeyEvent replay;
    replay.virtual_key = static_cast<std::uint32_t>(std::towupper(value));
    replay.text.assign(1, value);
    if (!rime->ProcessKey(rime_session, replay, &replay_state) ||
        !replay_state.handled) {
      replayed = false;
      break;
    }
  }
  if (!replayed) {
    rime->DestroySession(rime_session);
    return false;
  }
  session->rime_session_id = rime_session;
  session->echo_preedit.clear();
  session->candidate_ids.clear();
  session->candidate_texts.clear();
  session->candidate_page = 0;
  return true;
}

bool ValidateSequence(const std::string& host_instance_id,
                      const std::string& request_host, std::uint64_t request_seq,
                      std::uint64_t expected_revision, const Session& session) {
  return request_host == host_instance_id &&
         request_seq == session.last_request_seq + 1 &&
         expected_revision == session.revision;
}

bool HandleConnection(HANDLE pipe, const std::string& host_instance_id,
                       RimeEngine* rime,
                       RuntimeSnapshotCoordinator* snapshot_coordinator,
                       OtpBrokerInsertClient* otp_client,
                       bool allow_echo) {
  std::vector<std::uint8_t> frame;
  std::string client_id;
  if (!ReadFrame(pipe, &frame) || !DecodeClientHello(frame, &client_id) ||
      !WriteFrame(pipe, EncodeHostHello(host_instance_id))) return false;

  SessionRegistry registry(rime, snapshot_coordinator);
  while (ReadFrame(pipe, &frame)) {
    StartSessionRequest start;
    if (DecodeStartSession(frame, &start)) {
      if (start.host_instance_id != host_instance_id ||
          registry.sessions.contains(start.session_id)) return false;
      Session session;
      session.input_context = start.context;
      const bool snapshot_allowed =
          start.context.clipvault_allowed && start.context.learning_allowed &&
          !start.context.incognito &&
          start.context.field_kind != InputFieldKind::kUnknown &&
          start.context.field_kind != InputFieldKind::kPassword;
      session.snapshot = snapshot_coordinator->BeginSession(snapshot_allowed);
      if (rime->available()) {
        session.rime_session_id = rime->CreateSession(start.context);
        if (session.rime_session_id == 0) return false;
      }
      const auto [inserted_session, inserted] =
          registry.sessions.emplace(start.session_id, std::move(session));
      if (!inserted) return false;
      EngineState state;
      state.host_instance_id = host_instance_id;
      state.session_id = start.session_id;
      state.ack_request_seq = start.request_seq;
      state.mode = 1;
      state.snapshot_surface =
          snapshot_coordinator->Current(inserted_session->second.snapshot);
      if (!WriteFrame(pipe, EncodeEngineState(state))) return false;
      continue;
    }

    std::string session_id;
    std::uint64_t request_seq = 0;
    std::uint64_t expected_revision = 0;
    ProcessKeyRequest key;
    SelectCandidateRequest select;
    PageCandidatesRequest page;
    CompositionCommandRequest command;
    SelectSnapshotCandidateRequest snapshot_select;
    InsertOtpRequest otp_insert;
    enum class Operation {
      kKey,
      kSelect,
      kPage,
      kCommit,
      kCancel,
      kSelectSnapshot,
      kInsertOtp
    } operation;
    if (DecodeProcessKey(frame, &key)) {
      operation = Operation::kKey;
      session_id = key.session_id;
      request_seq = key.request_seq;
      expected_revision = key.expected_revision;
    } else if (DecodeSelectCandidate(frame, &select)) {
      operation = Operation::kSelect;
      session_id = select.session_id;
      request_seq = select.request_seq;
      expected_revision = select.expected_revision;
    } else if (DecodePageCandidates(frame, &page)) {
      operation = Operation::kPage;
      session_id = page.session_id;
      request_seq = page.request_seq;
      expected_revision = page.expected_revision;
    } else if (DecodeCommitComposition(frame, &command)) {
      operation = Operation::kCommit;
      session_id = command.session_id;
      request_seq = command.request_seq;
      expected_revision = command.expected_revision;
    } else if (DecodeCancelComposition(frame, &command)) {
      operation = Operation::kCancel;
      session_id = command.session_id;
      request_seq = command.request_seq;
      expected_revision = command.expected_revision;
    } else if (DecodeSelectSnapshotCandidate(frame, &snapshot_select)) {
      operation = Operation::kSelectSnapshot;
      session_id = snapshot_select.session_id;
      request_seq = snapshot_select.request_seq;
      expected_revision = snapshot_select.expected_revision;
    } else if (DecodeInsertOtp(frame, &otp_insert)) {
      operation = Operation::kInsertOtp;
      session_id = otp_insert.session_id;
      request_seq = otp_insert.request_seq;
      expected_revision = otp_insert.expected_revision;
    } else {
      return false;
    }
    auto session_it = registry.sessions.find(session_id);
    if (session_it == registry.sessions.end()) return false;
    auto& session = session_it->second;
    const std::string& request_host =
        operation == Operation::kKey
            ? key.host_instance_id
            : operation == Operation::kSelect
                  ? select.host_instance_id
                  : operation == Operation::kPage
                         ? page.host_instance_id
                         : operation == Operation::kInsertOtp
                               ? otp_insert.host_instance_id
                         : operation == Operation::kSelectSnapshot
                              ? snapshot_select.host_instance_id
                              : command.host_instance_id;
    if (!ValidateSequence(host_instance_id, request_host, request_seq,
                          expected_revision, session)) return false;

    // A connection may be accepted before librime is ready. Keep an in-flight
    // echo composition stable, then promote the session at the next clean key
    // boundary once the asynchronously initialized engine is available.
    if (operation == Operation::kKey && session.rime_session_id == 0 &&
        rime->available()) {
      PromoteEchoSessionToRime(&session, rime);
    }

    EngineState state;
    bool success = false;
    if (operation == Operation::kKey) {
      success = session.rime_session_id != 0
                    ? rime->ProcessKey(session.rime_session_id, key.event, &state)
                    : allow_echo
                          ? PopulateEchoState(&session, &key.event, std::nullopt,
                                              &state)
                          : PopulateUnavailableState(&state);
    } else if (operation == Operation::kSelect) {
      const auto candidate =
          std::find(session.candidate_ids.begin(), session.candidate_ids.end(),
                    select.candidate_id);
      if (candidate == session.candidate_ids.end()) return false;
      const auto index = static_cast<std::size_t>(candidate - session.candidate_ids.begin());
      success = session.rime_session_id != 0
                    ? rime->SelectCandidate(session.rime_session_id, index, &state)
                    : PopulateEchoState(&session, nullptr, index, &state);
    } else if (operation == Operation::kPage) {
      success = session.rime_session_id != 0
                    ? rime->ChangePage(session.rime_session_id, page.backward, &state)
                    : PopulateEchoState(&session, nullptr, std::nullopt, &state);
    } else if (operation == Operation::kCommit) {
      KeyEvent commit;
      commit.virtual_key = VK_RETURN;
      success = session.rime_session_id != 0
                    ? rime->CommitComposition(session.rime_session_id, &state)
                    : PopulateEchoState(&session, &commit, std::nullopt, &state);
    } else if (operation == Operation::kCancel) {
      KeyEvent cancel;
      cancel.virtual_key = VK_ESCAPE;
      success = session.rime_session_id != 0
                    ? rime->CancelComposition(session.rime_session_id, &state)
                    : PopulateEchoState(&session, &cancel, std::nullopt, &state);
    } else if (operation == Operation::kSelectSnapshot) {
      const auto selected = snapshot_coordinator->Consume(
          session.snapshot, snapshot_select.publisher_epoch,
          snapshot_select.generation, snapshot_select.candidate_id);
      state = EngineState{};
      success = true;
      if (selected.has_value()) {
        EngineState ignored;
        if (session.rime_session_id != 0 &&
            !rime->CancelComposition(session.rime_session_id, &ignored)) {
          return false;
        }
        session.echo_preedit.clear();
        session.candidate_ids.clear();
        session.candidate_texts.clear();
        session.candidate_page = 0;
        state.handled = true;
        state.commit_text = std::move(*selected);
        state.mode = 1;
      } else {
        success = session.rime_session_id != 0
                      ? rime->SnapshotState(session.rime_session_id, &state)
                      : PopulateEchoState(&session, nullptr, std::nullopt,
                                          &state);
      }
    } else {
      state = EngineState{};
      const bool context_allowed =
          session.input_context.field_kind != InputFieldKind::kUnknown &&
          session.input_context.field_kind != InputFieldKind::kPassword;
      std::wstring otp;
      success = true;
      if (context_allowed && otp_client != nullptr &&
          otp_client->ConsumeLatest(otp_insert.context, &otp)) {
        EngineState ignored;
        if (session.rime_session_id != 0 &&
            !rime->CancelComposition(session.rime_session_id, &ignored)) {
          SecureZeroMemory(otp.data(), otp.size() * sizeof(wchar_t));
          return false;
        }
        session.echo_preedit.clear();
        session.candidate_ids.clear();
        session.candidate_texts.clear();
        session.candidate_page = 0;
        state.handled = true;
        state.commit_text = std::move(otp);
        state.mode = 1;
      }
    }
    if (!success) return false;
    ApplyEnvelope(host_instance_id, session_id, &session, request_seq, &state);
    state.snapshot_surface = snapshot_coordinator->Current(session.snapshot);
    auto encoded = EncodeEngineState(state);
    const bool wrote = !encoded.empty() && WriteFrame(pipe, encoded);
    if (operation == Operation::kInsertOtp) {
      if (!encoded.empty()) SecureZeroMemory(encoded.data(), encoded.size());
      if (state.commit_text.has_value() && !state.commit_text->empty())
        SecureZeroMemory(state.commit_text->data(),
                         state.commit_text->size() * sizeof(wchar_t));
    }
    if (!wrote) return false;
  }
  return true;
}

bool HasArgument(int argc, wchar_t** argv, const wchar_t* expected) {
  for (int index = 1; index < argc; ++index)
    if (std::wcscmp(argv[index], expected) == 0) return true;
  return false;
}

DWORD TestRimeInitializationDelayMilliseconds() {
  if (LocalTestNamespaceSuffix().empty()) return 0;
  wchar_t value[16]{};
  const DWORD length = GetEnvironmentVariableW(
      L"CLIPVAULT_IME_TEST_RIME_INIT_DELAY_MS", value, std::size(value));
  if (length == 0 || length >= std::size(value)) return 0;
  wchar_t* end = nullptr;
  const unsigned long parsed = std::wcstoul(value, &end, 10);
  if (end == value || *end != L'\0' || parsed > 5000) return 0;
  return static_cast<DWORD>(parsed);
}

}  // namespace

int wmain(int argc, wchar_t** argv) {
  const bool once = HasArgument(argc, argv, L"--once");
  const bool require_rime = HasArgument(argc, argv, L"--require-rime");
  const bool force_echo = HasArgument(argc, argv, L"--echo");
  const bool deploy_rime = HasArgument(argc, argv, L"--deploy-rime");
  const bool test_echo_before_rime =
      HasArgument(argc, argv, L"--test-echo-before-rime");
  if ((require_rime && force_echo) || (deploy_rime && force_echo)) return 7;
  if (test_echo_before_rime && LocalTestNamespaceSuffix().empty()) return 7;
  const bool allow_echo = force_echo || test_echo_before_rime || !require_rime;
  if (deploy_rime) {
    const ULONGLONG started = GetTickCount64();
    EmitDiagnostic(DiagnosticEvent::kRimeDeployStarted);
    RimeEngine deployer;
    const bool deployed = deployer.Initialize(ExecutableDirectory(), true);
    const auto duration = static_cast<std::uint32_t>(
        std::min<ULONGLONG>(GetTickCount64() - started, UINT32_MAX));
    EmitDiagnostic(DiagnosticEvent::kRimeDeployFinished, deployed ? 1u : 0u,
                   duration);
    return deployed ? 0 : 6;
  }
  DWORD windows_session_id = 0;
  ProcessIdToSessionId(GetCurrentProcessId(), &windows_session_id);
  const std::wstring mutex_name =
      L"Local\\ClipVaultImeHostV2-" + std::to_wstring(windows_session_id) +
      LocalTestNamespaceSuffix();
  HANDLE mutex = CreateMutexW(nullptr, TRUE, mutex_name.c_str());
  if (mutex == nullptr || GetLastError() == ERROR_ALREADY_EXISTS) {
    if (mutex != nullptr) CloseHandle(mutex);
    return 2;
  }

  LocalSecurity security;
  const std::string host_instance_id = NewOpaqueId();
  RimeEngine rime;
  if (!security.Initialize() || host_instance_id.empty()) {
    CloseHandle(mutex);
    return 3;
  }

  RuntimeSnapshotFetchOptions snapshot_options;
  snapshot_options.pipe_name = RuntimeSnapshotPipeNameForCurrentSession();
  snapshot_options.expected_server_path =
      ExpectedRuntimeExecutable(ExecutableDirectory());
  snapshot_options.require_trusted_signature = true;
  auto snapshot_client =
      std::make_shared<RuntimeSnapshotPipeClient>(std::move(snapshot_options));
  RuntimeSnapshotCoordinator snapshot_coordinator(
      [snapshot_client](std::uint64_t request_id, std::uint32_t limit,
                        std::uint64_t now_ms,
                        RuntimeSnapshotResponse* response) {
        return snapshot_client->Fetch(request_id, limit, now_ms, response);
      });
  OtpBrokerInsertClient otp_client;

  // Create the control endpoint before any librime initialization. This keeps
  // Profile activation and the first key independent from dictionary deploy or
  // engine startup. Normal runtime initialization never performs maintenance;
  // installers/settings must call --deploy-rime beforehand.
  HANDLE next_pipe = CreatePipe(&security, true);
  if (next_pipe == INVALID_HANDLE_VALUE) {
    CloseHandle(mutex);
    return 4;
  }

  std::jthread rime_initialization;
  if (require_rime) {
    const ULONGLONG started = GetTickCount64();
    EmitDiagnostic(DiagnosticEvent::kRimeInitializeStarted);
    const bool ready = rime.Initialize(ExecutableDirectory(), false);
    const auto duration = static_cast<std::uint32_t>(
        std::min<ULONGLONG>(GetTickCount64() - started, UINT32_MAX));
    EmitDiagnostic(ready ? DiagnosticEvent::kRimeInitializeReady
                         : DiagnosticEvent::kRimeInitializeUnavailable,
                   0, duration);
    if (!ready) {
      CloseHandle(next_pipe);
      CloseHandle(mutex);
      return 6;
    }
  } else if (!force_echo) {
    rime_initialization = std::jthread([&rime] {
      const ULONGLONG started = GetTickCount64();
      EmitDiagnostic(DiagnosticEvent::kRimeInitializeStarted);
      const DWORD test_delay = TestRimeInitializationDelayMilliseconds();
      if (test_delay != 0) Sleep(test_delay);
      const bool ready = rime.Initialize(ExecutableDirectory(), false);
      const auto duration = static_cast<std::uint32_t>(
          std::min<ULONGLONG>(GetTickCount64() - started, UINT32_MAX));
      EmitDiagnostic(ready ? DiagnosticEvent::kRimeInitializeReady
                           : DiagnosticEvent::kRimeInitializeUnavailable,
                     0, duration);
    });
  }

  int result = 0;
  do {
    HANDLE pipe = next_pipe;
    next_pipe = INVALID_HANDLE_VALUE;
    const bool connected = ConnectNamedPipe(pipe, nullptr) != FALSE ||
                           GetLastError() == ERROR_PIPE_CONNECTED;
    if (connected) {
      if (once) {
        HandleConnection(pipe, host_instance_id, &rime, &snapshot_coordinator,
                         &otp_client, allow_echo);
        FlushFileBuffers(pipe);
        DisconnectNamedPipe(pipe);
        CloseHandle(pipe);
      } else {
        std::thread([pipe, host_instance_id, &rime, &snapshot_coordinator,
                     &otp_client, allow_echo] {
          HandleConnection(pipe, host_instance_id, &rime,
                           &snapshot_coordinator, &otp_client, allow_echo);
          FlushFileBuffers(pipe);
          DisconnectNamedPipe(pipe);
          CloseHandle(pipe);
        }).detach();
      }
    } else {
      CloseHandle(pipe);
    }
    if (!connected && once) result = 5;
    if (!once) {
      next_pipe = CreatePipe(&security, false);
      if (next_pipe == INVALID_HANDLE_VALUE) {
        result = 4;
        break;
      }
    }
  } while (!once);

  if (next_pipe != INVALID_HANDLE_VALUE) CloseHandle(next_pipe);

  ReleaseMutex(mutex);
  CloseHandle(mutex);
  return result;
}
