#pragma once

#include <memory>

#include "clipvault/rime_bridge.h"

namespace clipvault::rime_poc {

// Concrete Backend for the preferred A route. The implementation is isolated
// in the .cpp file so Android/Kotlin-facing code never depends on rime_api.h.
class LibrimeBackend final : public Backend {
 public:
  LibrimeBackend();
  ~LibrimeBackend() override;

  LibrimeBackend(const LibrimeBackend&) = delete;
  LibrimeBackend& operator=(const LibrimeBackend&) = delete;
  LibrimeBackend(LibrimeBackend&&) = delete;
  LibrimeBackend& operator=(LibrimeBackend&&) = delete;

  void initialize(const InitOptions& options) override;
  void reset() override;
  bool process_key(int keycode, int mask) override;
  bool select_candidate(std::size_t index) override;
  Snapshot snapshot() override;
  void shutdown() noexcept override;

 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};

}  // namespace clipvault::rime_poc
