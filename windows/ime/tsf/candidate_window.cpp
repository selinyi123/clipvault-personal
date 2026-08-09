#include "candidate_window.h"

#include <windowsx.h>

#include <algorithm>
#include <chrono>
#include <iterator>
#include <string>
#include <utility>

namespace {

constexpr wchar_t kCandidateWindowClass[] =
    L"ClipVaultCandidateWindow.C5CEE00A05AD4ABA93BB6E76932AF126";
constexpr UINT_PTR kSnapshotExpiryTimerId = 1;
constexpr ULONGLONG kMaximumSnapshotLifetimeMilliseconds = 30'000;

std::uint64_t UnixTimeMilliseconds() noexcept {
  const auto value = std::chrono::duration_cast<std::chrono::milliseconds>(
                         std::chrono::system_clock::now().time_since_epoch())
                         .count();
  return value > 0 ? static_cast<std::uint64_t>(value) : 0;
}

}  // namespace

CandidateWindow::CandidateWindow(HINSTANCE module, SelectionHandler select,
                                 PageHandler page,
                                 SnapshotSelectionHandler select_snapshot)
    : module_(module),
      select_(std::move(select)),
      page_(std::move(page)),
      select_snapshot_(std::move(select_snapshot)) {}

CandidateWindow::~CandidateWindow() {
  StopSnapshotExpiryTimer();
  ClearSnapshotSurface();
  ResetSnapshotDeadlineIdentity();
  if (window_ != nullptr) DestroyWindow(window_);
}

bool CandidateWindow::visible() const noexcept {
  return window_ != nullptr && IsWindowVisible(window_) != FALSE;
}

bool CandidateWindow::EnsureWindow() {
  if (window_ != nullptr) return true;
  WNDCLASSEXW type{sizeof(WNDCLASSEXW)};
  type.lpfnWndProc = WindowProcedure;
  type.hInstance = module_;
  type.hCursor = LoadCursorW(nullptr, IDC_ARROW);
  type.lpszClassName = kCandidateWindowClass;
  if (RegisterClassExW(&type) == 0 && GetLastError() != ERROR_CLASS_ALREADY_EXISTS)
    return false;
  window_ = CreateWindowExW(WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_NOACTIVATE,
                            kCandidateWindowClass, L"ClipVault candidates",
                            WS_POPUP | WS_BORDER, 0, 0, 0, 0, nullptr, nullptr,
                            module_, this);
  return window_ != nullptr;
}

POINT CandidateWindow::ResolveAnchor(ITfContext* context,
                                     const RECT* text_extent) const {
  POINT anchor{};
  if (text_extent != nullptr) {
    anchor.x = text_extent->left;
    anchor.y = text_extent->bottom;
    return anchor;
  }
  ITfContextView* view = nullptr;
  if (context != nullptr && SUCCEEDED(context->GetActiveView(&view))) {
    RECT screen{};
    if (SUCCEEDED(view->GetScreenExt(&screen))) {
      anchor.x = screen.left;
      anchor.y = screen.top +
                 clipvault::ime::candidate_layout::ScaleMetrics(96)
                     .fallback_view_offset;
    }
    view->Release();
  }
  if (anchor.x == 0 && anchor.y == 0)
    GetCursorPos(&anchor);
  return anchor;
}

bool CandidateWindow::Show(ITfContext* context,
                           const RECT* text_extent,
                           const clipvault::ime::EngineState& state) {
  if (state.candidates.empty() && state.snapshot_surface.empty()) {
    Hide();
    return true;
  }
  StopSnapshotExpiryTimer();
  if (!EnsureWindow()) {
    Hide();
    return false;
  }
  candidates_ = state.candidates;
  if (candidates_.size() > 9) candidates_.resize(9);
  ClearSnapshotSurface();
  snapshot_surface_ = state.snapshot_surface;
  if (snapshot_surface_.candidates.size() > 8)
    snapshot_surface_.candidates.resize(8);
  if (!snapshot_surface_.empty() && !ArmSnapshotExpiryTimer())
    ClearSnapshotSurface();
  if (candidates_.empty() && snapshot_surface_.empty()) {
    Hide();
    return true;
  }
  page_index_ = state.page_index;
  has_previous_page_ = state.has_previous_page;
  has_next_page_ = state.has_next_page;
  layout_metrics_ = clipvault::ime::candidate_layout::ScaleMetrics(
      GetDpiForWindow(window_));
  const auto size = clipvault::ime::candidate_layout::MeasureWindow(
      candidates_.size(), snapshot_surface_.candidates.size(),
      layout_metrics_);
  POINT anchor = ResolveAnchor(context, text_extent);
  RECT desired{anchor.x, anchor.y, anchor.x + size.width,
               anchor.y + size.height};
  MONITORINFO monitor{sizeof(MONITORINFO)};
  const HMONITOR handle = MonitorFromRect(&desired, MONITOR_DEFAULTTONEAREST);
  if (GetMonitorInfoW(handle, &monitor)) {
    const auto placed = clipvault::ime::candidate_layout::PlaceWindow(
        {anchor.x, anchor.y}, size,
        {monitor.rcWork.left, monitor.rcWork.top, monitor.rcWork.right,
         monitor.rcWork.bottom},
        layout_metrics_.row_height);
    anchor = {placed.x, placed.y};
  }
  SetWindowPos(window_, HWND_TOPMOST, anchor.x, anchor.y, size.width,
               size.height,
               SWP_NOACTIVATE | SWP_SHOWWINDOW);
  InvalidateRect(window_, nullptr, TRUE);
  return true;
}

