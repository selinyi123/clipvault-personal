#include "text_service.h"

#include "diagnostics.h"
#include "globals.h"
#include "key_translation.h"
#include "recovery_policy.h"

#include <InputScope.h>
#include <rpc.h>

#include <algorithm>
#include <array>
#include <cwctype>
#include <new>
#include <string>

namespace {

constexpr DWORD kInitialHostLaunchBackoffMilliseconds = 250;
constexpr DWORD kHostStartupGraceMilliseconds = 1000;
constexpr DWORD kMaximumHostLaunchBackoffMilliseconds = 30'000;
constexpr std::size_t kMaximumBufferedCharacters = 32;

enum class LocalBufferAction {
  kReject,
  kAppendLetter,
  kCommitFullBufferWithLetter,
  kBackspace,
  kCancel,
  kCommit,
};

LocalBufferAction PlanLocalBufferAction(WPARAM key, bool control, bool alt,
                                        std::size_t buffered_characters) noexcept {
  if (!control && !alt && key >= 'A' && key <= 'Z') {
    return buffered_characters < kMaximumBufferedCharacters
               ? LocalBufferAction::kAppendLetter
               : LocalBufferAction::kCommitFullBufferWithLetter;
  }
  if (buffered_characters == 0) return LocalBufferAction::kReject;
  if (key == VK_BACK) return LocalBufferAction::kBackspace;
  if (key == VK_ESCAPE) return LocalBufferAction::kCancel;
  if (key == VK_SPACE || key == VK_RETURN) return LocalBufferAction::kCommit;
  return LocalBufferAction::kReject;
}

std::wstring HostMutexNameForCurrentSession() {
  DWORD session_id = 0;
  if (!ProcessIdToSessionId(GetCurrentProcessId(), &session_id)) return {};
  return L"Local\\ClipVaultImeHostV2-" + std::to_wstring(session_id) +
         clipvault::ime::LocalTestNamespaceSuffix();
}

bool HostInstanceIsRunning() {
  const auto mutex_name = HostMutexNameForCurrentSession();
  if (mutex_name.empty()) return true;
  HANDLE mutex = OpenMutexW(SYNCHRONIZE, FALSE, mutex_name.c_str());
  if (mutex != nullptr) {
    CloseHandle(mutex);
    return true;
  }
  // Only a definitely absent mutex authorizes another CreateProcess attempt.
  // Access failures and other namespace errors fail closed to avoid a launch
  // loop inside every process that has the TSF DLL loaded.
  return GetLastError() != ERROR_FILE_NOT_FOUND;
}

void WipeSnapshotSurface(
    clipvault::ime::SnapshotSurface* surface) noexcept {
  if (surface == nullptr) return;
  std::fill(surface->publisher_epoch.begin(), surface->publisher_epoch.end(),
            '\0');
  surface->publisher_epoch.clear();
  surface->generation = 0;
  surface->expires_at_ms = 0;
  for (auto& candidate : surface->candidates) {
    std::fill(candidate.candidate_id.begin(), candidate.candidate_id.end(),
              '\0');
    std::fill(candidate.label.begin(), candidate.label.end(), L'\0');
    std::fill(candidate.text.begin(), candidate.text.end(), L'\0');
  }
  surface->candidates.clear();
}

void WipeCommitText(clipvault::ime::EngineState* state) noexcept {
  if (state == nullptr || !state->commit_text.has_value()) return;
  if (!state->commit_text->empty()) {
    SecureZeroMemory(state->commit_text->data(),
                     state->commit_text->size() * sizeof(wchar_t));
  }
  state->commit_text.reset();
}

class CommitTextWipeGuard final {
 public:
  explicit CommitTextWipeGuard(clipvault::ime::EngineState* state) noexcept
      : state_(state) {}
  ~CommitTextWipeGuard() { WipeCommitText(state_); }

  CommitTextWipeGuard(const CommitTextWipeGuard&) = delete;
  CommitTextWipeGuard& operator=(const CommitTextWipeGuard&) = delete;

 private:
  clipvault::ime::EngineState* state_ = nullptr;
};

}  // namespace

const GUID kOtpInsertPreservedKey = {
    0x9a29af53, 0xa42c, 0x4e3f,
    {0x99, 0x93, 0x89, 0x42, 0x47, 0xb4, 0xb7, 0x0d}};

bool NewUuidBytes(std::array<std::uint8_t, 16>* output) {
  if (output == nullptr) return false;
  UUID value{};
  const RPC_STATUS status = UuidCreate(&value);
  if (status != RPC_S_OK && status != RPC_S_UUID_LOCAL_ONLY) return false;
  (*output)[0] = static_cast<std::uint8_t>(value.Data1 >> 24);
  (*output)[1] = static_cast<std::uint8_t>(value.Data1 >> 16);
  (*output)[2] = static_cast<std::uint8_t>(value.Data1 >> 8);
  (*output)[3] = static_cast<std::uint8_t>(value.Data1);
  (*output)[4] = static_cast<std::uint8_t>(value.Data2 >> 8);
  (*output)[5] = static_cast<std::uint8_t>(value.Data2);
  (*output)[6] = static_cast<std::uint8_t>(value.Data3 >> 8);
  (*output)[7] = static_cast<std::uint8_t>(value.Data3);
  std::copy_n(value.Data4, 8, output->begin() + 8);
  return ((*output)[6] & 0xf0U) == 0x40U &&
         ((*output)[8] & 0xc0U) == 0x80U;
}

bool InputDesktopIsUnlocked() {
  HDESK desktop = OpenInputDesktop(0, FALSE, DESKTOP_READOBJECTS);
  if (desktop == nullptr) return false;
  std::array<wchar_t, 64> name{};
  DWORD required = 0;
  const bool read = GetUserObjectInformationW(
                        desktop, UOI_NAME, name.data(),
                        static_cast<DWORD>(name.size() * sizeof(wchar_t)),
                        &required) != FALSE;
  CloseDesktop(desktop);
  return read && _wcsicmp(name.data(), L"Default") == 0;
}

template <typename T>
void SafeRelease(T** value) noexcept {
  if (*value != nullptr) {
    (*value)->Release();
    *value = nullptr;
  }
}

bool SameComIdentity(IUnknown* left, IUnknown* right) noexcept {
  if (left == nullptr || right == nullptr) return false;
  IUnknown* left_identity = nullptr;
  IUnknown* right_identity = nullptr;
  const bool available =
      SUCCEEDED(left->QueryInterface(IID_PPV_ARGS(&left_identity))) &&
      SUCCEEDED(right->QueryInterface(IID_PPV_ARGS(&right_identity)));
  const bool same = available && left_identity == right_identity;
  SafeRelease(&left_identity);
  SafeRelease(&right_identity);
  return same;
}

DWORD RemainingBudget(ULONGLONG deadline) noexcept {
  const ULONGLONG now = GetTickCount64();
  if (now >= deadline) return 0;
  return static_cast<DWORD>(
      std::min<ULONGLONG>(deadline - now, static_cast<ULONGLONG>(MAXDWORD)));
}

class ApplyStateEditSession final : public ITfEditSession {
 public:
  ApplyStateEditSession(TextService* service, ITfContext* context,
                        const clipvault::ime::EngineState& state)
      : service_(service), context_(context), state_(state) {
    service_->AddRef();
    context_->AddRef();
  }

  STDMETHODIMP QueryInterface(REFIID interface_id, void** object) override {
    if (object == nullptr) return E_INVALIDARG;
    *object = nullptr;
    if (IsEqualIID(interface_id, IID_IUnknown) ||
        IsEqualIID(interface_id, IID_ITfEditSession)) {
      *object = static_cast<ITfEditSession*>(this);
      AddRef();
      return S_OK;
    }
    return E_NOINTERFACE;
  }

