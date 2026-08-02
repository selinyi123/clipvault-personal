#include "clipvault/rime_bridge.h"

#include <filesystem>
#include <stdexcept>
#include <utility>

namespace clipvault::rime_poc {

Bridge::Bridge(std::unique_ptr<Backend> backend) : backend_(std::move(backend)) {
  if (!backend_) {
    throw std::invalid_argument("backend must not be null");
  }
}

Bridge::~Bridge() { shutdown(); }

void Bridge::validate_options(const InitOptions& options) {
  if (options.shared_data_dir.empty()) {
    throw std::invalid_argument("shared_data_dir must not be empty");
  }
  if (options.user_data_dir.empty()) {
    throw std::invalid_argument("user_data_dir must not be empty");
  }
  if (options.schema_id.empty()) {
    throw std::invalid_argument("schema_id must not be empty");
  }

  const auto shared = std::filesystem::path(options.shared_data_dir).lexically_normal();
  const auto user = std::filesystem::path(options.user_data_dir).lexically_normal();
  if (!shared.is_absolute() || !user.is_absolute()) {
    throw std::invalid_argument("data directories must be absolute paths");
  }
  if (shared == user) {
    throw std::invalid_argument("shared_data_dir and user_data_dir must be distinct");
  }
}

void Bridge::initialize(const InitOptions& options) {
  std::scoped_lock lock(mutex_);
  if (initialized_) {
    throw std::logic_error("bridge is already initialized");
  }
  validate_options(options);
  try {
    backend_->initialize(options);
    initialized_ = true;
  } catch (...) {
    backend_->shutdown();
    throw;
  }
}

void Bridge::require_initialized() const {
  if (!initialized_) {
    throw std::logic_error("bridge is not initialized");
  }
}

KeyResult Bridge::process_key(int keycode, int mask) {
  std::scoped_lock lock(mutex_);
  require_initialized();
  if (keycode < 0) {
    throw std::invalid_argument("keycode must be non-negative");
  }
  const bool handled = backend_->process_key(keycode, mask);
  return KeyResult{handled, backend_->snapshot()};
}

Snapshot Bridge::select_candidate(std::size_t index) {
  std::scoped_lock lock(mutex_);
  require_initialized();
  if (!backend_->select_candidate(index)) {
    throw std::out_of_range("candidate index was rejected");
  }
  return backend_->snapshot();
}

Snapshot Bridge::reset() {
  std::scoped_lock lock(mutex_);
  require_initialized();
  backend_->reset();
  auto value = backend_->snapshot();
  if (!value.composition.empty() || !value.candidates.empty() ||
      !value.commit.empty()) {
    throw std::runtime_error("backend reset left stale state behind");
  }
  return value;
}

void Bridge::shutdown() noexcept {
  std::scoped_lock lock(mutex_);
  if (!initialized_) {
    return;
  }
  backend_->shutdown();
  initialized_ = false;
}

bool Bridge::initialized() const noexcept {
  std::scoped_lock lock(mutex_);
  return initialized_;
}

}  // namespace clipvault::rime_poc