void CandidateWindow::Hide() noexcept {
  StopSnapshotExpiryTimer();
  candidates_.clear();
  ClearSnapshotSurface();
  if (window_ != nullptr) ShowWindow(window_, SW_HIDE);
}

bool CandidateWindow::ArmSnapshotExpiryTimer() {
  if (window_ == nullptr || snapshot_surface_.empty()) return false;

  const bool same_snapshot =
      snapshot_deadline_publisher_epoch_ ==
          snapshot_surface_.publisher_epoch &&
      snapshot_deadline_generation_ == snapshot_surface_.generation &&
      snapshot_deadline_expires_at_ms_ == snapshot_surface_.expires_at_ms;
  const ULONGLONG now_tick = GetTickCount64();
  if (!same_snapshot) {
    ResetSnapshotDeadlineIdentity();
    snapshot_deadline_publisher_epoch_ = snapshot_surface_.publisher_epoch;
    snapshot_deadline_generation_ = snapshot_surface_.generation;
    snapshot_deadline_expires_at_ms_ = snapshot_surface_.expires_at_ms;
    const std::uint64_t now_unix = UnixTimeMilliseconds();
    const std::uint64_t wall_remaining =
        snapshot_surface_.expires_at_ms > now_unix
            ? snapshot_surface_.expires_at_ms - now_unix
            : 0;
    // A wall-clock rollback must not mint a fresh UI lifetime. Host responses
    // are contractually capped at 30 seconds; anything outside that envelope
    // is rejected instead of silently clamped and displayed.
    if (wall_remaining == 0 ||
        wall_remaining > kMaximumSnapshotLifetimeMilliseconds) {
      snapshot_expiry_deadline_tick_ = now_tick;
      return false;
    }
    snapshot_expiry_deadline_tick_ = now_tick + wall_remaining;
  }

  if (snapshot_expiry_deadline_tick_ <= now_tick) return false;
  const auto remaining = static_cast<UINT>(
      std::min<ULONGLONG>(snapshot_expiry_deadline_tick_ - now_tick,
                          kMaximumSnapshotLifetimeMilliseconds));
  if (SetTimer(window_, kSnapshotExpiryTimerId, std::max<UINT>(remaining, 1),
               nullptr) == 0) {
    // Timer creation failure is fail-closed. Remember this generation as
    // already expired so a later Show cannot resurrect it with a fresh TTL.
    snapshot_expiry_deadline_tick_ = now_tick;
    return false;
  }
  return true;
}

void CandidateWindow::StopSnapshotExpiryTimer() noexcept {
  if (window_ != nullptr) KillTimer(window_, kSnapshotExpiryTimerId);
}

void CandidateWindow::ClearSnapshotSurface() noexcept {
  std::fill(snapshot_surface_.publisher_epoch.begin(),
            snapshot_surface_.publisher_epoch.end(), '\0');
  snapshot_surface_.publisher_epoch.clear();
  snapshot_surface_.generation = 0;
  snapshot_surface_.expires_at_ms = 0;
  for (auto& candidate : snapshot_surface_.candidates) {
    std::fill(candidate.candidate_id.begin(), candidate.candidate_id.end(),
              '\0');
    std::fill(candidate.label.begin(), candidate.label.end(), L'\0');
    std::fill(candidate.text.begin(), candidate.text.end(), L'\0');
  }
  snapshot_surface_.candidates.clear();
}

