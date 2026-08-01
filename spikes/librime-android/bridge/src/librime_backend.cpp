#include "clipvault/librime_backend.h"

#include <cstring>
#include <mutex>
#include <stdexcept>
#include <string>
#include <utility>

#include <rime_api.h>

namespace clipvault::rime_poc {
namespace {

std::mutex& global_rime_mutex() {
  static std::mutex mutex;
  return mutex;
}

void require_api(bool available, const char* name) {
  if (!available) {
    throw std::runtime_error(std::string("librime API unavailable: ") + name);
  }
}

std::string copy_nullable(const char* value) {
  return value == nullptr ? std::string{} : std::string(value);
}

}  // namespace

struct LibrimeBackend::Impl {
  RimeApi* api = nullptr;
  RimeSessionId session_id = 0;
  bool initialized = false;
  std::string shared_data_dir;
  std::string user_data_dir;
  std::string schema_id;
};

LibrimeBackend::LibrimeBackend() : impl_(std::make_unique<Impl>()) {}

LibrimeBackend::~LibrimeBackend() { shutdown(); }

void LibrimeBackend::initialize(const InitOptions& options) {
  std::scoped_lock lock(global_rime_mutex());
  if (impl_->initialized || impl_->session_id != 0) {
    throw std::logic_error("librime backend is already initialized");
  }

  impl_->shared_data_dir = options.shared_data_dir;
  impl_->user_data_dir = options.user_data_dir;
  impl_->schema_id = options.schema_id;
  impl_->api = rime_get_api();
  require_api(impl_->api != nullptr, "rime_get_api");
  require_api(impl_->api->setup != nullptr, "setup");
  require_api(impl_->api->initialize != nullptr, "initialize");
  require_api(impl_->api->finalize != nullptr, "finalize");
  require_api(impl_->api->start_maintenance != nullptr, "start_maintenance");
  require_api(impl_->api->join_maintenance_thread != nullptr,
              "join_maintenance_thread");
  require_api(impl_->api->create_session != nullptr, "create_session");
  require_api(impl_->api->destroy_session != nullptr, "destroy_session");
  require_api(impl_->api->process_key != nullptr, "process_key");
  require_api(impl_->api->clear_composition != nullptr, "clear_composition");
  require_api(impl_->api->get_commit != nullptr, "get_commit");
  require_api(impl_->api->free_commit != nullptr, "free_commit");
  require_api(impl_->api->get_context != nullptr, "get_context");
  require_api(impl_->api->free_context != nullptr, "free_context");
  require_api(RIME_API_AVAILABLE(impl_->api, get_input), "get_input");
  require_api(RIME_API_AVAILABLE(impl_->api, select_candidate_on_current_page),
              "select_candidate_on_current_page");
  require_api(impl_->api->select_schema != nullptr, "select_schema");
  require_api(impl_->api->set_option != nullptr, "set_option");

  RIME_STRUCT(RimeTraits, traits);
  traits.shared_data_dir = impl_->shared_data_dir.c_str();
  traits.user_data_dir = impl_->user_data_dir.c_str();
  traits.distribution_name = "ClipVault Rime PoC";
  traits.distribution_code_name = "clipvault-rime-poc";
  traits.distribution_version = "0.1";
  traits.app_name = "rime.clipvault.poc";
  traits.min_log_level = 3;
  traits.log_dir = "";

  impl_->api->setup(&traits);
  impl_->api->initialize(nullptr);
  impl_->initialized = true;

  if (impl_->api->start_maintenance(True)) {
    impl_->api->join_maintenance_thread();
  }

  impl_->session_id = impl_->api->create_session();
  if (impl_->session_id == 0) {
    throw std::runtime_error("librime failed to create a session");
  }
  if (!impl_->api->select_schema(impl_->session_id, impl_->schema_id.c_str())) {
    throw std::runtime_error("librime failed to select schema: " + impl_->schema_id);
  }
  impl_->api->set_option(impl_->session_id, "ascii_mode", False);
}

void LibrimeBackend::reset() {
  std::scoped_lock lock(global_rime_mutex());
  require_api(impl_->initialized && impl_->session_id != 0,
              "reset on initialized session");
  impl_->api->clear_composition(impl_->session_id);

  // Reset is destructive for the PoC boundary. Drain any unread commit so a
  // previous selection can never cross into the next test vector or field.
  RIME_STRUCT(RimeCommit, commit);
  if (impl_->api->get_commit(impl_->session_id, &commit)) {
    impl_->api->free_commit(&commit);
  }
}

bool LibrimeBackend::process_key(int keycode, int mask) {
  std::scoped_lock lock(global_rime_mutex());
  require_api(impl_->initialized && impl_->session_id != 0,
              "process_key on initialized session");
  return impl_->api->process_key(impl_->session_id, keycode, mask) == True;
}

bool LibrimeBackend::select_candidate(std::size_t index) {
  std::scoped_lock lock(global_rime_mutex());
  require_api(impl_->initialized && impl_->session_id != 0,
              "select_candidate on initialized session");
  return impl_->api->select_candidate_on_current_page(impl_->session_id, index) ==
         True;
}

Snapshot LibrimeBackend::snapshot() {
  std::scoped_lock lock(global_rime_mutex());
  require_api(impl_->initialized && impl_->session_id != 0,
              "snapshot on initialized session");

  Snapshot result;

  RIME_STRUCT(RimeCommit, commit);
  if (impl_->api->get_commit(impl_->session_id, &commit)) {
    try {
      result.commit = copy_nullable(commit.text);
    } catch (...) {
      impl_->api->free_commit(&commit);
      throw;
    }
    impl_->api->free_commit(&commit);
  }

  result.composition = copy_nullable(impl_->api->get_input(impl_->session_id));

  RIME_STRUCT(RimeContext, context);
  if (impl_->api->get_context(impl_->session_id, &context)) {
    try {
      if (result.composition.empty()) {
        result.composition = copy_nullable(context.composition.preedit);
      }
      if (context.menu.num_candidates < 0) {
        throw std::runtime_error("librime returned a negative candidate count");
      }
      if (context.menu.num_candidates > 0 && context.menu.candidates == nullptr) {
        throw std::runtime_error("librime returned a null candidate array");
      }
      result.candidates.reserve(
          static_cast<std::size_t>(context.menu.num_candidates));
      for (int index = 0; index < context.menu.num_candidates; ++index) {
        const auto& candidate = context.menu.candidates[index];
        result.candidates.push_back(
            Candidate{copy_nullable(candidate.text), copy_nullable(candidate.comment)});
      }
    } catch (...) {
      impl_->api->free_context(&context);
      throw;
    }
    impl_->api->free_context(&context);
  }

  return result;
}

void LibrimeBackend::shutdown() noexcept {
  try {
    std::scoped_lock lock(global_rime_mutex());
    if (impl_->api != nullptr && impl_->session_id != 0 &&
        impl_->api->destroy_session != nullptr) {
      impl_->api->destroy_session(impl_->session_id);
    }
    impl_->session_id = 0;
    if (impl_->api != nullptr && impl_->initialized &&
        impl_->api->finalize != nullptr) {
      impl_->api->finalize();
    }
    impl_->initialized = false;
    impl_->api = nullptr;
    impl_->shared_data_dir.clear();
    impl_->user_data_dir.clear();
    impl_->schema_id.clear();
  } catch (...) {
    // Backend::shutdown is noexcept. Native teardown failure must not escape
    // an IME lifecycle callback or destructor.
  }
}

}  // namespace clipvault::rime_poc
