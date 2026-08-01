#include "rime_engine.h"

#include <algorithm>
#include <cstring>
#include <filesystem>
#include <optional>
#include <system_error>

namespace clipvault::ime {
namespace {

#if defined(CLIPVAULT_WITH_RIME)
constexpr std::size_t kPreparedSessionsPerSchema = 8;
using RimeGetApi = RimeApi*(__cdecl*)();

bool WideToUtf8(const std::wstring& value, std::string* output) {
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

bool Utf8ToWide(const char* value, std::wstring* output) {
  output->clear();
  if (value == nullptr || *value == '\0') return true;
  const int size = static_cast<int>(std::strlen(value));
  const int required = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, value, size,
                                            nullptr, 0);
  if (required <= 0) return false;
  output->resize(static_cast<std::size_t>(required));
  return MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, value, size,
                             output->data(), required) == required;
}

std::wstring EnvironmentPath(const wchar_t* name) {
  const DWORD required = GetEnvironmentVariableW(name, nullptr, 0);
  if (required == 0) return {};
  std::wstring value(required, L'\0');
  const DWORD written = GetEnvironmentVariableW(name, value.data(), required);
  if (written == 0 || written >= required) return {};
  value.resize(written);
  return value;
}

std::wstring DefaultUserDirectory() {
  std::wstring local = EnvironmentPath(L"LOCALAPPDATA");
  if (local.empty()) return {};
  return local + L"\\ClipVault\\Rime";
}

int RimeKeyCode(const KeyEvent& event) {
  if (!event.text.empty() && event.text.front() >= 0x20 &&
      event.text.front() <= 0x7e) return static_cast<int>(event.text.front());
  switch (event.virtual_key) {
    case VK_BACK: return 0xff08;
    case VK_RETURN: return 0xff0d;
    case VK_ESCAPE: return 0xff1b;
    case VK_SPACE: return 0x20;
    case VK_PRIOR: return 0xff55;
    case VK_NEXT: return 0xff56;
    default: return static_cast<int>(event.virtual_key);
  }
}

int RimeModifier(const KeyEvent& event) {
  int result = 0;
  if (event.shift) result |= 1 << 0;
  if (event.control) result |= 1 << 2;
  if (event.alt) result |= 1 << 3;
  if (!event.key_down) result |= 1 << 30;
  return result;
}
#endif

}  // namespace

RimeEngine::~RimeEngine() {
#if defined(CLIPVAULT_WITH_RIME)
  std::scoped_lock lock(mutex_);
  if (initialized_.load(std::memory_order_acquire) && api_ != nullptr) {
    api_->cleanup_all_sessions();
    api_->finalize();
  }
  ordinary_pool_.clear();
  private_pool_.clear();
  leased_sessions_.clear();
  initialized_.store(false, std::memory_order_release);
  api_ = nullptr;
  if (module_ != nullptr) FreeLibrary(module_);
  module_ = nullptr;
#endif
}

