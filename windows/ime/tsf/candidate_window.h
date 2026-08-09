#pragma once

#include "candidate_layout.h"
#include "protocol.h"

#include <msctf.h>
#include <windows.h>

#include <cstddef>
#include <cstdint>
#include <functional>
#include <string>
#include <vector>

class CandidateWindow final {
 public:
  using SelectionHandler = std::function<void(std::size_t)>;
  using PageHandler = std::function<void(bool)>;
  using SnapshotSelectionHandler =
      std::function<void(const std::string&, std::uint64_t,
                         const std::string&)>;

  CandidateWindow(HINSTANCE module, SelectionHandler select, PageHandler page,
                  SnapshotSelectionHandler select_snapshot);
  ~CandidateWindow();
  CandidateWindow(const CandidateWindow&) = delete;
  CandidateWindow& operator=(const CandidateWindow&) = delete;

  bool Show(ITfContext* context, const RECT* text_extent,
            const clipvault::ime::EngineState& state);
  void Hide() noexcept;
  [[nodiscard]] bool visible() const noexcept;

 private:
  bool EnsureWindow();
  POINT ResolveAnchor(ITfContext* context, const RECT* text_extent) const;
  LRESULT HandleMessage(UINT message, WPARAM word, LPARAM data);
  bool ArmSnapshotExpiryTimer();
  void StopSnapshotExpiryTimer() noexcept;
  void ClearSnapshotSurface() noexcept;
  void ExpireSnapshotSurface() noexcept;
  void ResetSnapshotDeadlineIdentity() noexcept;
  void Paint();
  static LRESULT CALLBACK WindowProcedure(HWND window, UINT message, WPARAM word,
                                          LPARAM data);

  HINSTANCE module_;
  HWND window_ = nullptr;
  SelectionHandler select_;
  PageHandler page_;
  SnapshotSelectionHandler select_snapshot_;
  std::vector<clipvault::ime::EngineCandidate> candidates_;
  clipvault::ime::SnapshotSurface snapshot_surface_;
  std::uint32_t page_index_ = 0;
  bool has_previous_page_ = false;
  bool has_next_page_ = false;
  std::string snapshot_deadline_publisher_epoch_;
  std::uint64_t snapshot_deadline_generation_ = 0;
  std::uint64_t snapshot_deadline_expires_at_ms_ = 0;
  ULONGLONG snapshot_expiry_deadline_tick_ = 0;
  clipvault::ime::candidate_layout::Metrics layout_metrics_;
};
