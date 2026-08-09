#pragma once

#include "broker_protocol.h"

#include <cstddef>
#include <cstdint>
#include <mutex>
#include <optional>
#include <vector>

namespace clipvault::otp::broker {

// Must match the Android sealed pair-record nonce history.  Exhaustion never
// evicts a nonce under the same AES-GCM key; it returns kRotationRequired and
// requires a freshly paired session_epoch/verifier/key.
inline constexpr std::size_t kPairSessionNonceCapacity = 4'096;

struct PairSession final {
  crypto::Sha256Bytes pair_verifier{};
  crypto::UuidBytes session_epoch{};
  crypto::UuidBytes sender_device{};
  crypto::UuidBytes target_device{};
};

class PersistentSequenceAuthority {
 public:
  virtual ~PersistentSequenceAuthority() = default;
  virtual bool AdvanceHighSequence(const PairSession& session,
                                   std::uint64_t sequence) noexcept = 0;
};

class OtpBrokerCore final {
 public:
  explicit OtpBrokerCore(const PairSession& session,
                         std::size_t live_capacity = 8,
                         std::size_t replay_capacity =
                             kPairSessionNonceCapacity,
                         std::uint64_t high_sequence = 0,
                         PersistentSequenceAuthority* sequence_authority = nullptr);
  ~OtpBrokerCore();
  OtpBrokerCore(const OtpBrokerCore&) = delete;
  OtpBrokerCore& operator=(const OtpBrokerCore&) = delete;

  [[nodiscard]] bool ready() const noexcept;
  [[nodiscard]] bool MatchesSession(const PairSession& session) const noexcept;
  BrokerStatus Offer(const OpaqueEnvelope& envelope, std::uint64_t wall_now_ms,
                     std::uint64_t monotonic_now_ms);
  BrokerResponse Arm(const ArmRequest& request,
                     std::uint32_t verified_window_owner_pid,
                     std::uint32_t verified_window_thread_id,
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
  struct Event;
  struct ReplayMarker;

  bool ContextIsValid(const ContextBinding& context,
                      std::uint32_t verified_window_owner_pid,
                      std::uint32_t verified_window_thread_id) const noexcept;
  BrokerResponse ArmEventLocked(Event* event, const ContextBinding& context,
                                std::uint64_t monotonic_now_ms);
  void ReleaseClaim(Event* event) noexcept;
  void EraseEvent(Event* event) noexcept;
  void ExpireDueLocked(std::uint64_t monotonic_now_ms);

  mutable std::mutex mutex_;
  PairSession session_;
  crypto::KeySchedule schedule_;
  bool ready_ = false;
  std::size_t live_capacity_ = 0;
  std::size_t replay_capacity_ = 0;
  std::uint64_t high_sequence_ = 0;
  PersistentSequenceAuthority* sequence_authority_ = nullptr;
  std::vector<Event> events_;
  std::vector<ReplayMarker> replay_;
};

}  // namespace clipvault::otp::broker
