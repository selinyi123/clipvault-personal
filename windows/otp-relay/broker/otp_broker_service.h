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
                     std::uint64_t monotonic_now_ms);
  BrokerResponse ArmLatest(const ContextBinding& context,
                           std::uint32_t verified_window_owner_pid,
                           std::uint32_t verified_window_thread_id,
                           std::uint64_t monotonic_now_ms);
  BrokerResponse Consume(const ConsumeRequest& request,
                         std::uint32_t verified_window_owner_pid,
                         std::uint32_t verified_window_thread_id,
                         std::uint64_t monotonic_now_ms);
  BrokerStatus Dismiss(const crypto::UuidBytes& event_id);
  std::size_t ExpireDue(std::uint64_t monotonic_now_ms);
  void Clear() noexcept;

 private:
  struct Slot final {
    crypto::UuidBytes session_epoch{};
    std::uint64_t last_offer_order = 0;
    std::unique_ptr<OtpBrokerCore> core;
  };

  mutable std::mutex mutex_;
  authority::PairCredentialAuthority* authority_ = nullptr;
  std::size_t session_capacity_ = 0;
  std::uint64_t offer_order_ = 0;
  std::vector<Slot> slots_;
};

}  // namespace clipvault::otp::broker
