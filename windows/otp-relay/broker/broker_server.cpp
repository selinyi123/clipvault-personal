#include "broker_server.h"

#include <sddl.h>

#include <algorithm>
#include <chrono>
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

}  // namespace

bool BrokerPipeServer::ServeOne(DWORD accept_timeout_ms,
                                DWORD request_budget_ms) {
  if (service_ == nullptr || authorizer_ == nullptr || !service_->ready() ||
      accept_timeout_ms == 0 ||
      request_budget_ms == 0) {
    return false;
  }
  LocalSecurity security;
  if (!security.Initialize()) return false;
  HANDLE pipe = CreateNamedPipeW(
      BrokerPipeNameForCurrentSession().c_str(),
      PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED | FILE_FLAG_FIRST_PIPE_INSTANCE,
      PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT |
          PIPE_REJECT_REMOTE_CLIENTS,
      1, kMaximumBrokerFrameBytes + 4, kMaximumBrokerFrameBytes + 4, 0,
      &security.attributes);
  if (pipe == INVALID_HANDLE_VALUE) return false;

  const bool connected =
      ConnectUntil(pipe, GetTickCount64() + accept_timeout_ms);
  const ULONGLONG request_deadline = GetTickCount64() + request_budget_ms;
  bool success = false;
  BrokerResponse response;
  std::vector<std::uint8_t> request_frame;
  if (connected && ReadBrokerFrameUntil(
                       pipe, &request_frame,
                       request_deadline)) {
    DWORD client_pid = 0;
    GetNamedPipeClientProcessId(pipe, &client_pid);
    OpaqueEnvelope offer;
    ContextBinding arm_latest;
    ConsumeRequest consume;
    crypto::UuidBytes dismiss{};
    if (DecodeOffer(request_frame, &offer)) {
      if (authorizer_->Authorize(client_pid,
                                 BrokerClientRole::kOpaqueDesktopOffer)) {
        response.status = service_->Offer(offer, WallNowMilliseconds(),
                                          GetTickCount64());
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
            arm_latest, window.process_id, window.thread_id, GetTickCount64());
      } else {
        response.status = BrokerStatus::kDenied;
      }
    } else if (DecodeConsume(request_frame, &consume)) {
      const auto window = WindowOwner(consume.context);
      if (authorizer_->Authorize(client_pid,
                                 BrokerClientRole::kImeHostControl)) {
        response = service_->Consume(consume, window.process_id,
                                     window.thread_id, GetTickCount64());
      } else {
        response.status = BrokerStatus::kDenied;
      }
    } else if (DecodeDismiss(request_frame, &dismiss)) {
      response.status =
          authorizer_->Authorize(client_pid,
                                 BrokerClientRole::kImeHostControl)
              ? service_->Dismiss(dismiss)
              : BrokerStatus::kDenied;
    } else {
      response.status = BrokerStatus::kRejected;
    }
    auto encoded = EncodeResponse(response);
    success = !encoded.empty() && WriteBrokerFrameUntil(
                                      pipe, encoded, request_deadline);
    if (success) {
      // DisconnectNamedPipe discards unread pipe buffers. Keep the instance
      // alive until the peer has read the response and closes its handle, or
      // until the same absolute request deadline expires. This bounded
      // overlapped read is cancellable and avoids an unbounded
      // FlushFileBuffers call.
      std::vector<std::uint8_t> ignored_followup;
      ReadBrokerFrameUntil(pipe, &ignored_followup, request_deadline);
    }
    // Both objects may contain the detached one-use lease. Erase all
    // application-owned copies after the pipe handoff completes.
    crypto::SecureErase(encoded);
    crypto::SecureErase(response.secret);
  }

  CancelIoEx(pipe, nullptr);
  DisconnectNamedPipe(pipe);
  CloseHandle(pipe);
  return success;
}

}  // namespace clipvault::otp::broker
