#include "candidate_window.h"

#include <windowsx.h>

#include <algorithm>
#include <iterator>
#include <string>
#include <utility>

namespace {

constexpr wchar_t kCandidateWindowClass[] =
    L"ClipVaultCandidateWindow.C5CEE00A05AD4ABA93BB6E76932AF126";

int ScaleForDpi(int value, UINT dpi) {
  return MulDiv(value, static_cast<int>(dpi), 96);
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
      anchor.y = screen.top + ScaleForDpi(36, 96);
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
  if (!EnsureWindow()) return false;
  candidates_ = state.candidates;
  if (candidates_.size() > 9) candidates_.resize(9);
  snapshot_surface_ = state.snapshot_surface;
  if (snapshot_surface_.candidates.size() > 8)
    snapshot_surface_.candidates.resize(8);
  page_index_ = state.page_index;
  has_previous_page_ = state.has_previous_page;
  has_next_page_ = state.has_next_page;
  const UINT dpi = std::max<UINT>(96, GetDpiForWindow(window_));
  row_height_ = ScaleForDpi(30, dpi);
  header_height_ = ScaleForDpi(24, dpi);
  footer_height_ = ScaleForDpi(24, dpi);
  const int width = ScaleForDpi(440, dpi);
  const int height =
      row_height_ * static_cast<int>(candidates_.size()) +
      (candidates_.empty() ? 0 : footer_height_) +
      (snapshot_surface_.empty() ? 0 : header_height_) +
      row_height_ * static_cast<int>(snapshot_surface_.candidates.size());
  POINT anchor = ResolveAnchor(context, text_extent);
  RECT desired{anchor.x, anchor.y, anchor.x + width, anchor.y + height};
  MONITORINFO monitor{sizeof(MONITORINFO)};
  const HMONITOR handle = MonitorFromRect(&desired, MONITOR_DEFAULTTONEAREST);
  if (GetMonitorInfoW(handle, &monitor)) {
    anchor.x = std::clamp(anchor.x, monitor.rcWork.left,
                          std::max(monitor.rcWork.left, monitor.rcWork.right - width));
    if (anchor.y + height > monitor.rcWork.bottom)
      anchor.y = std::max(monitor.rcWork.top, anchor.y - height - row_height_);
  }
  SetWindowPos(window_, HWND_TOPMOST, anchor.x, anchor.y, width, height,
               SWP_NOACTIVATE | SWP_SHOWWINDOW);
  InvalidateRect(window_, nullptr, TRUE);
  return true;
}

void CandidateWindow::Hide() noexcept {
  candidates_.clear();
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
  if (window_ != nullptr) ShowWindow(window_, SW_HIDE);
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
    RECT row{ScaleForDpi(10, GetDpiForWindow(window_)),
             static_cast<LONG>(index * static_cast<std::size_t>(row_height_)),
             client.right - ScaleForDpi(8, GetDpiForWindow(window_)),
             static_cast<LONG>((index + 1) * static_cast<std::size_t>(row_height_))};
    std::wstring label = std::to_wstring(index + 1) + L". " +
                         candidates_[index].text;
    if (!candidates_[index].comment.empty())
      label += L"   " + candidates_[index].comment;
    DrawTextW(device, label.c_str(), static_cast<int>(label.size()), &row,
              DT_SINGLELINE | DT_VCENTER | DT_END_ELLIPSIS | DT_NOPREFIX);
  }
  LONG offset = static_cast<LONG>(candidates_.size() *
                                  static_cast<std::size_t>(row_height_));
  if (!candidates_.empty()) {
    RECT footer{ScaleForDpi(10, GetDpiForWindow(window_)), offset,
                client.right - ScaleForDpi(8, GetDpiForWindow(window_)),
                offset + footer_height_};
    std::wstring page = L"Page " + std::to_wstring(page_index_ + 1);
    if (has_previous_page_) page = L"PgUp  |  " + page;
    if (has_next_page_) page += L"  |  PgDn";
    SetTextColor(device, GetSysColor(COLOR_GRAYTEXT));
    DrawTextW(device, page.c_str(), static_cast<int>(page.size()), &footer,
              DT_SINGLELINE | DT_VCENTER | DT_CENTER | DT_NOPREFIX);
    offset += footer_height_;
  }
  if (!snapshot_surface_.empty()) {
    RECT header{ScaleForDpi(10, GetDpiForWindow(window_)), offset,
                client.right - ScaleForDpi(8, GetDpiForWindow(window_)),
                offset + header_height_};
    SetTextColor(device, GetSysColor(COLOR_GRAYTEXT));
    constexpr wchar_t title[] = L"ClipVault";
    DrawTextW(device, title, static_cast<int>(std::size(title) - 1), &header,
              DT_SINGLELINE | DT_VCENTER | DT_NOPREFIX);
    offset += header_height_;
    SetTextColor(device, GetSysColor(COLOR_WINDOWTEXT));
    for (const auto& candidate : snapshot_surface_.candidates) {
      RECT row{ScaleForDpi(10, GetDpiForWindow(window_)), offset,
               client.right - ScaleForDpi(8, GetDpiForWindow(window_)),
               offset + row_height_};
      std::wstring label = candidate.source == 1 ? L"Memory  " : L"Clipboard  ";
      if (!candidate.label.empty()) label += candidate.label + L"  ";
      label += candidate.text;
      DrawTextW(device, label.c_str(), static_cast<int>(label.size()), &row,
                DT_SINGLELINE | DT_VCENTER | DT_END_ELLIPSIS | DT_NOPREFIX);
      offset += row_height_;
    }
  }
  SelectObject(device, prior_font);
  EndPaint(window_, &paint);
}

LRESULT CandidateWindow::HandleMessage(UINT message, WPARAM word, LPARAM data) {
  if (message == WM_MOUSEACTIVATE) return MA_NOACTIVATE;
  if (message == WM_ERASEBKGND) return 1;
  if (message == WM_PAINT) {
    Paint();
    return 0;
  }
  if (message == WM_LBUTTONDOWN) {
    const int x = GET_X_LPARAM(data);
    const int y = GET_Y_LPARAM(data);
    const int candidate_height =
        row_height_ * static_cast<int>(candidates_.size());
    if (y >= 0 && y < candidate_height) {
      const auto index = static_cast<std::size_t>(y / row_height_);
      if (index < candidates_.size()) select_(index);
    } else if (!candidates_.empty() &&
               y < candidate_height + footer_height_) {
      RECT bounds{};
      GetClientRect(window_, &bounds);
      if (x < bounds.right / 2 && has_previous_page_) page_(true);
      if (x >= bounds.right / 2 && has_next_page_) page_(false);
    } else if (!snapshot_surface_.empty()) {
      const int snapshot_top = candidate_height +
                               (candidates_.empty() ? 0 : footer_height_) +
                               header_height_;
      if (y >= snapshot_top) {
        const auto index = static_cast<std::size_t>(
            (y - snapshot_top) / row_height_);
        if (index < snapshot_surface_.candidates.size()) {
          select_snapshot_(snapshot_surface_.publisher_epoch,
                           snapshot_surface_.generation,
                           snapshot_surface_.candidates[index].candidate_id);
        }
      }
    }
    return 0;
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
