#pragma once

#include "pair_credential.h"

#include <cstddef>
#include <cstdint>
#include <memory>
#include <mutex>
#include <vector>

namespace clipvault::otp::broker {

// Multi-pair in-memory broker. CVPK is loaded lazily from current-user
// Credential Manager using the authenticated session epoch in each opaque
// offer. Plaintext remains owned by a session core until one bounded consume.
class OtpBrokerService final {
 public:
  explicit OtpBrokerService(authority::PairCredentialAuthority* authority,
                            std::size_t session_capacity = 8);
  ~OtpBrokerService();
  OtpBrokerService(const OtpBrokerService&) = delete;
  OtpBrokerService& operator=(const OtpBrokerService&) = delete;

  [[nodiscard]] bool ready() const noexcept;
  BrokerStatus Offer(const OpaqueEnvelope& envelope, std::uint64_t wall_now_ms,
                     std::uint64_t monotonic_now_ms,
                     ULONGLONG deadline_tick = 0);
  BrokerResponse ArmLatest(const ContextBinding& context,
                           std::uint32_t verified_window_owner_pid,
                           std::uint32_t verified_window_thread_id,
                           std::uint64_t monotonic_now_ms,
                           ULONGLONG deadline_tick = 0);
  BrokerResponse Consume(const ConsumeRequest& request,
                         std::uint32_t verified_window_owner_pid,
                         std::uint32_t verified_window_thread_id,
                         std::uint64_t monotonic_now_ms,
                         ULONGLONG deadline_tick = 0);
  BrokerStatus Dismiss(const crypto::UuidBytes& event_id);
  BrokerStatus RevokeSession(const crypto::UuidBytes& session_epoch,
                             ULONGLONG deadline_tick = 0);
  std::size_t ExpireDue(std::uint64_t monotonic_now_ms);
  void Clear() noexcept;

 private:
  struct Slot final {
    crypto::UuidBytes session_epoch{};
    std::uint64_t last_offer_order = 0;
    std::unique_ptr<OtpBrokerCore> core;
  };

  authority::CredentialAcquireStatus AcquireCurrentCredentialLocked(
      Slot& slot, authority::PairCredentialLease* lease,
      ULONGLONG deadline_tick = 0) noexcept;
  bool IsRevokedSessionLocked(
      const crypto::UuidBytes& session_epoch) const noexcept;
  bool RememberRevokedSessionLocked(
      const crypto::UuidBytes& session_epoch) noexcept;
  void ClearSlotLocked(Slot* slot) noexcept;
  authority::CredentialAcquireStatus PruneOneInvalidSlotLocked(
      ULONGLONG deadline_tick = 0) noexcept;

  // A failed durable delete must still fence late offers for the lifetime of
  // this Broker process.  The bound prevents an untrusted control client from
  // turning this into an unbounded allocation; entries are only cleared when
  // the process exits or the service is reset.
  static constexpr std::size_t kRevokedSessionFenceCapacity = 4'096;

  mutable std::mutex mutex_;
  authority::PairCredentialAuthority* authority_ = nullptr;
  std::size_t session_capacity_ = 0;
  std::uint64_t offer_order_ = 0;
  std::size_t credential_sweep_cursor_ = 0;
  std::vector<Slot> slots_;
  std::vector<crypto::UuidBytes> revoked_sessions_;
};

}  // namespace clipvault::otp::broker