  STDMETHODIMP_(ULONG) AddRef() override { return ++references_; }
  STDMETHODIMP_(ULONG) Release() override {
    const ULONG remaining = --references_;
    if (remaining == 0) delete this;
    return remaining;
  }

  STDMETHODIMP DoEditSession(TfEditCookie cookie) override {
    HRESULT result = S_OK;
    if (state_.commit_text.has_value()) {
      result = Commit(cookie, *state_.commit_text);
    } else if (!state_.preedit.empty()) {
      result = SetPreedit(cookie);
    } else {
      result = ClearComposition(cookie);
    }
    service_->candidate_anchor_valid_ = false;
    if (SUCCEEDED(result) && state_.composition_active)
      service_->CaptureCandidateAnchor(cookie, context_);
    return result;
  }

 private:
  ~ApplyStateEditSession() {
    // OTP insertion uses the same synchronous edit-session machinery as
    // ordinary commits. Erase the session-owned copy before its string storage
    // is released; the caller separately owns and wipes the source state.
    WipeCommitText(&state_);
    context_->Release();
    service_->Release();
  }

  HRESULT StartComposition(TfEditCookie cookie) {
    if (service_->composition_ != nullptr) return S_OK;
    ITfInsertAtSelection* insert_at_selection = nullptr;
    HRESULT result =
        context_->QueryInterface(IID_PPV_ARGS(&insert_at_selection));
    if (FAILED(result)) {
      clipvault::ime::EmitDiagnostic(
          clipvault::ime::DiagnosticEvent::kInsertInterfaceFailed,
          static_cast<std::uint32_t>(result));
      return result;
    }

    // A context may adjust the actual insertion range. Starting a composition
    // on the raw selection can fail with E_INVALIDARG even for a writable EDIT
    // control. Query the insertion range exactly as Microsoft SampleIME does.
    ITfRange* insertion_range = nullptr;
    result = insert_at_selection->InsertTextAtSelection(
        cookie, TF_IAS_QUERYONLY, nullptr, 0, &insertion_range);
    insert_at_selection->Release();
    if (FAILED(result) || insertion_range == nullptr) {
      const HRESULT failure = FAILED(result) ? result : E_FAIL;
      clipvault::ime::EmitDiagnostic(
          clipvault::ime::DiagnosticEvent::kInsertionRangeFailed,
          static_cast<std::uint32_t>(failure));
      SafeRelease(&insertion_range);
      return failure;
    }

    ITfContextComposition* compositions = nullptr;
    result = context_->QueryInterface(IID_PPV_ARGS(&compositions));
    if (SUCCEEDED(result)) {
      result = compositions->StartComposition(
          cookie, insertion_range,
          static_cast<ITfCompositionSink*>(service_), &service_->composition_);
      compositions->Release();
    } else {
      clipvault::ime::EmitDiagnostic(
          clipvault::ime::DiagnosticEvent::kCompositionInterfaceFailed,
          static_cast<std::uint32_t>(result));
    }
    if (SUCCEEDED(result) && service_->composition_ == nullptr) result = E_FAIL;
    if (FAILED(result) && compositions != nullptr) {
      clipvault::ime::EmitDiagnostic(
          clipvault::ime::DiagnosticEvent::kCompositionStartFailed,
          static_cast<std::uint32_t>(result));
    }
    insertion_range->Release();
    return result;
  }

  HRESULT SetPreedit(TfEditCookie cookie) {
    HRESULT result = StartComposition(cookie);
    if (FAILED(result)) return result;
    ITfRange* range = nullptr;
    result = service_->composition_->GetRange(&range);
    if (FAILED(result)) {
      clipvault::ime::EmitDiagnostic(
          clipvault::ime::DiagnosticEvent::kCompositionRangeFailed,
          static_cast<std::uint32_t>(result));
      return result;
    }
    result = range->SetText(cookie, 0, state_.preedit.c_str(),
                            static_cast<LONG>(state_.preedit.size()));
    if (FAILED(result)) {
      clipvault::ime::EmitDiagnostic(
          clipvault::ime::DiagnosticEvent::kCompositionTextFailed,
          static_cast<std::uint32_t>(result));
    }
    if (SUCCEEDED(result)) {
      ITfRange* caret = nullptr;
      result = range->Clone(&caret);
      if (FAILED(result)) {
        clipvault::ime::EmitDiagnostic(
            clipvault::ime::DiagnosticEvent::kCompositionCaretFailed,
            static_cast<std::uint32_t>(result));
      }
      if (SUCCEEDED(result)) {
        result = caret->Collapse(cookie, TF_ANCHOR_START);
        if (FAILED(result)) {
          clipvault::ime::EmitDiagnostic(
              clipvault::ime::DiagnosticEvent::kCompositionCaretFailed,
              static_cast<std::uint32_t>(result));
        }
        if (SUCCEEDED(result) && state_.caret_utf16 > 0) {
          LONG shifted = 0;
          result = caret->ShiftEnd(cookie, static_cast<LONG>(state_.caret_utf16),
                                   &shifted, nullptr);
          if (SUCCEEDED(result) &&
              shifted != static_cast<LONG>(state_.caret_utf16)) result = E_FAIL;
          if (FAILED(result)) {
            clipvault::ime::EmitDiagnostic(
                clipvault::ime::DiagnosticEvent::kCompositionCaretFailed,
                static_cast<std::uint32_t>(result));
          }
        }
        if (SUCCEEDED(result)) {
          result = caret->Collapse(cookie, TF_ANCHOR_END);
          if (FAILED(result)) {
            clipvault::ime::EmitDiagnostic(
                clipvault::ime::DiagnosticEvent::kCompositionCaretFailed,
                static_cast<std::uint32_t>(result));
          }
        }
        if (SUCCEEDED(result)) {
          TF_SELECTION selection{};
          selection.range = caret;
          selection.style.ase = TF_AE_NONE;
          selection.style.fInterimChar = FALSE;
          result = context_->SetSelection(cookie, 1, &selection);
          if (FAILED(result)) {
            clipvault::ime::EmitDiagnostic(
                clipvault::ime::DiagnosticEvent::kCompositionSelectionFailed,
                static_cast<std::uint32_t>(result));
          }
        }
        caret->Release();
      }
    }
    range->Release();
    return result;
  }

  HRESULT Commit(TfEditCookie cookie, const std::wstring& text) {
    if (service_->composition_ == nullptr) {
      TF_SELECTION selection{};
      ULONG fetched = 0;
      HRESULT result = context_->GetSelection(cookie, TF_DEFAULT_SELECTION, 1,
                                               &selection, &fetched);
      if (FAILED(result) || fetched != 1) return FAILED(result) ? result : E_FAIL;
      result = selection.range->SetText(cookie, 0, text.c_str(),
                                        static_cast<LONG>(text.size()));
      selection.range->Release();
      return result;
    }
    ITfRange* range = nullptr;
    HRESULT result = service_->composition_->GetRange(&range);
    if (SUCCEEDED(result)) {
      result = range->SetText(cookie, 0, text.c_str(), static_cast<LONG>(text.size()));
      range->Release();
    }
    if (SUCCEEDED(result)) {
      result = service_->composition_->EndComposition(cookie);
      if (SUCCEEDED(result)) SafeRelease(&service_->composition_);
    }
    return result;
  }

