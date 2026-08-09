#pragma once

#include "otp_aead_cng.h"

#include <windows.h>

#include <cstdint>
#include <string>
#include <vector>

namespace clipvault::otp::broker {

inline constexpr std::uint8_t kBrokerProtocolVersion = 1;
inline constexpr std::uint32_t kMaximumBrokerFrameBytes = 512;
inline constexpr DWORD kBrokerForwardBudgetMilliseconds = 250;
// Arm+consume is invoked from the TSF key path.  Keep its Credential Manager
// acquisition bounded to the same budget as the Host's absolute deadline.
inline constexpr DWORD kImeOtpBrokerOperationBudgetMilliseconds = 35;

enum class BrokerOperation : std::uint8_t {
  kOffer = 1,
  kArm = 2,
  kConsume = 3,
  kDismiss = 4,
  kArmLatest = 5,
  kRevokeSession = 6,
  kResponse = 128,
};

enum class BrokerStatus : std::uint8_t {
  kAccepted = 1,
  kDuplicate = 2,
  kRejected = 3,
  kExpired = 4,
  kNotFound = 5,
  kDenied = 6,
  kConsumed = 7,
  kUnavailable = 8,
  // Sender must create a fresh session_epoch/verifier/key before retrying.
  // Replay markers are never evicted under the current AES-GCM key.
  kRotationRequired = 9,
};

struct OpaqueEnvelope final {
  std::uint8_t version = 1;
  std::uint8_t algorithm = 1;  // A256GCM; protocol v1 has no negotiation.
  crypto::UuidBytes session_epoch{};
  crypto::UuidBytes event_id{};
  crypto::UuidBytes sender_device{};
  crypto::UuidBytes target_device{};
  std::uint64_t sequence = 0;
  std::uint64_t issued_at_ms = 0;
  std::uint64_t expires_at_ms = 0;
  crypto::NonceBytes nonce{};
  std::vector<std::uint8_t> ciphertext;
  crypto::TagBytes authentication_tag{};
};

struct ContextBinding final {
  std::uint32_t process_id = 0;
  std::uint32_t thread_id = 0;
  std::uint64_t window_handle = 0;
  crypto::UuidBytes document_token{};
  crypto::UuidBytes context_token{};

  bool operator==(const ContextBinding&) const = default;
};

struct ArmRequest final {
  crypto::UuidBytes event_id{};
  ContextBinding context;
};

struct ConsumeRequest final {
  crypto::UuidBytes claim_id{};
  ContextBinding context;
};

struct BrokerResponse final {
  BrokerStatus status = BrokerStatus::kRejected;
  crypto::UuidBytes claim_id{};
  std::vector<std::uint8_t> secret;
};

std::vector<std::uint8_t> EncodeOffer(const OpaqueEnvelope& envelope);
bool DecodeOffer(const std::vector<std::uint8_t>& frame,
                 OpaqueEnvelope* envelope);
std::vector<std::uint8_t> EncodeArm(const ArmRequest& request);
bool DecodeArm(const std::vector<std::uint8_t>& frame, ArmRequest* request);
std::vector<std::uint8_t> EncodeArmLatest(const ContextBinding& context);
bool DecodeArmLatest(const std::vector<std::uint8_t>& frame,
                     ContextBinding* context);
std::vector<std::uint8_t> EncodeConsume(const ConsumeRequest& request);
bool DecodeConsume(const std::vector<std::uint8_t>& frame,
                   ConsumeRequest* request);
std::vector<std::uint8_t> EncodeDismiss(const crypto::UuidBytes& event_id);
bool DecodeDismiss(const std::vector<std::uint8_t>& frame,
                   crypto::UuidBytes* event_id);
std::vector<std::uint8_t> EncodeRevokeSession(
    const crypto::UuidBytes& session_epoch);
bool DecodeRevokeSession(const std::vector<std::uint8_t>& frame,
                         crypto::UuidBytes* session_epoch);
std::vector<std::uint8_t> EncodeResponse(const BrokerResponse& response);
bool DecodeResponse(const std::vector<std::uint8_t>& frame,
                    BrokerResponse* response);

// Both deadline functions require FILE_FLAG_OVERLAPPED and share one absolute
// GetTickCount64 deadline across prefix and payload. Timeout always issues
// CancelIoEx before returning.
bool ReadBrokerFrameUntil(HANDLE pipe, std::vector<std::uint8_t>* payload,
                          ULONGLONG deadline_tick);
bool WriteBrokerFrameUntil(HANDLE pipe,
                           const std::vector<std::uint8_t>& payload,
                           ULONGLONG deadline_tick);

std::wstring BrokerPipeNameForCurrentSession();

class BrokerPipeClient final {
 public:
  BrokerPipeClient() = default;
  ~BrokerPipeClient();
  BrokerPipeClient(const BrokerPipeClient&) = delete;
  BrokerPipeClient& operator=(const BrokerPipeClient&) = delete;

  bool ConnectUntil(ULONGLONG deadline_tick);
  bool ExchangeUntil(const std::vector<std::uint8_t>& request,
                     BrokerResponse* response, ULONGLONG deadline_tick);
  void Close() noexcept;

 private:
  HANDLE pipe_ = INVALID_HANDLE_VALUE;
};

}  // namespace clipvault::otp::broker