bool RimeEngine::Initialize(const std::wstring& executable_directory,
                            bool run_maintenance) {
#if !defined(CLIPVAULT_WITH_RIME)
  (void)executable_directory;
  (void)run_maintenance;
  return false;
#else
  std::scoped_lock lock(mutex_);
  if (initialized_.load(std::memory_order_acquire)) return true;
  const std::wstring dll_path = executable_directory + L"\\rime.dll";
  module_ = LoadLibraryExW(dll_path.c_str(), nullptr,
                           LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR |
                               LOAD_LIBRARY_SEARCH_SYSTEM32);
  if (module_ == nullptr) return false;
  const auto get_api = reinterpret_cast<RimeGetApi>(GetProcAddress(module_, "rime_get_api"));
  if (get_api == nullptr) return false;
  api_ = get_api();
  if (api_ == nullptr || !RIME_API_AVAILABLE(api_, setup) ||
      !RIME_API_AVAILABLE(api_, initialize) ||
      !RIME_API_AVAILABLE(api_, finalize) ||
      !RIME_API_AVAILABLE(api_, create_session) ||
      !RIME_API_AVAILABLE(api_, destroy_session) ||
      !RIME_API_AVAILABLE(api_, set_option) ||
      !RIME_API_AVAILABLE(api_, select_schema) ||
      !RIME_API_AVAILABLE(api_, process_key) ||
      !RIME_API_AVAILABLE(api_, get_context) ||
      !RIME_API_AVAILABLE(api_, free_context) ||
      !RIME_API_AVAILABLE(api_, get_commit) ||
      !RIME_API_AVAILABLE(api_, free_commit) ||
      !RIME_API_AVAILABLE(api_, select_candidate_on_current_page) ||
      !RIME_API_AVAILABLE(api_, change_page) ||
      !RIME_API_AVAILABLE(api_, commit_composition) ||
      !RIME_API_AVAILABLE(api_, clear_composition)) {
    api_ = nullptr;
    return false;
  }

  std::wstring shared = EnvironmentPath(L"CLIPVAULT_RIME_DATA_DIR");
  if (shared.empty()) shared = executable_directory + L"\\rime-data";
  std::wstring user = EnvironmentPath(L"CLIPVAULT_RIME_USER_DIR");
  if (user.empty()) user = DefaultUserDirectory();
  std::error_code error;
  std::filesystem::create_directories(user, error);
  if (user.empty() || error || !std::filesystem::is_directory(shared, error) || error)
    return false;
  std::string shared_utf8;
  std::string user_utf8;
  if (!WideToUtf8(shared, &shared_utf8) || !WideToUtf8(user, &user_utf8)) return false;

  RIME_STRUCT(RimeTraits, traits);
  traits.shared_data_dir = shared_utf8.c_str();
  traits.user_data_dir = user_utf8.c_str();
  traits.distribution_name = "ClipVault";
  traits.distribution_code_name = "clipvault";
  traits.distribution_version = "2-native-p1";
  traits.app_name = "rime.clipvault";
  traits.min_log_level = 3;
  traits.log_dir = "";
  api_->setup(&traits);
  api_->initialize(&traits);
  if (run_maintenance && RIME_API_AVAILABLE(api_, start_maintenance) &&
      RIME_API_AVAILABLE(api_, join_maintenance_thread) &&
      api_->start_maintenance(True)) {
    api_->join_maintenance_thread();
  }
  // Creating an empty session does not load a schema or its dictionary. Warm
  // both production schemas and one minimal decode before publishing
  // readiness, otherwise the first StartSession can exceed the 40 ms TSF RPC
  // budget on a clean user directory.
  const auto prepare_schema_session = [this](const char* schema,
                                             RimeSessionId* prepared) {
    const RimeSessionId session = api_->create_session();
    if (session == 0) return false;
    api_->set_option(session, "incognito_mode", True);
    bool ready = api_->select_schema(session, schema) != False &&
                 api_->process_key(session, 'n', 0) != False &&
                 api_->process_key(session, 'i', 0) != False;
    RIME_STRUCT(RimeContext, context);
    if (ready) {
      const bool got_context = api_->get_context(session, &context) != False;
      ready = got_context && context.composition.preedit != nullptr &&
              context.menu.num_candidates > 0;
      if (got_context) api_->free_context(&context);
    }
    api_->clear_composition(session);
    if (ready) {
      *prepared = session;
    } else {
      api_->destroy_session(session);
    }
    return ready;
  };
  ordinary_pool_.reserve(kPreparedSessionsPerSchema);
  private_pool_.reserve(kPreparedSessionsPerSchema);
  for (std::size_t index = 0; index < kPreparedSessionsPerSchema; ++index) {
    RimeSessionId private_session = 0;
    RimeSessionId ordinary_session = 0;
    if (!prepare_schema_session("clipvault_pinyin_private",
                                &private_session) ||
        !prepare_schema_session("clipvault_pinyin", &ordinary_session)) {
      api_->cleanup_all_sessions();
      ordinary_pool_.clear();
      private_pool_.clear();
      api_->finalize();
      api_ = nullptr;
      return false;
    }
    private_pool_.push_back(private_session);
    ordinary_pool_.push_back(ordinary_session);
  }
  initialized_.store(true, std::memory_order_release);
  return true;
#endif
}