  HRESULT ClearComposition(TfEditCookie cookie) {
    if (service_->composition_ == nullptr) return S_OK;
    ITfRange* range = nullptr;
    HRESULT result = service_->composition_->GetRange(&range);
    if (SUCCEEDED(result)) {
      result = range->SetText(cookie, 0, L"", 0);
      range->Release();
    }
    if (SUCCEEDED(result)) {
      result = service_->composition_->EndComposition(cookie);
      if (SUCCEEDED(result)) SafeRelease(&service_->composition_);
    }
    return result;
  }

  std::atomic<ULONG> references_{1};
  TextService* service_;
  ITfContext* context_;
  clipvault::ime::EngineState state_;
};

// OTP projection intentionally does not reuse ApplyStateEditSession. Copying a
// full EngineState can allocate after commit_text has already been copied; if a
// later member copy then throws, the containing class destructor never runs
// and the partially constructed OTP string cannot be explicitly erased. This
// narrow session copies into a fixed wipeable buffer and immediately erases
// the source. A std::wstring move is insufficient here because short-string
// optimization can copy 4-8 characters while leaving the source inline buffer
// intact even after its logical size becomes zero.
class OtpCommitEditSession final : public ITfEditSession {
 public:
  OtpCommitEditSession(TextService* service, ITfContext* context,
                       std::wstring* text) noexcept
      : service_(service), context_(context) {
    if (text != nullptr) {
      text_length_ = std::min(text->size(), text_.size());
      std::copy_n(text->data(), text_length_, text_.begin());
      if (!text->empty()) {
        SecureZeroMemory(text->data(), text->size() * sizeof(wchar_t));
        text->clear();
      }
    }
    service_->AddRef();
    context_->AddRef();
  }

  STDMETHODIMP QueryInterface(REFIID interface_id, void** object) override {
    if (object == nullptr) return E_INVALIDARG;
    *object = nullptr;
    if (IsEqualIID(interface_id, IID_IUnknown) ||
        IsEqualIID(interface_id, IID_ITfEditSession)) {
      *object = static_cast<ITfEditSession*>(this);
      AddRef();
      return S_OK;
    }
    return E_NOINTERFACE;
  }

  STDMETHODIMP_(ULONG) AddRef() override { return ++references_; }
  STDMETHODIMP_(ULONG) Release() override {
    const ULONG remaining = --references_;
    if (remaining == 0) delete this;
    return remaining;
  }

  STDMETHODIMP DoEditSession(TfEditCookie cookie) override {
    TF_SELECTION selection{};
    ULONG fetched = 0;
    try {
      const HRESULT selected = context_->GetSelection(
          cookie, TF_DEFAULT_SELECTION, 1, &selection, &fetched);
      if (FAILED(selected) || fetched != 1 || selection.range == nullptr) {
        SafeRelease(&selection.range);
        return FAILED(selected) ? selected : E_FAIL;
      }
      const HRESULT inserted = selection.range->SetText(
          cookie, 0, text_.data(), static_cast<LONG>(text_length_));
      selection.range->Release();
      return inserted;
    } catch (...) {
      SafeRelease(&selection.range);
      return E_FAIL;
    }
  }

 private:
  ~OtpCommitEditSession() {
    SecureZeroMemory(text_.data(), text_.size() * sizeof(wchar_t));
    text_length_ = 0;
    context_->Release();
    service_->Release();
  }

  std::atomic<ULONG> references_{1};
  TextService* service_ = nullptr;
  ITfContext* context_ = nullptr;
  std::array<wchar_t, 8> text_{};
  std::size_t text_length_ = 0;
};

TextService::TextService()
    : candidate_window_(
          g_module_instance,
          [this](std::size_t index) { SelectCandidate(index); },
          [this](bool backward) { ChangeCandidatePage(backward); },
          [this](const std::string& publisher_epoch,
                 std::uint64_t generation,
                 const std::string& candidate_id) {
            SelectSnapshotCandidate(publisher_epoch, generation,
                                    candidate_id);
          }) {
  ModuleAddRef();
}

TextService::~TextService() {
  Deactivate();
  SafeRelease(&active_context_);
  SafeRelease(&composition_);
  ModuleRelease();
}

STDMETHODIMP TextService::QueryInterface(REFIID interface_id, void** object) {
  if (object == nullptr) return E_INVALIDARG;
  *object = nullptr;
  if (IsEqualIID(interface_id, IID_IUnknown) ||
      IsEqualIID(interface_id, IID_ITfTextInputProcessor)) {
    *object = static_cast<ITfTextInputProcessor*>(this);
  } else if (IsEqualIID(interface_id, IID_ITfTextInputProcessorEx)) {
    *object = static_cast<ITfTextInputProcessorEx*>(this);
  } else if (IsEqualIID(interface_id, IID_ITfKeyEventSink)) {
    *object = static_cast<ITfKeyEventSink*>(this);
  } else if (IsEqualIID(interface_id, IID_ITfCompositionSink)) {
    *object = static_cast<ITfCompositionSink*>(this);
  } else {
    return E_NOINTERFACE;
  }
  AddRef();
  return S_OK;
}

STDMETHODIMP_(ULONG) TextService::AddRef() { return ++references_; }
STDMETHODIMP_(ULONG) TextService::Release() {
  const ULONG remaining = --references_;
  if (remaining == 0) delete this;
  return remaining;
}

STDMETHODIMP TextService::Activate(ITfThreadMgr* thread_manager, TfClientId client_id) {
  return ActivateEx(thread_manager, client_id, 0);
}

STDMETHODIMP TextService::ActivateEx(ITfThreadMgr* thread_manager, TfClientId client_id,
                                     DWORD) {
  if (thread_manager == nullptr || thread_manager_ != nullptr) return E_INVALIDARG;
  clipvault::ime::EmitDiagnostic(
      clipvault::ime::DiagnosticEvent::kTextServiceActivate);
  ITfKeystrokeMgr* keys = nullptr;
  HRESULT result = thread_manager->QueryInterface(IID_PPV_ARGS(&keys));
  if (FAILED(result)) {
    clipvault::ime::EmitDiagnostic(
        clipvault::ime::DiagnosticEvent::kKeySinkAdviseFailed,
        static_cast<std::uint32_t>(result));
    return result;
  }
  result = keys->AdviseKeyEventSink(client_id, this, TRUE);
  keys->Release();
  if (FAILED(result)) {
    clipvault::ime::EmitDiagnostic(
        clipvault::ime::DiagnosticEvent::kKeySinkAdviseFailed,
        static_cast<std::uint32_t>(result));
    return result;
  }
  clipvault::ime::EmitDiagnostic(
      clipvault::ime::DiagnosticEvent::kKeySinkAdvised);
  thread_manager_ = thread_manager;
  thread_manager_->AddRef();
  client_id_ = client_id;
  ITfKeystrokeMgr* preserved = nullptr;
  if (SUCCEEDED(thread_manager_->QueryInterface(IID_PPV_ARGS(&preserved)))) {
    TF_PRESERVEDKEY otp_key{};
    otp_key.uVKey = 'O';
    otp_key.uModifiers = TF_MOD_CONTROL | TF_MOD_ALT;
    otp_key_preserved_ =
        SUCCEEDED(preserved->PreserveKey(client_id_, kOtpInsertPreservedKey,
                                         &otp_key, L"输入手机验证码", 7));
    preserved->Release();
  }
  // Host startup and Rime initialization are separate. Activation permits one
  // bounded control-plane handshake after launching/prewarming the Host; it
  // never waits for Rime or dictionary maintenance on an application UI thread.
  MaybeLaunchHost();
  return S_OK;
}

