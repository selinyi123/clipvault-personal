#include "clipvault_rime_engine.h"

#include <cstring>
#include <mutex>
#include <utility>

#include <rime_api.h>

namespace clipvault::rime_poc {
namespace {

std::mutex g_lifecycle_mutex;
bool g_engine_active = false;

void SetError(std::string* error, const char* operation) {
  if (error != nullptr) {
    *error = operation;
  }
}

class ActiveEngineReservation final {
 public:
  static bool Acquire(std::string* error) {
    std::lock_guard<std::mutex> lock(g_lifecycle_mutex);
    if (g_engine_active) {
      SetError(error, "engine_already_active");
      return false;
    }
    g_engine_active = true;
    return true;
  }

  static void Release() {
    std::lock_guard<std::mutex> lock(g_lifecycle_mutex);
    g_engine_active = false;
  }
};

}  // namespace

class RimeEngine::Impl final {
 public:
  explicit Impl(EnginePaths paths) : paths_(std::move(paths)) {}

  ~Impl() { Shutdown(); }

  bool Initialize(std::string* error) {
    if (paths_.shared_data_dir.empty() || paths_.user_data_dir.empty() ||
        paths_.shared_data_dir == paths_.user_data_dir) {
      SetError(error, "invalid_data_directories");
      return false;
    }

    if (!ActiveEngineReservation::Acquire(error)) {
      return false;
    }
    reservation_held_ = true;

    api_ = rime_get_api();
    if (api_ == nullptr) {
      SetError(error, "get_api_failed");
      return false;
    }

    RimeTraits traits = {};
    RIME_STRUCT_INIT(RimeTraits, traits);
    traits.shared_data_dir = paths_.shared_data_dir.c_str();
    traits.user_data_dir = paths_.user_data_dir.c_str();
    traits.distribution_name = "ClipVault Rime PoC";
    traits.distribution_code_name = "clipvault_rime_poc";
    traits.distribution_version = "0.1";
    traits.app_name = "rime.clipvault.poc";
    traits.modules = nullptr;
    traits.min_log_level = 3;
    traits.log_dir = "";

    api_->setup(&traits);
    api_->initialize(&traits);
    initialized_ = true;

    if (api_->start_maintenance(false)) {
      api_->join_maintenance_thread();
    }

    session_id_ = api_->create_session();
    if (session_id_ == 0) {
      SetError(error, "create_session_failed");
      return false;
    }
    if (!api_->select_schema(session_id_, "clipvault_poc")) {
      SetError(error, "select_schema_failed");
      return false;
    }
    return true;
  }

  void Shutdown() {
    if (api_ != nullptr && session_id_ != 0) {
      api_->destroy_session(session_id_);
      session_id_ = 0;
    }
    if (api_ != nullptr && initialized_) {
      api_->finalize();
      initialized_ = false;
    }
    api_ = nullptr;
    if (reservation_held_) {
      ActiveEngineReservation::Release();
      reservation_held_ = false;
    }
  }

  bool Reset(std::string* error) {
    if (!Ready(error)) {
      return false;
    }
    api_->clear_composition(session_id_);

    // A reset must not leave an unread commit that can leak into the next
    // deterministic vector. get_commit() consumes the pending commit, if any.
    RimeCommit pending_commit = {};
    RIME_STRUCT_INIT(RimeCommit, pending_commit);
    if (api_->get_commit(session_id_, &pending_commit)) {
      api_->free_commit(&pending_commit);
    }
    return true;
  }

  bool ProcessKey(int keycode, int mask, std::string* error) {
    if (!Ready(error)) {
      return false;
    }
    return api_->process_key(session_id_, keycode, mask) != False;
  }

  bool GetSnapshot(Snapshot* snapshot, std::string* error) const {
    if (snapshot == nullptr) {
      SetError(error, "snapshot_output_missing");
      return false;
    }
    if (!Ready(error)) {
      return false;
    }

    RimeContext context = {};
    RIME_STRUCT_INIT(RimeContext, context);
    if (!api_->get_context(session_id_, &context)) {
      SetError(error, "get_context_failed");
      return false;
    }

    Snapshot result;
    if (context.composition.preedit != nullptr) {
      result.composition = context.composition.preedit;
    }
    result.highlighted_candidate_index =
        context.menu.highlighted_candidate_index;
    result.candidates.reserve(
        static_cast<std::size_t>(context.menu.num_candidates));
    for (int index = 0; index < context.menu.num_candidates; ++index) {
      const RimeCandidate& source = context.menu.candidates[index];
      Candidate candidate;
      if (source.text != nullptr) {
        candidate.text = source.text;
      }
      if (source.comment != nullptr) {
        candidate.comment = source.comment;
      }
      result.candidates.push_back(std::move(candidate));
    }

    api_->free_context(&context);
    *snapshot = std::move(result);
    return true;
  }

  bool SelectCandidate(std::size_t index, std::string* error) {
    if (!Ready(error)) {
      return false;
    }
    if (!api_->select_candidate_on_current_page(session_id_, index)) {
      SetError(error, "select_candidate_failed");
      return false;
    }
    return true;
  }

  std::optional<std::string> TakeCommit(std::string* error) {
    if (!Ready(error)) {
      return std::nullopt;
    }

    RimeCommit commit = {};
    RIME_STRUCT_INIT(RimeCommit, commit);
    if (!api_->get_commit(session_id_, &commit)) {
      return std::nullopt;
    }

    std::string result;
    if (commit.text != nullptr) {
      result = commit.text;
    }
    api_->free_commit(&commit);
    return result;
  }

  std::string EngineVersion() const {
    if (api_ == nullptr || api_->get_version == nullptr) {
      return {};
    }
    const char* version = api_->get_version();
    return version == nullptr ? std::string() : std::string(version);
  }

 private:
  bool Ready(std::string* error) const {
    if (api_ == nullptr || !initialized_ || session_id_ == 0) {
      SetError(error, "engine_not_ready");
      return false;
    }
    return true;
  }

  EnginePaths paths_;
  RimeApi* api_ = nullptr;
  RimeSessionId session_id_ = 0;
  bool initialized_ = false;
  bool reservation_held_ = false;
};

std::unique_ptr<RimeEngine> RimeEngine::Open(const EnginePaths& paths,
                                             std::string* error) {
  auto impl = std::make_unique<Impl>(paths);
  if (!impl->Initialize(error)) {
    return nullptr;
  }
  return std::unique_ptr<RimeEngine>(new RimeEngine(std::move(impl)));
}

RimeEngine::RimeEngine(std::unique_ptr<Impl> impl) : impl_(std::move(impl)) {}

RimeEngine::~RimeEngine() = default;

bool RimeEngine::Reset(std::string* error) { return impl_->Reset(error); }

bool RimeEngine::ProcessKey(int keycode, int mask, std::string* error) {
  return impl_->ProcessKey(keycode, mask, error);
}

bool RimeEngine::GetSnapshot(Snapshot* snapshot, std::string* error) const {
  return impl_->GetSnapshot(snapshot, error);
}

bool RimeEngine::SelectCandidate(std::size_t index, std::string* error) {
  return impl_->SelectCandidate(index, error);
}

std::optional<std::string> RimeEngine::TakeCommit(std::string* error) {
  return impl_->TakeCommit(error);
}

std::string RimeEngine::EngineVersion() const {
  return impl_->EngineVersion();
}

}  // namespace clipvault::rime_poc