bool RimeEngine::available() const noexcept {
#if defined(CLIPVAULT_WITH_RIME)
  return initialized_.load(std::memory_order_acquire) && api_ != nullptr;
#else
  return false;
#endif
}

std::uint64_t RimeEngine::CreateSession(const InputContext& context) {
#if !defined(CLIPVAULT_WITH_RIME)
  (void)context;
  return 0;
#else
  std::scoped_lock lock(mutex_);
  if (!available()) return 0;
  const bool private_session =
      context.field_kind == InputFieldKind::kPassword ||
      context.field_kind == InputFieldKind::kUnknown || context.incognito ||
      !context.learning_allowed;
  auto& pool = private_session ? private_pool_ : ordinary_pool_;
  RimeSessionId session = 0;
  if (!pool.empty()) {
    session = pool.back();
    pool.pop_back();
  } else {
    session = api_->create_session();
    const char* schema =
        private_session ? "clipvault_pinyin_private" : "clipvault_pinyin";
    if (session == 0 || api_->select_schema(session, schema) == False) {
      if (session != 0) api_->destroy_session(session);
      return 0;
    }
  }
  api_->clear_composition(session);
  api_->set_option(session, "incognito_mode", private_session ? True : False);
  leased_sessions_[session] = private_session;
  return static_cast<std::uint64_t>(session);
#endif
}

void RimeEngine::DestroySession(std::uint64_t session_id) noexcept {
#if defined(CLIPVAULT_WITH_RIME)
  std::scoped_lock lock(mutex_);
  if (!available() || session_id == 0) return;
  const auto native_session = static_cast<RimeSessionId>(session_id);
  const auto leased = leased_sessions_.find(native_session);
  if (leased == leased_sessions_.end()) return;
  api_->clear_composition(native_session);
  auto& pool = leased->second ? private_pool_ : ordinary_pool_;
  if (pool.size() < kPreparedSessionsPerSchema) {
    pool.push_back(native_session);
  } else {
    api_->destroy_session(native_session);
  }
  leased_sessions_.erase(leased);
#else
  (void)session_id;
#endif
}

bool RimeEngine::ProcessKey(std::uint64_t session_id, const KeyEvent& event,
                            EngineState* state) {
#if !defined(CLIPVAULT_WITH_RIME)
  (void)session_id;
  (void)event;
  (void)state;
  return false;
#else
  std::scoped_lock lock(mutex_);
  if (!available() || session_id == 0) return false;
  const auto id = static_cast<RimeSessionId>(session_id);
  const bool handled = api_->process_key(id, RimeKeyCode(event), RimeModifier(event));
  return PopulateStateLocked(id, handled, state);
#endif
}

bool RimeEngine::SelectCandidate(std::uint64_t session_id,
                                 std::size_t current_page_index,
                                 EngineState* state) {
#if !defined(CLIPVAULT_WITH_RIME)
  (void)session_id;
  (void)current_page_index;
  (void)state;
  return false;
#else
  std::scoped_lock lock(mutex_);
  if (!available() || session_id == 0) return false;
  const auto id = static_cast<RimeSessionId>(session_id);
  const bool handled = api_->select_candidate_on_current_page(id, current_page_index);
  return PopulateStateLocked(id, handled, state);
#endif
}

bool RimeEngine::ChangePage(std::uint64_t session_id, bool backward,
                            EngineState* state) {
#if !defined(CLIPVAULT_WITH_RIME)
  (void)session_id;
  (void)backward;
  (void)state;
  return false;
#else
  std::scoped_lock lock(mutex_);
  if (!available() || session_id == 0) return false;
  const auto id = static_cast<RimeSessionId>(session_id);
  const bool handled = api_->change_page(id, backward ? True : False);
  return PopulateStateLocked(id, handled, state);
#endif
}