STDMETHODIMP TextService::Deactivate() {
  clipvault::ime::EmitDiagnostic(
      clipvault::ime::DiagnosticEvent::kTextServiceDeactivate);
  candidate_window_.Hide();
  if (active_context_ != nullptr) {
    clipvault::ime::EngineState empty;
    ApplyState(active_context_, empty);
  }
  if (thread_manager_ != nullptr) {
    ITfKeystrokeMgr* keys = nullptr;
    if (SUCCEEDED(thread_manager_->QueryInterface(IID_PPV_ARGS(&keys)))) {
      if (otp_key_preserved_) {
        TF_PRESERVEDKEY otp_key{};
        otp_key.uVKey = 'O';
        otp_key.uModifiers = TF_MOD_CONTROL | TF_MOD_ALT;
        keys->UnpreserveKey(kOtpInsertPreservedKey, &otp_key);
      }
      keys->UnadviseKeyEventSink(client_id_);
      keys->Release();
    }
    thread_manager_->Release();
    thread_manager_ = nullptr;
  }
  client_id_ = TF_CLIENTID_NULL;
  SafeRelease(&active_context_);
  pending_preedit_.clear();
  input_context_ = clipvault::ime::InputContext{};
  otp_key_preserved_ = false;
  ResetOtpContext();
  ResetEngine();
  return S_OK;
}

STDMETHODIMP TextService::OnSetFocus(BOOL foreground) {
  if (!foreground) {
    ResetOtpContext();
    candidate_window_.Hide();
    if (composition_active_)
      RetireComposition(active_context_);
    else
      ResetEngine();
  }
  return S_OK;
}

bool TextService::ShouldHandle(WPARAM key) const noexcept {
  if (key >= 'A' && key <= 'Z') return true;
  if (key == VK_OEM_1 || key == VK_OEM_PLUS || key == VK_OEM_COMMA ||
      key == VK_OEM_MINUS || key == VK_OEM_PERIOD || key == VK_OEM_2 ||
      key == VK_OEM_3 || key == VK_OEM_4 || key == VK_OEM_5 ||
      key == VK_OEM_6 || key == VK_OEM_7) return true;
  if (!composition_active_) return false;
  if (key >= '1' && key <= '9')
    return static_cast<std::size_t>(key - '1') < last_state_.candidates.size();
  if (key == VK_PRIOR) return last_state_.has_previous_page;
  if (key == VK_NEXT) return last_state_.has_next_page;
  return key == VK_BACK || key == VK_SPACE || key == VK_RETURN ||
         key == VK_ESCAPE;
}

clipvault::ime::InputContext TextService::ClassifyInputContext(
    ITfContext* context) const noexcept {
  // Fail-safe defaults still permit private Rime composition, but never user
  // learning, ClipVault snapshots, or OTP surfaces.
  clipvault::ime::InputContext result;
  if (context == nullptr) return result;
  ITfInputScope* input_scope = nullptr;
  if (FAILED(context->QueryInterface(IID_PPV_ARGS(&input_scope)))) return result;
  InputScope* scopes = nullptr;
  UINT count = 0;
  const HRESULT read = input_scope->GetInputScopes(&scopes, &count);
  input_scope->Release();
  if (FAILED(read) || scopes == nullptr || count == 0) {
    CoTaskMemFree(scopes);
    return result;
  }

  result.field_kind = clipvault::ime::InputFieldKind::kText;
  result.incognito = false;
  result.learning_allowed = true;
  result.clipvault_allowed = true;
  for (UINT index = 0; index < count; ++index) {
    const InputScope scope = scopes[index];
    if (scope == IS_PASSWORD || scope == IS_NUMERIC_PASSWORD ||
        scope == IS_NUMERIC_PIN || scope == IS_ALPHANUMERIC_PIN ||
        scope == IS_ALPHANUMERIC_PIN_SET) {
      result.field_kind = clipvault::ime::InputFieldKind::kPassword;
      result.incognito = true;
      break;
    }
    if (scope == IS_PRIVATE) {
      result.incognito = true;
    } else if (scope == IS_URL) {
      result.field_kind = clipvault::ime::InputFieldKind::kUrl;
    } else if (scope == IS_EMAIL_USERNAME ||
               scope == IS_EMAIL_SMTPEMAILADDRESS ||
               scope == IS_EMAILNAME_OR_ADDRESS) {
      result.field_kind = clipvault::ime::InputFieldKind::kEmail;
    } else if (scope == IS_TELEPHONE_FULLTELEPHONENUMBER ||
               scope == IS_TELEPHONE_COUNTRYCODE ||
               scope == IS_TELEPHONE_AREACODE ||
               scope == IS_TELEPHONE_LOCALNUMBER) {
      result.field_kind = clipvault::ime::InputFieldKind::kPhone;
    } else if (scope == IS_DIGITS || scope == IS_NUMBER ||
               scope == IS_NUMBER_FULLWIDTH) {
      result.field_kind = clipvault::ime::InputFieldKind::kNumber;
    } else if (scope == IS_SEARCH || scope == IS_SEARCH_INCREMENTAL) {
      result.action = clipvault::ime::InputAction::kSearch;
    }
  }
  CoTaskMemFree(scopes);
  if (result.incognito ||
      result.field_kind == clipvault::ime::InputFieldKind::kPassword) {
    result.learning_allowed = false;
    result.clipvault_allowed = false;
  }
  return result;
}

bool TextService::LaunchHost() const {
  const auto directory = ModuleDirectory();
  if (directory.empty()) {
    clipvault::ime::EmitDiagnostic(
        clipvault::ime::DiagnosticEvent::kHostLaunchFailed, ERROR_PATH_NOT_FOUND);
    return false;
  }
  std::wstring executable = directory + L"\\ClipVaultImeHost.exe";
  if (GetFileAttributesW(executable.c_str()) == INVALID_FILE_ATTRIBUTES) {
    executable = directory + L"\\..\\host-x64\\ClipVaultImeHost.exe";
  }
  if (GetFileAttributesW(executable.c_str()) == INVALID_FILE_ATTRIBUTES) {
    clipvault::ime::EmitDiagnostic(
        clipvault::ime::DiagnosticEvent::kHostLaunchFailed, ERROR_FILE_NOT_FOUND);
    return false;
  }
  std::wstring command = L"\"" + executable + L"\"";
  STARTUPINFOW startup{sizeof(STARTUPINFOW)};
  PROCESS_INFORMATION process{};
  const BOOL created = CreateProcessW(
      executable.c_str(), command.data(), nullptr, nullptr, FALSE,
      CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP, nullptr, directory.c_str(),
      &startup, &process);
  if (!created) {
    clipvault::ime::EmitDiagnostic(
        clipvault::ime::DiagnosticEvent::kHostLaunchFailed, GetLastError());
    return false;
  }
  CloseHandle(process.hThread);
  CloseHandle(process.hProcess);
  clipvault::ime::EmitDiagnostic(
      clipvault::ime::DiagnosticEvent::kHostLaunchSucceeded);
  return true;
}

bool TextService::MaybeLaunchHost() {
  if (HostInstanceIsRunning()) return true;
  const ULONGLONG now = GetTickCount64();
  if (now < next_host_launch_tick_) return false;

  const bool launched = LaunchHost();
  const DWORD attempt_backoff = host_launch_backoff_milliseconds_;
  const DWORD delay = launched
                          ? std::max(kHostStartupGraceMilliseconds,
                                     attempt_backoff)
                          : attempt_backoff;
  next_host_launch_tick_ = now + delay;
  // CreateProcess only proves that a launch was attempted; the child can die
  // before acquiring its mutex or completing the pipe handshake. Advance the
  // backoff after every attempt and reset it only after StartSession succeeds.
  host_launch_backoff_milliseconds_ = std::min<DWORD>(
      attempt_backoff * 2, kMaximumHostLaunchBackoffMilliseconds);
  return launched;
}

