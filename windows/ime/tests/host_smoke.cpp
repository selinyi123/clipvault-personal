#include "protocol.h"

#include <windows.h>

#include <cwctype>
#include <filesystem>
#include <iostream>
#include <optional>
#include <set>
#include <string>
#include <system_error>

namespace {

std::wstring Quote(const std::wstring& value) { return L"\"" + value + L"\""; }

std::optional<std::wstring> ReadEnvironment(const wchar_t* name) {
  const DWORD required = GetEnvironmentVariableW(name, nullptr, 0);
  if (required == 0) return std::nullopt;
  std::wstring value(required, L'\0');
  const DWORD written = GetEnvironmentVariableW(name, value.data(), required);
  if (written == 0 || written >= required) return std::nullopt;
  value.resize(written);
  return value;
}

void RestoreEnvironment(const wchar_t* name,
                        const std::optional<std::wstring>& value) {
  SetEnvironmentVariableW(name, value.has_value() ? value->c_str() : nullptr);
}

bool SendText(clipvault::ime::PipeEngineClient* client,
              const std::wstring& text, clipvault::ime::EngineState* state,
              int* stage) {
  for (const wchar_t value : text) {
    clipvault::ime::KeyEvent event;
    event.virtual_key = static_cast<std::uint32_t>(towupper(value));
    event.text.assign(1, value);
    if (!client->ProcessKey(event, state)) return false;
    ++*stage;
  }
  return true;
}

std::set<std::filesystem::path> UserDatabaseFiles(
    const std::filesystem::path& directory) {
  std::set<std::filesystem::path> result;
  std::error_code error;
  for (std::filesystem::recursive_directory_iterator iterator(directory, error),
       end;
       !error && iterator != end; iterator.increment(error)) {
    if (iterator->is_regular_file(error) && !error &&
        iterator->path().filename().wstring().find(L".userdb") !=
            std::wstring::npos) {
      result.insert(std::filesystem::relative(iterator->path(), directory, error));
    }
  }
  return error ? std::set<std::filesystem::path>{} : result;
}

std::string CandidateIdForText(const clipvault::ime::EngineState& state,
                               const std::wstring& text) {
  for (const auto& candidate : state.candidates) {
    if (candidate.text == text) return candidate.candidate_id;
  }
  return {};
}

}  // namespace

