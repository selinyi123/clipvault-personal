#include "candidate_layout.h"

#include <algorithm>
#include <cstdint>
#include <limits>

namespace clipvault::ime::candidate_layout {
namespace {

constexpr std::int64_t kDefaultDpi = 96;

int ClampToInt(std::int64_t value) noexcept {
  return static_cast<int>(std::clamp<std::int64_t>(
      value, std::numeric_limits<int>::min(),
      std::numeric_limits<int>::max()));
}

int Scale(int value, std::uint32_t dpi) noexcept {
  const std::int64_t normalized_dpi =
      std::max<std::int64_t>(kDefaultDpi, dpi);
  return ClampToInt((static_cast<std::int64_t>(value) * normalized_dpi +
                     kDefaultDpi / 2) /
                    kDefaultDpi);
}

std::int64_t SaturatingHeight(std::size_t count, int height) noexcept {
  const auto bounded_count = std::min<std::uint64_t>(
      count, static_cast<std::uint64_t>(std::numeric_limits<int>::max()));
  return std::min<std::int64_t>(
      static_cast<std::int64_t>(bounded_count) * std::max(0, height),
      std::numeric_limits<int>::max());
}

}  // namespace

Metrics ScaleMetrics(std::uint32_t dpi) noexcept {
  Metrics metrics;
  metrics.width = Scale(440, dpi);
  metrics.row_height = Scale(30, dpi);
  metrics.header_height = Scale(24, dpi);
  metrics.footer_height = Scale(24, dpi);
  metrics.text_left = Scale(10, dpi);
  metrics.text_right_margin = Scale(8, dpi);
  metrics.fallback_view_offset = Scale(36, dpi);
  return metrics;
}

WindowSize MeasureWindow(std::size_t engine_candidate_count,
                         std::size_t snapshot_candidate_count,
                         const Metrics& metrics) noexcept {
  std::int64_t height =
      SaturatingHeight(engine_candidate_count, metrics.row_height);
  if (engine_candidate_count != 0) {
    height += std::max(0, metrics.footer_height);
  }
  if (snapshot_candidate_count != 0) {
    height += std::max(0, metrics.header_height);
    height += SaturatingHeight(snapshot_candidate_count, metrics.row_height);
  }
  return {std::max(0, metrics.width),
          ClampToInt(std::min<std::int64_t>(
              height, std::numeric_limits<int>::max()))};
}

Point PlaceWindow(Point below_anchor, WindowSize size, Rect work_area,
                  int flip_gap) noexcept {
  if (work_area.right <= work_area.left || work_area.bottom <= work_area.top) {
    return below_anchor;
  }

  const std::int64_t width = std::max(0, size.width);
  const std::int64_t height = std::max(0, size.height);
  const std::int64_t minimum_x = work_area.left;
  const std::int64_t maximum_x = std::max<std::int64_t>(
      minimum_x, static_cast<std::int64_t>(work_area.right) - width);
  const std::int64_t minimum_y = work_area.top;
  const std::int64_t maximum_y = std::max<std::int64_t>(
      minimum_y, static_cast<std::int64_t>(work_area.bottom) - height);

  std::int64_t y = below_anchor.y;
  if (y + height > work_area.bottom) {
    y -= height + std::max(0, flip_gap);
  }

  return {
      ClampToInt(std::clamp<std::int64_t>(below_anchor.x, minimum_x,
                                          maximum_x)),
      ClampToInt(std::clamp<std::int64_t>(y, minimum_y, maximum_y)),
  };
}

HitTarget HitTest(Point client_point, int client_width, const Metrics& metrics,
                  std::size_t engine_candidate_count,
                  std::size_t snapshot_candidate_count,
                  bool has_previous_page, bool has_next_page) noexcept {
  if (client_point.x < 0 || client_point.x >= client_width ||
      client_point.y < 0 || metrics.row_height <= 0) {
    return {};
  }

  const std::int64_t y = client_point.y;
  const std::int64_t engine_height =
      SaturatingHeight(engine_candidate_count, metrics.row_height);
  if (y < engine_height) {
    const auto index = static_cast<std::size_t>(y / metrics.row_height);
    return {HitKind::kEngineCandidate, index};
  }

  std::int64_t offset = engine_height;
  if (engine_candidate_count != 0) {
    const std::int64_t footer_bottom =
        offset + std::max(0, metrics.footer_height);
    if (y < footer_bottom) {
      const bool left_half = client_point.x < client_width / 2;
      if (left_half && has_previous_page) {
        return {HitKind::kPreviousPage, 0};
      }
      if (!left_half && has_next_page) {
        return {HitKind::kNextPage, 0};
      }
      return {};
    }
    offset = footer_bottom;
  }

  if (snapshot_candidate_count == 0) {
    return {};
  }
  offset += std::max(0, metrics.header_height);
  const std::int64_t snapshot_height =
      SaturatingHeight(snapshot_candidate_count, metrics.row_height);
  if (y < offset || y >= offset + snapshot_height) {
    return {};
  }
  const auto index = static_cast<std::size_t>(
      (y - offset) / metrics.row_height);
  return {HitKind::kSnapshotCandidate, index};
}

}  // namespace clipvault::ime::candidate_layout