bool TextService::EnsureEngine(
    const clipvault::ime::InputContext& input_context) {
  if (session_started_ && input_context_ != input_context) ResetEngine();
  input_context_ = input_context;
  if (engine_.connected() && session_started_) return true;
  constexpr DWORD kEnsureEngineBudgetMilliseconds = 30;
  const ULONGLONG deadline =
      GetTickCount64() + kEnsureEngineBudgetMilliseconds;
  if (!engine_.connected()) {
    // The per-session Host mutex is the source of truth. A live Host may be
    // starting or temporarily unable to accept a pipe, so never spawn another
    // process while that mutex exists. If the Host really exited, retry launch
    // under a monotonic exponential backoff; the Host mutex resolves races
    // among the many application processes that can load this TSF DLL.
    MaybeLaunchHost();
    // This runs on the host application's key path. A cold/deploying Host is
    // allowed to miss the key; it must never freeze the app for seconds.
    const DWORD connect_budget = RemainingBudget(deadline);
    if (connect_budget == 0 || !engine_.Connect(connect_budget)) return false;
  }
  clipvault::ime::EngineState state;
  const DWORD session_budget = RemainingBudget(deadline);
  session_started_ = session_budget != 0 &&
                     engine_.StartSession(input_context_, &state,
                                          session_budget);
  composition_active_ = false;
  if (session_started_) {
    next_host_launch_tick_ = 0;
    host_launch_backoff_milliseconds_ =
        kInitialHostLaunchBackoffMilliseconds;
  }
  return session_started_;
}

clipvault::ime::KeyEvent TextService::TranslateKey(WPARAM key, LPARAM key_data) const {
  clipvault::ime::KeyEvent event;
  event.virtual_key = static_cast<std::uint32_t>(key);
  event.repeat = (key_data & (1LL << 30)) != 0;
  event.shift = (GetKeyState(VK_SHIFT) & 0x8000) != 0;
  event.control = (GetKeyState(VK_CONTROL) & 0x8000) != 0;
  event.alt = (GetKeyState(VK_MENU) & 0x8000) != 0;
  if (!event.control && !event.alt) {
    BYTE keyboard_state[256]{};
    wchar_t translated[8]{};
    if (GetKeyboardState(keyboard_state)) {
      const UINT scan = static_cast<UINT>((key_data >> 16) & 0xff);
      const int count = ToUnicodeEx(static_cast<UINT>(key), scan, keyboard_state,
                                    translated, static_cast<int>(std::size(translated)),
                                    4, GetKeyboardLayout(0));
      if (count > 0) event.text.assign(translated, translated + count);
    }
  }
  if (event.text.empty() && key >= 'A' && key <= 'Z' && !event.control && !event.alt) {
    const bool caps_lock = (GetKeyState(VK_CAPITAL) & 1) != 0;
    const wchar_t base = clipvault::ime::LatinUppercase(event.shift, caps_lock)
                             ? L'A'
                             : L'a';
    event.text.assign(1, static_cast<wchar_t>(base + (key - 'A')));
  } else if (event.text.empty() && !event.control && !event.alt) {
    wchar_t value = L'\0';
    switch (key) {
      case VK_OEM_1: value = event.shift ? L':' : L';'; break;
      case VK_OEM_PLUS: value = event.shift ? L'+' : L'='; break;
      case VK_OEM_COMMA: value = event.shift ? L'<' : L','; break;
      case VK_OEM_MINUS: value = event.shift ? L'_' : L'-'; break;
      case VK_OEM_PERIOD: value = event.shift ? L'>' : L'.'; break;
      case VK_OEM_2: value = event.shift ? L'?' : L'/'; break;
      case VK_OEM_3: value = event.shift ? L'~' : L'`'; break;
      case VK_OEM_4: value = event.shift ? L'{' : L'['; break;
      case VK_OEM_5: value = event.shift ? L'|' : L'\\'; break;
      case VK_OEM_6: value = event.shift ? L'}' : L']'; break;
      case VK_OEM_7: value = event.shift ? L'"' : L'\''; break;
      default: break;
    }
    if (value != L'\0') event.text.assign(1, value);
  }
  return event;
}

STDMETHODIMP TextService::OnTestKeyDown(ITfContext* context, WPARAM key, LPARAM,
                                        BOOL* eaten) {
  if (eaten == nullptr) return E_INVALIDARG;
  const auto key_class =
      clipvault::ime::ClassifyKeyForDiagnostics(key, composition_active_);
  clipvault::ime::EmitDiagnostic(
      clipvault::ime::DiagnosticEvent::kTestKeyObserved,
      static_cast<std::uint32_t>(key_class));
  const auto input_context = ClassifyInputContext(context);
  if (input_context.field_kind ==
      clipvault::ime::InputFieldKind::kPassword) {
    candidate_window_.Hide();
    clipvault::ime::EmitDiagnostic(
        clipvault::ime::DiagnosticEvent::kTestKeySensitive,
        static_cast<std::uint32_t>(key_class));
    *eaten = FALSE;
    ResetEngine();
  } else if (!ShouldHandle(key)) {
    clipvault::ime::EmitDiagnostic(
        clipvault::ime::DiagnosticEvent::kTestKeyUnsupported,
        static_cast<std::uint32_t>(key_class));
    *eaten = FALSE;
  } else if (!EnsureEngine(input_context)) {
    clipvault::ime::EmitDiagnostic(
        clipvault::ime::DiagnosticEvent::kTestKeyEngineUnavailable,
        static_cast<std::uint32_t>(key_class));
    *eaten = CanBufferKey(key) ? TRUE : FALSE;
  } else {
    *eaten = TRUE;
  }
  return S_OK;
}

STDMETHODIMP TextService::OnTestKeyUp(ITfContext*, WPARAM, LPARAM, BOOL* eaten) {
  if (eaten == nullptr) return E_INVALIDARG;
  *eaten = FALSE;
  return S_OK;
}