bool RimeEngine::CommitComposition(std::uint64_t session_id,
                                   EngineState* state) {
#if !defined(CLIPVAULT_WITH_RIME)
  (void)session_id;
  (void)state;
  return false;
#else
  std::scoped_lock lock(mutex_);
  if (!available() || session_id == 0) return false;
  const auto id = static_cast<RimeSessionId>(session_id);
  const bool handled = api_->commit_composition(id) != False;
  return PopulateStateLocked(id, handled, state);
#endif
}

bool RimeEngine::CancelComposition(std::uint64_t session_id,
                                   EngineState* state) {
#if !defined(CLIPVAULT_WITH_RIME)
  (void)session_id;
  (void)state;
  return false;
#else
  std::scoped_lock lock(mutex_);
  if (!available() || session_id == 0) return false;
  const auto id = static_cast<RimeSessionId>(session_id);
  api_->clear_composition(id);
  return PopulateStateLocked(id, true, state);
#endif
}

bool RimeEngine::SetOption(std::uint64_t session_id, const std::string& option,
                           bool enabled, EngineState* state) {
#if defined(CLIPVAULT_WITH_RIME)
  std::scoped_lock lock(mutex_);
  const auto native = static_cast<RimeSessionId>(session_id);
  if (api_ == nullptr || !leased_sessions_.contains(native) || option.empty())
    return false;
  api_->set_option(native, option.c_str(), enabled ? True : False);
  return PopulateStateLocked(native, true, state);
#else
  (void)session_id;
  (void)option;
  (void)enabled;
  (void)state;
  return false;
#endif
}

bool RimeEngine::SnapshotState(std::uint64_t session_id, EngineState* state) {
#if !defined(CLIPVAULT_WITH_RIME)
  (void)session_id;
  (void)state;
  return false;
#else
  std::scoped_lock lock(mutex_);
  if (!available() || session_id == 0) return false;
  return PopulateStateLocked(static_cast<RimeSessionId>(session_id), false,
                             state);
#endif
}

#if defined(CLIPVAULT_WITH_RIME)
bool RimeEngine::PopulateStateLocked(RimeSessionId session_id, bool handled,
                                     EngineState* state) {
  *state = EngineState{};
  state->handled = handled;
  RIME_STRUCT(RimeCommit, commit);
  if (api_->get_commit(session_id, &commit)) {
    std::wstring text;
    const bool converted = Utf8ToWide(commit.text, &text);
    api_->free_commit(&commit);
    if (!converted) return false;
    state->commit_text = std::move(text);
  }

  RIME_STRUCT(RimeContext, context);
  if (!api_->get_context(session_id, &context)) {
    state->mode = 1;
    return true;
  }
  bool valid = Utf8ToWide(context.composition.preedit, &state->preedit);
  if (valid && context.composition.preedit != nullptr) {
    const auto byte_cursor = std::clamp(context.composition.cursor_pos, 0,
                                        static_cast<int>(std::strlen(
                                            context.composition.preedit)));
    std::string prefix(context.composition.preedit,
                       context.composition.preedit + byte_cursor);
    std::wstring prefix_wide;
    valid = Utf8ToWide(prefix.c_str(), &prefix_wide);
    state->caret_utf16 = static_cast<std::uint32_t>(prefix_wide.size());
  }
  if (valid) {
    state->page_index = static_cast<std::uint32_t>(std::max(0, context.menu.page_no));
    state->page_size = static_cast<std::uint32_t>(std::max(0, context.menu.page_size));
    state->has_previous_page = context.menu.page_no > 0;
    state->has_next_page = context.menu.is_last_page == False;
    for (int index = 0; index < context.menu.num_candidates; ++index) {
      EngineCandidate candidate;
      valid = Utf8ToWide(context.menu.candidates[index].text, &candidate.text) &&
              Utf8ToWide(context.menu.candidates[index].comment, &candidate.comment);
      if (!valid) break;
      state->candidates.push_back(std::move(candidate));
    }
  }
  api_->free_context(&context);
  if (!valid) return false;
  state->composition_active = !state->preedit.empty();
  state->mode = state->candidates.empty() ? (state->composition_active ? 2 : 1) : 3;
  return true;
}
#endif

}  // namespace clipvault::ime
