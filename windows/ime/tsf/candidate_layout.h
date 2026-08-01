#pragma once

#include <cstddef>
#include <cstdint>

namespace clipvault::ime::candidate_layout {

struct Metrics final {
  int width = 440;
  int row_height = 30;
  int header_height = 24;
  int footer_height = 24;
  int text_left = 10;
  int text_right_margin = 8;
  int fallback_view_offset = 36;

  bool operator==(const Metrics&) const = default;
};

struct Point final {
  int x = 0;
  int y = 0;

  bool operator==(const Point&) const = default;
};

struct Rect final {
  int left = 0;
  int top = 0;
  int right = 0;
  int bottom = 0;

  bool operator==(const Rect&) const = default;
};

struct WindowSize final {
  int width = 0;
  int height = 0;

  bool operator==(const WindowSize&) const = default;
};

enum class HitKind : std::uint8_t {
  kNone = 0,
  kEngineCandidate = 1,
  kPreviousPage = 2,
  kNextPage = 3,
  kSnapshotCandidate = 4,
};

struct HitTarget final {
  HitKind kind = HitKind::kNone;
  std::size_t index = 0;

  bool operator==(const HitTarget&) const = default;
};

// Windows reports a transient zero DPI while a hidden popup is being created.
// Keep 96 DPI as the lower bound so the window remains usable and deterministic.
[[nodiscard]] Metrics ScaleMetrics(std::uint32_t dpi) noexcept;

[[nodiscard]] WindowSize MeasureWindow(std::size_t engine_candidate_count,
                                       std::size_t snapshot_candidate_count,
                                       const Metrics& metrics) noexcept;

// `below_anchor` is the desired top-left point below the caret. If the window
// would cross the selected monitor work area's bottom edge, it is flipped
// above that point with `flip_gap` clearance and then clamped to the work area.
[[nodiscard]] Point PlaceWindow(Point below_anchor, WindowSize size,
                                Rect work_area, int flip_gap) noexcept;

[[nodiscard]] HitTarget HitTest(
    Point client_point, int client_width, const Metrics& metrics,
    std::size_t engine_candidate_count, std::size_t snapshot_candidate_count,
    bool has_previous_page, bool has_next_page) noexcept;

}  // namespace clipvault::ime::candidate_layout