STDMETHODIMP TextService::OnKeyDown(ITfContext* context, WPARAM key, LPARAM key_data,
                                    BOOL* eaten) {
  if (context == nullptr || eaten == nullptr) return E_INVALIDARG;
  *eaten = FALSE;
  clipvault::ime::EmitDiagnostic(
      clipvault::ime::DiagnosticEvent::kKeyDownObserved,
      static_cast<std::uint32_t>(
          clipvault::ime::ClassifyKeyForDiagnostics(key, composition_active_)));
  const auto input_context = ClassifyInputContext(context);
  if (input_context.field_kind ==
      clipvault::ime::InputFieldKind::kPassword) {
    candidate_window_.Hide();
    if (composition_active_)
      RetireComposition(active_context_);
    else
      ResetEngine();
    return S_OK;
  }
  if (!ShouldHandle(key)) return S_OK;
  if (!EnsureEngine(input_context)) {
    if (BufferLocalKey(context, key, key_data)) *eaten = TRUE;
    return S_OK;
  }
  clipvault::ime::EngineState warmed_state;
  if (!pending_preedit_.empty() && !ReplayBufferedPreedit(&warmed_state)) {
    if (BufferLocalKey(context, key, key_data)) *eaten = TRUE;
    return S_OK;
  }
  clipvault::ime::EngineState state;
  bool processed = false;
  if (key >= '1' && key <= '9' && composition_active_) {
    const auto index = static_cast<std::size_t>(key - '1');
    processed = index < last_state_.candidates.size() &&
                engine_.SelectCandidate(last_state_.candidates[index].candidate_id,
                                        &state);
  } else if (key == VK_PRIOR && composition_active_) {
    processed = engine_.PageCandidates(true, &state);
  } else if (key == VK_NEXT && composition_active_) {
    processed = engine_.PageCandidates(false, &state);
  } else if (key == VK_ESCAPE && composition_active_) {
    processed = engine_.CancelComposition(&state);
  } else if (key == VK_RETURN && composition_active_) {
    processed = engine_.CommitComposition(&state);
  } else {
    processed = engine_.ProcessKey(TranslateKey(key, key_data), &state);
  }
  if (!processed) {
    clipvault::ime::EmitDiagnostic(
        clipvault::ime::DiagnosticEvent::kKeyRpcTimedOut);
    if (RecoverPlainKey(context, key, key_data)) {
      *eaten = TRUE;
    }
    return S_OK;
  }
  if (!state.handled) {
    if (!state.snapshot_surface.empty()) ApplyAndPresent(context, state);
    return S_OK;
  }
  const HRESULT applied = ApplyAndPresent(context, state);
  if (FAILED(applied)) {
    // The Host may have advanced. Retire locally rather than replaying a commit.
    RetireComposition(context);
    return S_OK;
  }
  *eaten = TRUE;
  clipvault::ime::EmitDiagnostic(
      clipvault::ime::DiagnosticEvent::kKeyStateApplied,
      state.composition_active ? 1u : 0u);
  return S_OK;
}

STDMETHODIMP TextService::OnKeyUp(ITfContext*, WPARAM, LPARAM, BOOL* eaten) {
  if (eaten == nullptr) return E_INVALIDARG;
  *eaten = FALSE;
  return S_OK;
}

STDMETHODIMP TextService::OnPreservedKey(ITfContext* context, REFGUID key_guid,
                                         BOOL* eaten) {
  if (eaten == nullptr) return E_INVALIDARG;
  *eaten = FALSE;
  if (!IsEqualGUID(key_guid, kOtpInsertPreservedKey)) return S_OK;
  *eaten = TRUE;
  InsertLatestOtp(context);
  return S_OK;
}

STDMETHODIMP TextService::OnCompositionTerminated(
    TfEditCookie, ITfComposition* composition) {
  if (SameComIdentity(composition_, composition)) {
    SafeRelease(&composition_);
    composition_active_ = false;
    candidate_anchor_valid_ = false;
    candidate_window_.Hide();
  }
  return S_OK;
}

HRESULT TextService::ApplyState(ITfContext* context,
                                const clipvault::ime::EngineState& state) {
  ApplyStateEditSession* edit = nullptr;
  try {
    // nothrow covers allocation of the object itself, but copying EngineState
    // in the constructor can still throw. Convert both failure modes into an
    // HRESULT instead of allowing an exception to escape a COM callback.
    edit = new (std::nothrow) ApplyStateEditSession(this, context, state);
  } catch (const std::bad_alloc&) {
    return E_OUTOFMEMORY;
  } catch (...) {
    return E_FAIL;
  }
  if (edit == nullptr) return E_OUTOFMEMORY;
  struct EditReleaseGuard final {
    ApplyStateEditSession* value;
    ~EditReleaseGuard() {
      if (value != nullptr) value->Release();
    }
  } edit_guard{edit};
  HRESULT session_result = E_FAIL;
  HRESULT requested = E_FAIL;
  try {
    requested = context->RequestEditSession(
        client_id_, edit, TF_ES_SYNC | TF_ES_READWRITE, &session_result);
  } catch (...) {
    return E_FAIL;
  }
  if (FAILED(requested)) {
    clipvault::ime::EmitDiagnostic(
        clipvault::ime::DiagnosticEvent::kEditSessionRequestFailed,
        static_cast<std::uint32_t>(requested));
  } else if (FAILED(session_result)) {
    clipvault::ime::EmitDiagnostic(
        clipvault::ime::DiagnosticEvent::kEditSessionApplyFailed,
        static_cast<std::uint32_t>(session_result));
  }
  return FAILED(requested) ? requested : session_result;
}

HRESULT TextService::ApplyAndPresent(
    ITfContext* context, const clipvault::ime::EngineState& state) {
  const HRESULT applied = ApplyState(context, state);
  if (FAILED(applied)) return applied;
  CaptureContext(context);
  last_state_ = state;
  composition_active_ = state.composition_active;
  if (!state.candidates.empty() || !state.snapshot_surface.empty()) {
    const RECT* anchor = candidate_anchor_valid_ ? &candidate_anchor_ : nullptr;
    if (!candidate_window_.Show(context, anchor, state)) {
      // Candidate presentation is optional. A UI creation failure must not
      // erase composition text that was already projected successfully.
      candidate_window_.Hide();
      clipvault::ime::EmitDiagnostic(
          clipvault::ime::DiagnosticEvent::kCandidateWindowUnavailable);
    }
  } else {
    candidate_window_.Hide();
  }
  // CandidateWindow owns the only UI copy and enforces its monotonic expiry.
  // TextService needs no second plaintext/ID copy: selection callbacks come
  // from that window and the Host revalidates epoch, generation, ID and TTL.
  WipeSnapshotSurface(&last_state_.snapshot_surface);
  return S_OK;
}

void TextService::CaptureCandidateAnchor(TfEditCookie cookie,
                                         ITfContext* context) noexcept {
  candidate_anchor_valid_ = false;
  if (context == nullptr || composition_ == nullptr) return;
  ITfRange* range = nullptr;
  ITfContextView* view = nullptr;
  if (FAILED(composition_->GetRange(&range)) ||
      FAILED(context->GetActiveView(&view))) {
    SafeRelease(&range);
    SafeRelease(&view);
    return;
  }
  BOOL clipped = FALSE;
  RECT extent{};
  if (SUCCEEDED(view->GetTextExt(cookie, range, &extent, &clipped)) &&
      extent.right >= extent.left && extent.bottom >= extent.top) {
    candidate_anchor_ = extent;
    candidate_anchor_valid_ = true;
  } else if (SUCCEEDED(view->GetScreenExt(&extent))) {
    candidate_anchor_ = extent;
    candidate_anchor_.bottom = candidate_anchor_.top + 36;
    candidate_anchor_valid_ = true;
  }
  view->Release();
  range->Release();
}

void TextService::CaptureContext(ITfContext* context) noexcept {
  if (active_context_ == context) return;
  ResetOtpContext();
  SafeRelease(&active_context_);
  active_context_ = context;
  if (active_context_ != nullptr) {
    active_context_->AddRef();
    if (!NewUuidBytes(&otp_document_token_) ||
        !NewUuidBytes(&otp_context_token_)) {
      ResetOtpContext();
    }
  }
}

bool TextService::IsCurrentContext(ITfContext* context) const noexcept {
  if (context == nullptr || thread_manager_ == nullptr) return false;
  BOOL thread_focus = FALSE;
  if (FAILED(thread_manager_->IsThreadFocus(&thread_focus)) || !thread_focus)
    return false;
  ITfDocumentMgr* document = nullptr;
  ITfContext* current = nullptr;
  const bool focused = SUCCEEDED(thread_manager_->GetFocus(&document)) &&
                       document != nullptr && SUCCEEDED(document->GetTop(&current));
  const bool same = focused && SameComIdentity(context, current);
  SafeRelease(&current);
  SafeRelease(&document);
  return same;
}

