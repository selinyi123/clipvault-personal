#include "otp_prompt.h"

namespace clipvault::otp::broker {
namespace {
constexpr UINT kNotifyMessage = WM_APP + 71;
constexpr UINT_PTR kHideTimer = 1;
constexpr wchar_t kWindowClass[] = L"ClipVaultOtpPromptV1";
constexpr wchar_t kReadyText[] =
    L"\u9a8c\u8bc1\u7801\u5df2\u5c31\u7eea \u00b7 "
    L"\u5728\u76ee\u6807\u8f93\u5165\u6846\u6309 Ctrl+Alt+O";
}  // namespace

NonActivatingOtpPrompt::~NonActivatingOtpPrompt() { Stop(); }

bool NonActivatingOtpPrompt::Start() {
  if (thread_.joinable()) return window_.load() != nullptr;
  ready_event_ = CreateEventW(nullptr, TRUE, FALSE, nullptr);
  if (ready_event_ == nullptr) return false;
  try {
    thread_ = std::jthread(
        [this](std::stop_token stop_token) { Run(stop_token); });
  } catch (...) {
    CloseHandle(ready_event_);
    ready_event_ = nullptr;
    return false;
  }
  if (WaitForSingleObject(ready_event_, 2'000) != WAIT_OBJECT_0 ||
      window_.load() == nullptr) {
    Stop();
    return false;
  }
  return true;
}

void NonActivatingOtpPrompt::NotifyOtpReady() noexcept {
  const HWND window = window_.load();
  if (window != nullptr) PostMessageW(window, kNotifyMessage, 0, 0);
}

void NonActivatingOtpPrompt::Stop() noexcept {
  if (thread_.joinable()) thread_.request_stop();
  const HWND window = window_.load();
  if (window != nullptr) PostMessageW(window, WM_CLOSE, 0, 0);
  if (thread_.joinable()) thread_.join();
  if (ready_event_ != nullptr) {
    CloseHandle(ready_event_);
    ready_event_ = nullptr;
  }
}

void NonActivatingOtpPrompt::Run(std::stop_token stop_token) noexcept {
  WNDCLASSW window_class{};
  window_class.lpfnWndProc = WindowProc;
  window_class.hInstance = GetModuleHandleW(nullptr);
  window_class.lpszClassName = kWindowClass;
  window_class.hbrBackground = reinterpret_cast<HBRUSH>(COLOR_WINDOW + 1);
  RegisterClassW(&window_class);
  HWND window = CreateWindowExW(
      WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST, kWindowClass,
      L"ClipVault OTP", WS_POPUP | WS_BORDER, 0, 0, 360, 72, nullptr, nullptr,
      window_class.hInstance, nullptr);
  if (window != nullptr) {
    CreateWindowExW(0, L"STATIC", kReadyText,
                    WS_CHILD | WS_VISIBLE | SS_CENTER, 8, 18, 344, 32, window,
                    nullptr, window_class.hInstance, nullptr);
    SetWindowDisplayAffinity(window, WDA_EXCLUDEFROMCAPTURE);
  }
  window_.store(window);
  SetEvent(ready_event_);
  // Start() can time out before the HWND becomes visible to Stop(). Honor the
  // jthread stop request after publishing readiness so that Stop()->join()
  // cannot wait forever on a newly entered GetMessage loop.
  if (stop_token.stop_requested()) {
    if (window != nullptr) DestroyWindow(window);
    window = nullptr;
  }
  MSG message{};
  while (window != nullptr && GetMessageW(&message, nullptr, 0, 0) > 0) {
    TranslateMessage(&message);
    DispatchMessageW(&message);
  }
  window_.store(nullptr);
  UnregisterClassW(kWindowClass, window_class.hInstance);
}

LRESULT CALLBACK NonActivatingOtpPrompt::WindowProc(
    HWND window, UINT message, WPARAM wparam, LPARAM lparam) {
  if (message == kNotifyMessage) {
    RECT work{};
    SystemParametersInfoW(SPI_GETWORKAREA, 0, &work, 0);
    SetWindowPos(window, HWND_TOPMOST, work.right - 376, work.bottom - 96,
                 360, 72, SWP_NOACTIVATE | SWP_SHOWWINDOW);
    SetTimer(window, kHideTimer, 15'000, nullptr);
    return 0;
  }
  if (message == WM_TIMER && wparam == kHideTimer) {
    KillTimer(window, kHideTimer);
    ShowWindow(window, SW_HIDE);
    return 0;
  }
  if (message == WM_MOUSEACTIVATE) return MA_NOACTIVATE;
  if (message == WM_CLOSE) {
    DestroyWindow(window);
    return 0;
  }
  if (message == WM_DESTROY) {
    PostQuitMessage(0);
    return 0;
  }
  return DefWindowProcW(window, message, wparam, lparam);
}

}  // namespace clipvault::otp::broker
