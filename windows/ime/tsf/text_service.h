#pragma once

#include "candidate_window.h"
#include "protocol.h"

#include <msctf.h>

#include <atomic>

class TextService final : public ITfTextInputProcessorEx, public ITfKeyEventSink {
 public:
  TextService();

  STDMETHODIMP QueryInterface(REFIID interface_id, void** object) override;
  STDMETHODIMP_(ULONG) AddRef() override;
  STDMETHODIMP_(ULONG) Release() override;

  STDMETHODIMP Activate(ITfThreadMgr* thread_manager, TfClientId client_id) override;
  STDMETHODIMP Deactivate() override;
  STDMETHODIMP ActivateEx(ITfThreadMgr* thread_manager, TfClientId client_id,
                          DWORD flags) override;

  STDMETHODIMP OnSetFocus(BOOL foreground) override;
  STDMETHODIMP OnTestKeyDown(ITfContext* context, WPARAM key, LPARAM key_data,
                             BOOL* eaten) override;
  STDMETHODIMP OnTestKeyUp(ITfContext* context, WPARAM key, LPARAM key_data,
                           BOOL* eaten) override;
  STDMETHODIMP OnKeyDown(ITfContext* context, WPARAM key, LPARAM key_data,
                         BOOL* eaten) override;
  STDMETHODIMP OnKeyUp(ITfContext* context, WPARAM key, LPARAM key_data,
                       BOOL* eaten) override;
  STDMETHODIMP OnPreservedKey(ITfContext* context, REFGUID key_guid,
                              BOOL* eaten) override;

 private:
  ~TextService();
  friend class ApplyStateEditSession;

  bool ShouldHandle(WPARAM key) const noexcept;
  clipvault::ime::InputContext ClassifyInputContext(
      ITfContext* context) const noexcept;
  bool EnsureEngine(const clipvault::ime::InputContext& input_context);
  bool LaunchHost() const;
  clipvault::ime::KeyEvent TranslateKey(WPARAM key, LPARAM key_data) const;
  HRESULT ApplyState(ITfContext* context, const clipvault::ime::EngineState& state);
  HRESULT ApplyAndPresent(ITfContext* context,
                          const clipvault::ime::EngineState& state);
  void CaptureCandidateAnchor(TfEditCookie cookie, ITfContext* context) noexcept;
  void CaptureContext(ITfContext* context) noexcept;
  bool IsCurrentContext(ITfContext* context) const noexcept;
  bool BuildOtpContext(ITfContext* context,
                       clipvault::ime::OtpContextBinding* binding) const noexcept;
  void InsertLatestOtp(ITfContext* context) noexcept;
  void ResetOtpContext() noexcept;
  void SelectCandidate(std::size_t index);
  void SelectSnapshotCandidate(const std::string& publisher_epoch,
                               std::uint64_t generation,
                               const std::string& candidate_id);
  void ChangeCandidatePage(bool backward);
  bool RecoverPlainKey(ITfContext* context, WPARAM key, LPARAM key_data,
                       clipvault::ime::EngineState* state);
  bool PreservePreeditLiteral(ITfContext* context) noexcept;
  bool CanBufferKey(WPARAM key) const noexcept;
  bool BufferLocalKey(ITfContext* context, WPARAM key, LPARAM key_data) noexcept;
  bool ReplayBufferedPreedit(clipvault::ime::EngineState* state) noexcept;
  void RetireComposition(ITfContext* context) noexcept;
  void ResetEngine() noexcept;

  std::atomic<ULONG> references_{1};
  ITfThreadMgr* thread_manager_ = nullptr;
  TfClientId client_id_ = TF_CLIENTID_NULL;
  ITfComposition* composition_ = nullptr;
  ITfContext* active_context_ = nullptr;
  clipvault::ime::PipeEngineClient engine_;
  clipvault::ime::EngineState last_state_;
  CandidateWindow candidate_window_;
  RECT candidate_anchor_{};
  bool candidate_anchor_valid_ = false;
  bool host_launch_attempted_ = false;
  bool session_started_ = false;
  bool composition_active_ = false;
  std::wstring pending_preedit_;
  clipvault::ime::InputContext input_context_;
  std::array<std::uint8_t, 16> otp_document_token_{};
  std::array<std::uint8_t, 16> otp_context_token_{};
  bool otp_key_preserved_ = false;
};