bool TextService::BuildOtpContext(
    ITfContext* context,
    clipvault::ime::OtpContextBinding* binding) const noexcept {
  if (binding == nullptr || context == nullptr || !IsCurrentContext(context) ||
      !InputDesktopIsUnlocked() || GetSystemMetrics(SM_REMOTESESSION) != 0) {
    return false;
  }
  const auto input = ClassifyInputContext(context);
  if (input.field_kind == clipvault::ime::InputFieldKind::kUnknown ||
      input.field_kind == clipvault::ime::InputFieldKind::kPassword) {
    return false;
  }
  GUITHREADINFO gui{};
  gui.cbSize = sizeof(gui);
  const DWORD current_thread = GetCurrentThreadId();
  if (!GetGUIThreadInfo(current_thread, &gui)) return false;
  HWND window = gui.hwndFocus != nullptr ? gui.hwndFocus : GetForegroundWindow();
  DWORD process_id = 0;
  const DWORD thread_id =
      window == nullptr ? 0 : GetWindowThreadProcessId(window, &process_id);
  if (process_id != GetCurrentProcessId() || thread_id != current_thread)
    return false;
  const auto canonical = [](const auto& value) {
    return (value[6] & 0xf0U) == 0x40U && (value[8] & 0xc0U) == 0x80U;
  };
  if (!canonical(otp_document_token_) || !canonical(otp_context_token_))
    return false;
  *binding = clipvault::ime::OtpContextBinding{
      .process_id = process_id,
      .thread_id = thread_id,
      .window_handle = static_cast<std::uint64_t>(
          reinterpret_cast<std::uintptr_t>(window)),
      .document_token = otp_document_token_,
      .context_token = otp_context_token_,
  };
  return true;
}

HRESULT TextService::ApplyOtpCommit(ITfContext* context,
                                    std::wstring* text) noexcept {
  if (context == nullptr || text == nullptr || text->size() < 4 ||
      text->size() > 8) {
    return E_INVALIDARG;
  }
  auto* edit =
      new (std::nothrow) OtpCommitEditSession(this, context, text);
  if (edit == nullptr) {
    clipvault::ime::EmitDiagnostic(
        clipvault::ime::DiagnosticEvent::kEditSessionApplyFailed,
        static_cast<std::uint32_t>(E_OUTOFMEMORY));
    return E_OUTOFMEMORY;
  }
  struct EditReleaseGuard final {
    OtpCommitEditSession* value;
    ~EditReleaseGuard() {
      if (value != nullptr) value->Release();
    }
  } edit_guard{edit};
  HRESULT session_result = E_FAIL;
  HRESULT requested = E_FAIL;
  try {
    requested = context->RequestEditSession(
        client_id_, edit, TF_ES_SYNC | TF_ES_READWRITE, &session_result);
  } catch (...) {
    clipvault::ime::EmitDiagnostic(
        clipvault::ime::DiagnosticEvent::kEditSessionRequestFailed,
        static_cast<std::uint32_t>(E_FAIL));
    return E_FAIL;
  }
  if (FAILED(requested)) {
    clipvault::ime::EmitDiagnostic(
        clipvault::ime::DiagnosticEvent::kEditSessionRequestFailed,
        static_cast<std::uint32_t>(requested));
  } else if (FAILED(session_result)) {
    clipvault::ime::EmitDiagnostic(
        clipvault::ime::DiagnosticEvent::kEditSessionApplyFailed,
        static_cast<std::uint32_t>(session_result));
  }
  return FAILED(requested) ? requested : session_result;
}

void TextService::InsertLatestOtp(ITfContext* context) noexcept {
  clipvault::ime::EngineState state;
  CommitTextWipeGuard wipe_commit(&state);
  try {
    if (context == nullptr || composition_active_) return;
    CaptureContext(context);
    clipvault::ime::OtpContextBinding before;
    const auto input = ClassifyInputContext(context);
    if (!BuildOtpContext(context, &before) || !EnsureEngine(input)) return;
    if (!engine_.InsertOtp(before, &state, 40)) {
      // The protocol layer also wipes partially decoded replies. Keep this
      // boundary defensive so future transport implementations cannot return
      // an error while leaving OTP plaintext in the caller-owned state.
      candidate_window_.Hide();
      return;
    }
    clipvault::ime::OtpContextBinding after;
    const bool valid = BuildOtpContext(context, &after) && before == after &&
                       state.handled && state.commit_text.has_value() &&
                       state.commit_text->size() >= 4 &&
                       state.commit_text->size() <= 8 &&
                       std::all_of(state.commit_text->begin(),
                                   state.commit_text->end(),
                                   [](wchar_t value) {
                                     return value >= L'0' && value <= L'9';
                                   });
    if (valid) {
      // Direct TSF range insertion only. The dedicated fixed-buffer edit
      // session copies the OTP and immediately erases this source string, so
      // no full EngineState OTP copy exists.
      const HRESULT projected =
          ApplyOtpCommit(context, &*state.commit_text);
      if (FAILED(projected)) {
        // The Broker has already consumed the one-use lease. Retire the local
        // Host session so the next key cannot continue from an ambiguous state;
        // ApplyOtpCommit emitted a content-free HRESULT diagnostic.
        ResetEngine();
        return;
      }
    }
    candidate_window_.Hide();
  } catch (...) {
    // The guard erases any received OTP before this noexcept COM callback
    // returns. Sever the session because the Host may already have consumed
    // the one-use credential even though local projection failed.
    engine_.Disconnect();
    session_started_ = false;
    candidate_window_.Hide();
  }
}

void TextService::ResetOtpContext() noexcept {
  SecureZeroMemory(otp_document_token_.data(), otp_document_token_.size());
  SecureZeroMemory(otp_context_token_.data(), otp_context_token_.size());
}

void TextService::SelectCandidate(std::size_t index) {
  if (active_context_ == nullptr || index >= last_state_.candidates.size() ||
      !IsCurrentContext(active_context_)) {
    RetireComposition(active_context_);
    return;
  }
  if (!EnsureEngine(input_context_)) return;
  clipvault::ime::EngineState state;
  if (!engine_.SelectCandidate(last_state_.candidates[index].candidate_id, &state)) {
    PreservePreeditLiteral(active_context_);
    return;
  }
  if (FAILED(ApplyAndPresent(active_context_, state))) {
    RetireComposition(active_context_);
  }
}

void TextService::SelectSnapshotCandidate(
    const std::string& publisher_epoch, std::uint64_t generation,
    const std::string& candidate_id) {
  if (active_context_ == nullptr || !IsCurrentContext(active_context_)) {
    candidate_window_.Hide();
    ResetEngine();
    return;
  }
  const auto current_context = ClassifyInputContext(active_context_);
  const bool allowed = current_context.clipvault_allowed &&
                       current_context.learning_allowed &&
                       !current_context.incognito &&
                       current_context.field_kind !=
                           clipvault::ime::InputFieldKind::kUnknown &&
                       current_context.field_kind !=
                           clipvault::ime::InputFieldKind::kPassword;
  if (!allowed) {
    candidate_window_.Hide();
    ResetEngine();
    return;
  }
  if (!EnsureEngine(current_context)) return;
  clipvault::ime::EngineState state;
  if (!engine_.SelectSnapshotCandidate(publisher_epoch, generation,
                                       candidate_id, &state)) {
    candidate_window_.Hide();
    ResetEngine();
    return;
  }
  if (FAILED(ApplyAndPresent(active_context_, state))) {
    RetireComposition(active_context_);
  }
}

