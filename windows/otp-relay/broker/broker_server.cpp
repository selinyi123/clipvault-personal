#include "broker_server.h"

#include <sddl.h>

#include <algorithm>
#include <chrono>
#include <limits>
#include <vector>

namespace clipvault::otp::broker {
namespace {

struct LocalSecurity final {
  PSECURITY_DESCRIPTOR descriptor = nullptr;
  SECURITY_ATTRIBUTES attributes{sizeof(SECURITY_ATTRIBUTES), nullptr, FALSE};

  ~LocalSecurity() {
    if (descriptor != nullptr) LocalFree(descriptor);
  }

  bool Initialize() {
    HANDLE token = nullptr;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token)) return false;
    DWORD required = 0;
    GetTokenInformation(token, TokenUser, nullptr, 0, &required);
    std::vector<std::uint8_t> storage(required);
    const bool read = required != 0 &&
                      GetTokenInformation(token, TokenUser, storage.data(),
                                          required, &required) != FALSE;
    CloseHandle(token);
    if (!read) return false;
    const auto* user = reinterpret_cast<const TOKEN_USER*>(storage.data());
    LPWSTR sid = nullptr;
    if (!ConvertSidToStringSidW(user->User.Sid, &sid)) return false;
    const std::wstring sddl =
        L"D:P(A;;GA;;;SY)(A;;GA;;;" + std::wstring(sid) + L")";
    LocalFree(sid);
    if (!ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl.c_str(), SDDL_REVISION_1, &descriptor, nullptr)) {
      return false;
    }
    attributes.lpSecurityDescriptor = descriptor;
    return true;
  }
};

bool ConnectUntil(HANDLE pipe, ULONGLONG deadline_tick) {
  HANDLE event = CreateEventW(nullptr, TRUE, FALSE, nullptr);
  if (event == nullptr) return false;
  OVERLAPPED overlapped{};
  overlapped.hEvent = event;
  bool connected = ConnectNamedPipe(pipe, &overlapped) != FALSE;
  if (!connected) {
    const DWORD error = GetLastError();
    if (error == ERROR_PIPE_CONNECTED) {
      connected = true;
    } else if (error == ERROR_IO_PENDING) {
      const ULONGLONG now = GetTickCount64();
      const DWORD wait = now >= deadline_tick
                             ? 0
                             : static_cast<DWORD>(std::min<ULONGLONG>(
                                   deadline_tick - now, MAXDWORD));
      if (WaitForSingleObject(event, wait) == WAIT_OBJECT_0) {
        DWORD transferred = 0;
        connected = GetOverlappedResult(pipe, &overlapped, &transferred,
                                        FALSE) != FALSE;
      } else {
        CancelIoEx(pipe, &overlapped);
        DWORD transferred = 0;
        GetOverlappedResult(pipe, &overlapped, &transferred, TRUE);
      }
    }
  }
  CloseHandle(event);
  return connected;
}

ULONGLONG BoundedDeadline(ULONGLONG upper_bound, DWORD budget_ms) {
  const ULONGLONG now = GetTickCount64();
  const ULONGLONG candidate =
      now > (std::numeric_limits<ULONGLONG>::max() - budget_ms)
          ? std::numeric_limits<ULONGLONG>::max()
          : now + budget_ms;
  return std::min(upper_bound, candidate);
}

std::uint64_t WallNowMilliseconds() {
  return static_cast<std::uint64_t>(
      std::chrono::duration_cast<std::chrono::milliseconds>(
          std::chrono::system_clock::now().time_since_epoch())
          .count());
}

struct WindowIdentity final {
  std::uint32_t process_id = 0;
  std::uint32_t thread_id = 0;
};

WindowIdentity WindowOwner(const ContextBinding& context) {
  DWORD owner = 0;
  const auto handle = reinterpret_cast<HWND>(
      static_cast<std::uintptr_t>(context.window_handle));
  const DWORD thread =
      handle == nullptr ? 0 : GetWindowThreadProcessId(handle, &owner);
  return WindowIdentity{owner, thread};
}

struct ScopedBrokerPipe final {
  HANDLE value = INVALID_HANDLE_VALUE;

  ~ScopedBrokerPipe() {
    if (value == INVALID_HANDLE_VALUE) return;
    CancelIoEx(value, nullptr);
    DisconnectNamedPipe(value);
    CloseHandle(value);
  }
};

struct SensitiveRequestState final {
  BrokerResponse* response = nullptr;
  std::vector<std::uint8_t>* request_frame = nullptr;
  std::vector<std::uint8_t>* encoded_response = nullptr;
  std::vector<std::uint8_t>* ignored_followup = nullptr;
  OpaqueEnvelope* offer = nullptr;
  ContextBinding* arm_latest = nullptr;
  ConsumeRequest* consume = nullptr;
  crypto::UuidBytes* dismiss = nullptr;
  crypto::UuidBytes* revoke_session = nullptr;

  ~SensitiveRequestState() {
    if (request_frame != nullptr) crypto::SecureErase(*request_frame);
    if (encoded_response != nullptr) crypto::SecureErase(*encoded_response);
    if (ignored_followup != nullptr) crypto::SecureErase(*ignored_followup);
    if (response != nullptr) {
      crypto::SecureErase(response->claim_id);
      crypto::SecureErase(response->secret);
    }
    if (offer != nullptr) {
      crypto::SecureErase(offer->session_epoch);
      crypto::SecureErase(offer->event_id);
      crypto::SecureErase(offer->sender_device);
      crypto::SecureErase(offer->target_device);
      crypto::SecureErase(offer->nonce);
      crypto::SecureErase(offer->ciphertext);
      crypto::SecureErase(offer->authentication_tag);
    }
    if (arm_latest != nullptr) {
      crypto::SecureErase(arm_latest->document_token);
      crypto::SecureErase(arm_latest->context_token);
    }
    if (consume != nullptr) {
      crypto::SecureErase(consume->claim_id);
      crypto::SecureErase(consume->context.document_token);
      crypto::SecureErase(consume->context.context_token);
    }
    if (dismiss != nullptr) crypto::SecureErase(*dismiss);
    if (revoke_session != nullptr) crypto::SecureErase(*revoke_session);
  }
};

}  // namespace