void CandidateWindow::ResetSnapshotDeadlineIdentity() noexcept {
  std::fill(snapshot_deadline_publisher_epoch_.begin(),
            snapshot_deadline_publisher_epoch_.end(), '\0');
  snapshot_deadline_publisher_epoch_.clear();
  snapshot_deadline_generation_ = 0;
  snapshot_deadline_expires_at_ms_ = 0;
  snapshot_expiry_deadline_tick_ = 0;
}

void CandidateWindow::ExpireSnapshotSurface() noexcept {
  StopSnapshotExpiryTimer();
  ClearSnapshotSurface();
  if (window_ == nullptr) return;
  if (candidates_.empty()) {
    ShowWindow(window_, SW_HIDE);
    return;
  }

  const auto size = clipvault::ime::candidate_layout::MeasureWindow(
      candidates_.size(), 0, layout_metrics_);
  SetWindowPos(window_, nullptr, 0, 0, size.width, size.height,
               SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE);
  InvalidateRect(window_, nullptr, TRUE);
}

void CandidateWindow::Paint() {
  PAINTSTRUCT paint{};
  HDC device = BeginPaint(window_, &paint);
  if (device == nullptr) return;
  RECT client{};
  GetClientRect(window_, &client);
  FillRect(device, &client, GetSysColorBrush(COLOR_WINDOW));
  const HGDIOBJ prior_font = SelectObject(device, GetStockObject(DEFAULT_GUI_FONT));
  SetBkMode(device, TRANSPARENT);
  SetTextColor(device, GetSysColor(COLOR_WINDOWTEXT));
  for (std::size_t index = 0; index < candidates_.size(); ++index) {
    RECT row{layout_metrics_.text_left,
             static_cast<LONG>(index *
                               static_cast<std::size_t>(layout_metrics_.row_height)),
             client.right - layout_metrics_.text_right_margin,
             static_cast<LONG>((index + 1) *
                               static_cast<std::size_t>(layout_metrics_.row_height))};
    std::wstring label = std::to_wstring(index + 1) + L". " +
                         candidates_[index].text;
    if (!candidates_[index].comment.empty())
      label += L"   " + candidates_[index].comment;
    DrawTextW(device, label.c_str(), static_cast<int>(label.size()), &row,
              DT_SINGLELINE | DT_VCENTER | DT_END_ELLIPSIS | DT_NOPREFIX);
  }
  LONG offset = static_cast<LONG>(candidates_.size() *
                                  static_cast<std::size_t>(layout_metrics_.row_height));
  if (!candidates_.empty()) {
    RECT footer{layout_metrics_.text_left, offset,
                client.right - layout_metrics_.text_right_margin,
                offset + layout_metrics_.footer_height};
    std::wstring page = L"Page " + std::to_wstring(page_index_ + 1);
    if (has_previous_page_) page = L"PgUp  |  " + page;
    if (has_next_page_) page += L"  |  PgDn";
    SetTextColor(device, GetSysColor(COLOR_GRAYTEXT));
    DrawTextW(device, page.c_str(), static_cast<int>(page.size()), &footer,
              DT_SINGLELINE | DT_VCENTER | DT_CENTER | DT_NOPREFIX);
    offset += layout_metrics_.footer_height;
  }
  if (!snapshot_surface_.empty()) {
    RECT header{layout_metrics_.text_left, offset,
                client.right - layout_metrics_.text_right_margin,
                offset + layout_metrics_.header_height};
    SetTextColor(device, GetSysColor(COLOR_GRAYTEXT));
    constexpr wchar_t title[] = L"ClipVault";
    DrawTextW(device, title, static_cast<int>(std::size(title) - 1), &header,
              DT_SINGLELINE | DT_VCENTER | DT_NOPREFIX);
    offset += layout_metrics_.header_height;
    SetTextColor(device, GetSysColor(COLOR_WINDOWTEXT));
    for (const auto& candidate : snapshot_surface_.candidates) {
      RECT row{layout_metrics_.text_left, offset,
               client.right - layout_metrics_.text_right_margin,
               offset + layout_metrics_.row_height};
      std::wstring label = candidate.source == 1 ? L"Memory  " : L"Clipboard  ";
      if (!candidate.label.empty()) label += candidate.label + L"  ";
      label += candidate.text;
      DrawTextW(device, label.c_str(), static_cast<int>(label.size()), &row,
                DT_SINGLELINE | DT_VCENTER | DT_END_ELLIPSIS | DT_NOPREFIX);
      offset += layout_metrics_.row_height;
    }
  }
  SelectObject(device, prior_font);
  EndPaint(window_, &paint);
}

