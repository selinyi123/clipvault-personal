#include "otp_prompt.h"

namespace clipvault::otp::broker {
namespace {
constexpr UINT kNotifyMessage = WM_APP + 71;
constexpr UINT_PTR kHideTimer = 1;
constexpr wchar_t kWindowClass[] = L"ClipVaultOtpPromptV1";
}

NonActivatingOtpPrompt::~NonActivatingOtpPrompt() { Stop(); }

bool NonActivatingOtpPrompt::Start() {
  if (thread_.joinable()) return window_.load() != nullptr;
  ready_event_ = CreateEventW(nullptr, TRUE, FALSE, nullptr);
  if (ready_event_ == nullptr) return false;
  thread_ = std::jthread([this] { Run(); });
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
  const HWND window = window_.exchange(nullptr);
  if (window != nullptr) PostMessageW(window, WM_CLOSE, 0, 0);
  if (thread_.joinable()) thread_.join();
  if (ready_event_ != nullptr) {
    CloseHandle(ready_event_);
    ready_event_ = nullptr;
  }
}

void NonActivatingOtpPrompt::Run() {
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
    CreateWindowExW(0, L"STATIC",
                    L"验证码已就绪 · 在目标输入框按 Ctrl+Alt+O",
                    WS_CHILD | WS_VISIBLE | SS_CENTER, 8, 18, 344, 32, window,
                    nullptr, window_class.hInstance, nullptr);
    SetWindowDisplayAffinity(window, WDA_EXCLUDEFROMCAPTURE);
  }
  window_.store(window);
  SetEvent(ready_event_);
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
