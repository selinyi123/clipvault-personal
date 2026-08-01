#pragma once

#include "protocol.h"

#include <windows.h>

#include <atomic>
#include <cstdint>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>

#if defined(CLIPVAULT_WITH_RIME)
#include <rime_api.h>
#endif

namespace clipvault::ime {

class RimeEngine final {
 public:
  RimeEngine() = default;
  ~RimeEngine();
  RimeEngine(const RimeEngine&) = delete;
  RimeEngine& operator=(const RimeEngine&) = delete;

  bool Initialize(const std::wstring& executable_directory,
                  bool run_maintenance = false);
  [[nodiscard]] bool available() const noexcept;
  std::uint64_t CreateSession(const InputContext& context);
  void DestroySession(std::uint64_t session_id) noexcept;
  bool ProcessKey(std::uint64_t session_id, const KeyEvent& event,
                  EngineState* state);
  bool SelectCandidate(std::uint64_t session_id, std::size_t current_page_index,
                       EngineState* state);
  bool ChangePage(std::uint64_t session_id, bool backward, EngineState* state);
  bool CommitComposition(std::uint64_t session_id, EngineState* state);
  bool CancelComposition(std::uint64_t session_id, EngineState* state);
  bool SetOption(std::uint64_t session_id, const std::string& option,
                 bool enabled, EngineState* state);
  bool SnapshotState(std::uint64_t session_id, EngineState* state);

 private:
#if defined(CLIPVAULT_WITH_RIME)
  bool PopulateStateLocked(RimeSessionId session_id, bool handled,
                           EngineState* state);
  RimeApi* api_ = nullptr;
  HMODULE module_ = nullptr;
  std::atomic_bool initialized_{false};
  mutable std::mutex mutex_;
  std::vector<RimeSessionId> ordinary_pool_;
  std::vector<RimeSessionId> private_pool_;
  std::unordered_map<RimeSessionId, bool> leased_sessions_;
#endif
};

}  // namespace clipvault::ime
