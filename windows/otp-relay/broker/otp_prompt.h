#pragma once

#include <windows.h>

#include <atomic>
#include <thread>

namespace clipvault::otp::broker {

class OtpPromptSink {
 public:
  virtual ~OtpPromptSink() = default;
  virtual void NotifyOtpReady() noexcept = 0;
};

// Independent generic prompt. It never receives or renders the OTP value and
// is shown without activation; Ctrl+Alt+O in the focused TSF context performs
// the explicit one-use insertion.
class NonActivatingOtpPrompt final : public OtpPromptSink {
 public:
  NonActivatingOtpPrompt() = default;
  ~NonActivatingOtpPrompt();
  bool Start();
  void NotifyOtpReady() noexcept override;
  void Stop() noexcept;

 private:
  void Run();
  static LRESULT CALLBACK WindowProc(HWND window, UINT message, WPARAM wparam,
                                     LPARAM lparam);
  std::jthread thread_;
  HANDLE ready_event_ = nullptr;
  std::atomic<HWND> window_{nullptr};
};

}  // namespace clipvault::otp::broker
