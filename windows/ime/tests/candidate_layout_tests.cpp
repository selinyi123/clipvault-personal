#include "candidate_layout.h"

#include <iostream>
#include <string_view>

namespace layout = clipvault::ime::candidate_layout;

namespace {

int failures = 0;

void Check(bool condition, std::string_view message) {
  if (condition) return;
  std::cerr << "FAIL: " << message << '\n';
  ++failures;
}

void TestDpiScaling() {
  Check(layout::ScaleMetrics(0) == layout::ScaleMetrics(96),
        "zero DPI uses the deterministic 96-DPI floor");
  Check(layout::ScaleMetrics(72) == layout::ScaleMetrics(96),
        "sub-96 DPI uses the usability floor");

  const auto dpi144 = layout::ScaleMetrics(144);
  Check(dpi144.width == 660 && dpi144.row_height == 45 &&
            dpi144.header_height == 36 && dpi144.footer_height == 36 &&
            dpi144.text_left == 15 && dpi144.text_right_margin == 12 &&
            dpi144.fallback_view_offset == 54,
        "144-DPI metrics scale every production dimension");

  const auto dpi192 = layout::ScaleMetrics(192);
  Check(dpi192.width == 880 && dpi192.row_height == 60,
        "192-DPI metrics double the candidate geometry");
}

void TestWindowMeasurement() {
  const auto metrics = layout::ScaleMetrics(96);
  Check(layout::MeasureWindow(3, 2, metrics) ==
            layout::WindowSize{440, 198},
        "mixed engine and snapshot groups include footer and header");
  Check(layout::MeasureWindow(3, 0, metrics) ==
            layout::WindowSize{440, 114},
        "engine-only layout includes the paging footer");
  Check(layout::MeasureWindow(0, 2, metrics) ==
            layout::WindowSize{440, 84},
        "snapshot-only layout includes its header without an engine footer");
  Check(layout::MeasureWindow(0, 0, metrics) ==
            layout::WindowSize{440, 0},
        "empty layout has no phantom rows");
}

void TestWorkAreaPlacement() {
  constexpr layout::Rect primary{0, 0, 1920, 1040};
  Check(layout::PlaceWindow({100, 200}, {440, 114}, primary, 30) ==
            layout::Point{100, 200},
        "window stays below the caret when it fits");
  Check(layout::PlaceWindow({1800, 200}, {440, 114}, primary, 30) ==
            layout::Point{1480, 200},
        "right edge clamps inside the monitor work area");
  Check(layout::PlaceWindow({100, 1000}, {440, 114}, primary, 30) ==
            layout::Point{100, 856},
        "bottom edge flips above the caret with one-row clearance");

  constexpr layout::Rect left_monitor{-1920, 0, 0, 1040};
  Check(layout::PlaceWindow({-100, 1000}, {440, 114}, left_monitor, 30) ==
            layout::Point{-440, 856},
        "negative-coordinate monitor clamps and flips independently");

  const auto dpi192 = layout::ScaleMetrics(192);
  const auto oversized = layout::MeasureWindow(9, 8, dpi192);
  Check(layout::PlaceWindow({-20, 1000}, oversized, left_monitor,
                            dpi192.row_height) == layout::Point{-880, 0},
        "high-DPI window larger than work height remains anchored in work area");
  Check(layout::PlaceWindow({5, 7}, {20, 30}, {0, 0, 0, 0}, 10) ==
            layout::Point{5, 7},
        "invalid work area leaves the caller-provided anchor unchanged");
}

void TestHitTargets() {
  const auto metrics = layout::ScaleMetrics(96);
  constexpr int width = 440;
  const auto hit = [&](int x, int y, bool previous = true,
                       bool next = true) {
    return layout::HitTest({x, y}, width, metrics, 2, 2, previous, next);
  };

  Check(hit(10, 0) ==
            layout::HitTarget{layout::HitKind::kEngineCandidate, 0},
        "first engine row starts at zero");
  Check(hit(10, 29) ==
            layout::HitTarget{layout::HitKind::kEngineCandidate, 0},
        "first engine row includes its last pixel");
  Check(hit(10, 30) ==
            layout::HitTarget{layout::HitKind::kEngineCandidate, 1},
        "second engine row starts on the exact boundary");
  Check(hit(219, 60) ==
            layout::HitTarget{layout::HitKind::kPreviousPage, 0},
        "left footer half maps to previous page");
  Check(hit(220, 60) ==
            layout::HitTarget{layout::HitKind::kNextPage, 0},
        "right footer half maps to next page");
  Check(hit(10, 60, false, true).kind == layout::HitKind::kNone,
        "disabled previous-page half is inert");
  Check(hit(300, 60, true, false).kind == layout::HitKind::kNone,
        "disabled next-page half is inert");
  Check(hit(10, 84).kind == layout::HitKind::kNone &&
            hit(10, 107).kind == layout::HitKind::kNone,
        "snapshot header is never selectable");
  Check(hit(10, 108) ==
            layout::HitTarget{layout::HitKind::kSnapshotCandidate, 0},
        "first snapshot row starts after its header");
  Check(hit(10, 138) ==
            layout::HitTarget{layout::HitKind::kSnapshotCandidate, 1},
        "second snapshot row has a stable boundary");
  Check(hit(10, 168).kind == layout::HitKind::kNone,
        "point below the measured window has no target");
  Check(hit(-1, 10).kind == layout::HitKind::kNone &&
            hit(width, 10).kind == layout::HitKind::kNone,
        "horizontal points outside the client area are rejected");

  Check(layout::HitTest({10, 24}, width, metrics, 0, 2, false, false) ==
            layout::HitTarget{layout::HitKind::kSnapshotCandidate, 0},
        "snapshot-only window maps rows after its header");
}

}  // namespace

int main() {
  TestDpiScaling();
  TestWindowMeasurement();
  TestWorkAreaPlacement();
  TestHitTargets();
  if (failures != 0) {
    std::cerr << failures << " candidate layout assertion(s) failed\n";
    return 1;
  }
  std::cout << "candidate layout DPI, work-area, and hit tests passed\n";
  return 0;
}