int wmain(int argc, wchar_t** argv) {
  if (argc != 3) return 2;
  const bool require_rime = std::wstring(argv[2]) == L"--require-rime";
  const bool force_echo = std::wstring(argv[2]) == L"--echo";
  if (!require_rime && !force_echo) return 2;
  const std::wstring test_namespace =
      L"smoke-" + std::to_wstring(GetCurrentProcessId()) + L"-" +
      std::to_wstring(GetTickCount64());
  if (!SetEnvironmentVariableW(L"CLIPVAULT_IME_TEST_NAMESPACE",
                               test_namespace.c_str()) ||
      !SetEnvironmentVariableW(L"CLIPVAULT_INSECURE_TEST_PIPE_TRUST", L"1"))
    return 3;

  std::filesystem::path isolated_user;
  const auto prior_user = ReadEnvironment(L"CLIPVAULT_RIME_USER_DIR");
  if (require_rime) {
    std::error_code error;
    isolated_user = std::filesystem::temp_directory_path(error) /
                    (L"ClipVaultRimeSmoke-" +
                     std::to_wstring(GetCurrentProcessId()) + L"-" +
                     std::to_wstring(GetTickCount64()));
    std::filesystem::create_directories(isolated_user, error);
    if (error || !SetEnvironmentVariableW(L"CLIPVAULT_RIME_USER_DIR",
                                           isolated_user.c_str())) return 3;

    std::wstring deploy_command = Quote(argv[1]) + L" --deploy-rime";
    STARTUPINFOW deploy_startup{sizeof(STARTUPINFOW)};
    PROCESS_INFORMATION deploy_process{};
    const bool deploy_created =
        CreateProcessW(argv[1], deploy_command.data(), nullptr, nullptr, FALSE,
                       CREATE_NO_WINDOW, nullptr, nullptr, &deploy_startup,
                       &deploy_process) != FALSE;
    if (!deploy_created) return 4;
    const DWORD deploy_wait = WaitForSingleObject(deploy_process.hProcess, 60000);
    DWORD deploy_exit = 99;
    GetExitCodeProcess(deploy_process.hProcess, &deploy_exit);
    if (deploy_wait == WAIT_TIMEOUT) {
      TerminateProcess(deploy_process.hProcess, 5);
      WaitForSingleObject(deploy_process.hProcess, 3000);
    }
    CloseHandle(deploy_process.hThread);
    CloseHandle(deploy_process.hProcess);
    if (deploy_wait != WAIT_OBJECT_0 || deploy_exit != 0) return 4;
  }

  std::wstring command = Quote(argv[1]) + L" --once " + argv[2];
  STARTUPINFOW startup{sizeof(STARTUPINFOW)};
  PROCESS_INFORMATION process{};
  const bool created = CreateProcessW(argv[1], command.data(), nullptr, nullptr,
                                      FALSE, CREATE_NO_WINDOW, nullptr, nullptr,
                                      &startup, &process) != FALSE;
  RestoreEnvironment(L"CLIPVAULT_RIME_USER_DIR", prior_user);
  if (!created) return 4;

  clipvault::ime::PipeEngineClient client;
  int stage = 0;
  bool ok = client.Connect(require_rime ? 30000 : 3000);
  if (ok) stage = 1;
  clipvault::ime::EngineState state;
  if (require_rime) {
    const auto user_databases_before = UserDatabaseFiles(isolated_user);
    clipvault::ime::InputContext private_context;
    private_context.field_kind = clipvault::ime::InputFieldKind::kText;
    private_context.incognito = true;
    private_context.learning_allowed = false;
    private_context.clipvault_allowed = false;
    ok = ok && client.StartSession(private_context, &state) &&
         state.revision == 0;
    if (ok) stage = 10;
    ok = ok &&
         SendText(&client, L"nihao", &state, &stage) &&
         state.composition_active && !state.candidates.empty() &&
         client.CancelComposition(&state) && state.preedit.empty() &&
         UserDatabaseFiles(isolated_user) == user_databases_before;
  } else {
    ok = ok && client.StartSession(&state) && state.revision == 0;
  }
  if (ok) stage = 20;

  if (require_rime) {
    ok = ok && client.StartSession(&state) && state.revision == 0;
    if (ok) stage = 21;
    ok = ok && SendText(&client, L"ce", &state, &stage) &&
         state.composition_active && client.CancelComposition(&state) &&
         state.preedit.empty() && !state.composition_active &&
         !state.commit_text.has_value();
    ok = ok && SendText(&client, L"nihao", &state, &stage) &&
         state.composition_active && !state.preedit.empty() &&
         !state.candidates.empty() && !state.candidates.front().candidate_id.empty();
    if (ok && state.has_next_page) {
      ok = client.PageCandidates(false, &state) && state.page_index == 1 &&
           !state.candidates.empty();
      if (ok) ++stage;
      ok = ok && client.PageCandidates(true, &state) && state.page_index == 0 &&
           !state.candidates.empty();
      if (ok) ++stage;
    }
    const std::string candidate_id =
        ok ? CandidateIdForText(state, L"你好") : "";
    ok = ok && client.SelectCandidate(candidate_id, &state) &&
         state.commit_text == L"你好" &&
         state.preedit.empty() && !state.composition_active;
    ok = ok && SendText(&client, L",", &state, &stage) &&
         state.commit_text == L"，" && state.preedit.empty() &&
         !state.composition_active;
    ok = ok && SendText(&client, L"zhongwen", &state, &stage) &&
         client.CommitComposition(&state) && state.commit_text.has_value() &&
         !state.commit_text->empty() && state.preedit.empty() &&
         !state.composition_active;
  } else {
    ok = ok && SendText(&client, L"discard", &state, &stage) &&
         client.CancelComposition(&state) && state.preedit.empty() &&
         !state.commit_text.has_value();
    ok = ok && SendText(&client, L"abc", &state, &stage) &&
         client.CommitComposition(&state) &&
         state.commit_text == L"abc" && state.preedit.empty() &&
         !state.composition_active;
  }
  if (ok) ++stage;
  client.Disconnect();

  const DWORD wait = WaitForSingleObject(process.hProcess, 5000);
  if (wait == WAIT_TIMEOUT) {
    TerminateProcess(process.hProcess, 5);
    WaitForSingleObject(process.hProcess, 3000);
  }
  DWORD exit_code = 99;
  GetExitCodeProcess(process.hProcess, &exit_code);
  CloseHandle(process.hThread);
  CloseHandle(process.hProcess);
  if (!isolated_user.empty()) {
    std::error_code cleanup_error;
    std::filesystem::remove_all(isolated_user, cleanup_error);
  }
  if (!ok) {
    std::cerr << "native host smoke failed at stage " << stage << ", host exit "
              << exit_code << ", candidates=" << state.candidates.size()
              << ", commit-present=" << state.commit_text.has_value()
              << ", commit-units="
              << (state.commit_text.has_value() ? state.commit_text->size() : 0)
              << ", preedit-units=" << state.preedit.size()
              << ", active=" << state.composition_active << '\n';
  }
  return ok && exit_code == 0 ? 0 : 1;
}