bool BrokerPipeServer::ServeOne(DWORD accept_timeout_ms,
                                DWORD request_budget_ms) {
  if (service_ == nullptr || authorizer_ == nullptr || !service_->ready() ||
      accept_timeout_ms == 0 ||
      request_budget_ms == 0) {
    return false;
  }
  try {
    LocalSecurity security;
    if (!security.Initialize()) return false;
    ScopedBrokerPipe pipe{CreateNamedPipeW(
        BrokerPipeNameForCurrentSession().c_str(),
        PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED |
            FILE_FLAG_FIRST_PIPE_INSTANCE,
        PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT |
            PIPE_REJECT_REMOTE_CLIENTS,
        1, kMaximumBrokerFrameBytes + 4, kMaximumBrokerFrameBytes + 4, 0,
        &security.attributes)};
    if (pipe.value == INVALID_HANDLE_VALUE) return false;

    bool success = false;
    BrokerResponse response;
    std::vector<std::uint8_t> request_frame;
    std::vector<std::uint8_t> encoded;
    std::vector<std::uint8_t> ignored_followup;
    OpaqueEnvelope offer;
    ContextBinding arm_latest;
    ConsumeRequest consume;
    crypto::UuidBytes dismiss{};
    crypto::UuidBytes revoke_session{};
    SensitiveRequestState sensitive{
        &response,      &request_frame, &encoded, &ignored_followup,
        &offer,         &arm_latest,    &consume, &dismiss,
        &revoke_session};

    const bool connected =
        ConnectUntil(pipe.value, GetTickCount64() + accept_timeout_ms);
    const ULONGLONG request_deadline = GetTickCount64() + request_budget_ms;
    if (!connected ||
        !ReadBrokerFrameUntil(pipe.value, &request_frame, request_deadline)) {
      return false;
    }

    DWORD client_pid = 0;
    GetNamedPipeClientProcessId(pipe.value, &client_pid);
    if (DecodeOffer(request_frame, &offer)) {
      if (authorizer_->Authorize(client_pid,
                                 BrokerClientRole::kOpaqueDesktopOffer)) {
        response.status = service_->Offer(offer, WallNowMilliseconds(),
                                          GetTickCount64(), request_deadline);
        if (response.status == BrokerStatus::kAccepted && prompt_ != nullptr)
          prompt_->NotifyOtpReady();
      } else {
        response.status = BrokerStatus::kDenied;
      }
      crypto::SecureErase(offer.ciphertext);
    } else if (DecodeArmLatest(request_frame, &arm_latest)) {
      const auto window = WindowOwner(arm_latest);
      if (authorizer_->Authorize(client_pid,
                                 BrokerClientRole::kImeHostControl)) {
        response = service_->ArmLatest(
            arm_latest, window.process_id, window.thread_id, GetTickCount64(),
            BoundedDeadline(request_deadline,
                            kImeOtpBrokerOperationBudgetMilliseconds));
      } else {
        response.status = BrokerStatus::kDenied;
      }
    } else if (DecodeConsume(request_frame, &consume)) {
      const auto window = WindowOwner(consume.context);
      if (authorizer_->Authorize(client_pid,
                                 BrokerClientRole::kImeHostControl)) {
        response = service_->Consume(
            consume, window.process_id, window.thread_id, GetTickCount64(),
            BoundedDeadline(request_deadline,
                            kImeOtpBrokerOperationBudgetMilliseconds));
      } else {
        response.status = BrokerStatus::kDenied;
      }
    } else if (DecodeDismiss(request_frame, &dismiss)) {
      response.status =
          authorizer_->Authorize(client_pid,
                                 BrokerClientRole::kImeHostControl)
              ? service_->Dismiss(dismiss)
              : BrokerStatus::kDenied;
    } else if (DecodeRevokeSession(request_frame, &revoke_session)) {
      response.status =
          authorizer_->Authorize(client_pid,
                                 BrokerClientRole::kDesktopControl)
              ? service_->RevokeSession(revoke_session, request_deadline)
              : BrokerStatus::kDenied;
    } else {
      response.status = BrokerStatus::kRejected;
    }
    encoded = EncodeResponse(response);
    success = !encoded.empty() && WriteBrokerFrameUntil(
                                      pipe.value, encoded, request_deadline);
    if (success) {
      // DisconnectNamedPipe discards unread pipe buffers. Keep the instance
      // alive until the peer has read the response and closes its handle, or
      // until the same absolute request deadline expires. This bounded
      // overlapped read is cancellable and avoids an unbounded
      // FlushFileBuffers call.
      ReadBrokerFrameUntil(pipe.value, &ignored_followup, request_deadline);
    }
    return success;
  } catch (...) {
    // A malformed request, identity-provider failure, allocation failure, or
    // prompt failure must not terminate the long-running Broker. Stack guards
    // erase any decoded credential material and close the pipe on unwind.
    return false;
  }
}

}  // namespace clipvault::otp::broker