LRESULT CandidateWindow::HandleMessage(UINT message, WPARAM word, LPARAM data) {
  if (message == WM_MOUSEACTIVATE) return MA_NOACTIVATE;
  if (message == WM_ERASEBKGND) return 1;
  if (message == WM_PAINT) {
    if (!snapshot_surface_.empty() &&
        (snapshot_expiry_deadline_tick_ == 0 ||
         GetTickCount64() >= snapshot_expiry_deadline_tick_)) {
      ExpireSnapshotSurface();
    }
    Paint();
    return 0;
  }
  if (message == WM_TIMER && word == kSnapshotExpiryTimerId) {
    const ULONGLONG now_tick = GetTickCount64();
    if (snapshot_surface_.empty() || snapshot_expiry_deadline_tick_ == 0 ||
        now_tick >= snapshot_expiry_deadline_tick_) {
      ExpireSnapshotSurface();
    } else {
      const auto remaining = static_cast<UINT>(std::max<ULONGLONG>(
          snapshot_expiry_deadline_tick_ - now_tick, 1));
      if (SetTimer(window_, kSnapshotExpiryTimerId, remaining, nullptr) == 0) {
        snapshot_expiry_deadline_tick_ = now_tick;
        ExpireSnapshotSurface();
      }
    }
    return 0;
  }
  if (message == WM_LBUTTONDOWN) {
    // A queued timer can be delivered just after a click. Enforce the frozen
    // monotonic deadline again before hit-testing so an expired Snapshot item
    // is never selected during that race.
    if (!snapshot_surface_.empty() &&
        (snapshot_expiry_deadline_tick_ == 0 ||
         GetTickCount64() >= snapshot_expiry_deadline_tick_)) {
      ExpireSnapshotSurface();
    }
    const int x = GET_X_LPARAM(data);
    const int y = GET_Y_LPARAM(data);
    RECT bounds{};
    GetClientRect(window_, &bounds);
    const auto target = clipvault::ime::candidate_layout::HitTest(
        {x, y}, bounds.right - bounds.left, layout_metrics_, candidates_.size(),
        snapshot_surface_.candidates.size(), has_previous_page_,
        has_next_page_);
    switch (target.kind) {
      case clipvault::ime::candidate_layout::HitKind::kEngineCandidate:
        if (target.index < candidates_.size()) select_(target.index);
        break;
      case clipvault::ime::candidate_layout::HitKind::kPreviousPage:
        page_(true);
        break;
      case clipvault::ime::candidate_layout::HitKind::kNextPage:
        page_(false);
        break;
      case clipvault::ime::candidate_layout::HitKind::kSnapshotCandidate:
        if (target.index < snapshot_surface_.candidates.size()) {
          select_snapshot_(snapshot_surface_.publisher_epoch,
                           snapshot_surface_.generation,
                           snapshot_surface_.candidates[target.index]
                               .candidate_id);
        }
        break;
      case clipvault::ime::candidate_layout::HitKind::kNone:
        break;
    }
    return 0;
  }
  if (message == WM_NCDESTROY) {
    const HWND destroyed_window = window_;
    StopSnapshotExpiryTimer();
    candidates_.clear();
    ClearSnapshotSurface();
    ResetSnapshotDeadlineIdentity();
    SetWindowLongPtrW(destroyed_window, GWLP_USERDATA, 0);
    window_ = nullptr;
    return DefWindowProcW(destroyed_window, message, word, data);
  }
  return DefWindowProcW(window_, message, word, data);
}

LRESULT CALLBACK CandidateWindow::WindowProcedure(HWND window, UINT message,
                                                   WPARAM word, LPARAM data) {
  auto* self = reinterpret_cast<CandidateWindow*>(
      GetWindowLongPtrW(window, GWLP_USERDATA));
  if (message == WM_NCCREATE) {
    const auto* create = reinterpret_cast<const CREATESTRUCTW*>(data);
    self = static_cast<CandidateWindow*>(create->lpCreateParams);
    self->window_ = window;
    SetWindowLongPtrW(window, GWLP_USERDATA,
                      reinterpret_cast<LONG_PTR>(self));
  }
  return self != nullptr ? self->HandleMessage(message, word, data)
                         : DefWindowProcW(window, message, word, data);
}