void TextService::ChangeCandidatePage(bool backward) {
  if (active_context_ == nullptr || !IsCurrentContext(active_context_)) {
    RetireComposition(active_context_);
    return;
  }
  if (!EnsureEngine(input_context_)) return;
  if ((backward && !last_state_.has_previous_page) ||
      (!backward && !last_state_.has_next_page)) return;
  clipvault::ime::EngineState state;
  if (!engine_.PageCandidates(backward, &state)) {
    PreservePreeditLiteral(active_context_);
    return;
  }
  if (FAILED(ApplyAndPresent(active_context_, state))) {
    RetireComposition(active_context_);
  }
}

bool TextService::RecoverPlainKey(ITfContext* context, WPARAM key,
                                  LPARAM key_data) {
  const auto event = TranslateKey(key, key_data);
  const bool replayable = key >= 'A' && key <= 'Z' && !event.control && !event.alt;
  const auto recovery = clipvault::ime::PlanRpcRecovery(
      composition_active_ && !last_state_.preedit.empty(), replayable);
  if (recovery.preserve_preedit_as_literal && !PreservePreeditLiteral(context)) {
    MaybeLaunchHost();
    return false;
  }
  if (!recovery.preserve_preedit_as_literal) RetireComposition(context);
  // A failed RPC may have been accepted by the old Host even though its reply
  // was never acknowledged. Never replay that key into a new session. A plain
  // letter is allowed to fall through once after any preedit is committed
  // literally; ambiguous candidate/control operations remain consumed.
  MaybeLaunchHost();
  return recovery.consume_original_key;
}

bool TextService::PreservePreeditLiteral(ITfContext* context) noexcept {
  if (context == nullptr || last_state_.preedit.empty()) return false;
  candidate_window_.Hide();
  clipvault::ime::EngineState literal;
  literal.commit_text = last_state_.preedit;
  if (FAILED(ApplyState(context, literal))) {
    // Leave the editor composition untouched if the literal commit is
    // ambiguous; only sever the Host connection so no operation is replayed.
    engine_.Disconnect();
    session_started_ = false;
    return false;
  }
  last_state_ = clipvault::ime::EngineState{};
  composition_active_ = false;
  pending_preedit_.clear();
  ResetEngine();
  return true;
}

bool TextService::CanBufferKey(WPARAM key) const noexcept {
  const bool control = (GetKeyState(VK_CONTROL) & 0x8000) != 0;
  const bool alt = (GetKeyState(VK_MENU) & 0x8000) != 0;
  return PlanLocalBufferAction(key, control, alt, pending_preedit_.size()) !=
         LocalBufferAction::kReject;
}

bool TextService::BufferLocalKey(ITfContext* context, WPARAM key,
                                 LPARAM key_data) noexcept {
  if (context == nullptr) return false;
  const auto event = TranslateKey(key, key_data);
  const auto action = PlanLocalBufferAction(
      key, event.control, event.alt, pending_preedit_.size());
  if (action == LocalBufferAction::kReject) return false;
  if ((action == LocalBufferAction::kAppendLetter ||
       action == LocalBufferAction::kCommitFullBufferWithLetter) &&
      event.text.empty()) {
    return false;
  }
  const bool commit_full_buffer =
      action == LocalBufferAction::kCommitFullBufferWithLetter ||
      (action == LocalBufferAction::kAppendLetter &&
       event.text.size() >
           kMaximumBufferedCharacters - pending_preedit_.size());
  if (commit_full_buffer) {
    // At capacity, atomically commit the buffered preedit and the current key
    // as one literal state. This keeps OnTestKeyDown/OnKeyDown consistent and
    // cannot either eat-and-drop key 33 or leak it through behind an active
    // 32-character composition.
    clipvault::ime::EngineState literal;
    literal.commit_text = pending_preedit_ + event.text;
    if (FAILED(ApplyAndPresent(context, literal))) return false;
    pending_preedit_.clear();
    clipvault::ime::EmitDiagnostic(
        clipvault::ime::DiagnosticEvent::kLocalBufferUpdated, 0);
    return true;
  }
  if (action == LocalBufferAction::kAppendLetter) {
    pending_preedit_.append(event.text);
  } else if (action == LocalBufferAction::kBackspace) {
    if (!pending_preedit_.empty()) pending_preedit_.pop_back();
  } else if (action == LocalBufferAction::kCancel) {
    pending_preedit_.clear();
  } else {
    clipvault::ime::EngineState literal;
    literal.commit_text = pending_preedit_;
    if (FAILED(ApplyAndPresent(context, literal))) return false;
    pending_preedit_.clear();
    clipvault::ime::EmitDiagnostic(
        clipvault::ime::DiagnosticEvent::kLocalBufferUpdated, 0);
    return true;
  }

  clipvault::ime::EngineState buffered;
  buffered.handled = true;
  buffered.preedit = pending_preedit_;
  buffered.caret_utf16 = static_cast<std::uint32_t>(pending_preedit_.size());
  buffered.composition_active = !pending_preedit_.empty();
  buffered.mode = buffered.composition_active ? 2u : 1u;
  if (FAILED(ApplyAndPresent(context, buffered))) return false;
  clipvault::ime::EmitDiagnostic(
      clipvault::ime::DiagnosticEvent::kLocalBufferUpdated,
      static_cast<std::uint32_t>(pending_preedit_.size()));
  return true;
}

bool TextService::ReplayBufferedPreedit(
    clipvault::ime::EngineState* state) noexcept {
  if (pending_preedit_.empty()) return true;
  constexpr DWORD kReplayBudgetMilliseconds = 40;
  const ULONGLONG deadline = GetTickCount64() + kReplayBudgetMilliseconds;
  const auto abandon_partial_replay = [this] {
    // The Host session may already contain a strict prefix. It cannot remain
    // connected while the editor continues with the complete local buffer,
    // otherwise the next key would fork composition/revision state.
    engine_.Disconnect();
    session_started_ = false;
    last_state_ = clipvault::ime::EngineState{};
    candidate_window_.Hide();
  };
  clipvault::ime::EngineState replayed;
  for (const wchar_t value : pending_preedit_) {
    const DWORD remaining = RemainingBudget(deadline);
    if (remaining == 0) {
      abandon_partial_replay();
      return false;
    }
    clipvault::ime::KeyEvent event;
    event.virtual_key = static_cast<std::uint32_t>(std::towupper(value));
    event.text.assign(1, value);
    if (!engine_.ProcessKey(event, &replayed, remaining) || !replayed.handled) {
      abandon_partial_replay();
      return false;
    }
  }
  const auto replayed_count = static_cast<std::uint32_t>(pending_preedit_.size());
  pending_preedit_.clear();
  *state = replayed;
  last_state_ = replayed;
  composition_active_ = replayed.composition_active;
  clipvault::ime::EmitDiagnostic(
      clipvault::ime::DiagnosticEvent::kLocalBufferReplayed, replayed_count);
  return true;
}

void TextService::RetireComposition(ITfContext* context) noexcept {
  candidate_window_.Hide();
  clipvault::ime::EngineState empty;
  if (context != nullptr) ApplyState(context, empty);
  last_state_ = empty;
  composition_active_ = false;
  pending_preedit_.clear();
  ResetEngine();
}

void TextService::ResetEngine() noexcept {
  candidate_window_.Hide();
  engine_.Disconnect();
  session_started_ = false;
  composition_active_ = false;
  // A reset invalidates the Host session and the editor context together.
  // Never carry a locally buffered preedit into a later context (including a
  // password/focus transition), where ReplayBufferedPreedit could otherwise
  // project the old text into the newly focused control.
  pending_preedit_.clear();
  last_state_ = clipvault::ime::EngineState{};
  candidate_anchor_valid_ = false;
}
