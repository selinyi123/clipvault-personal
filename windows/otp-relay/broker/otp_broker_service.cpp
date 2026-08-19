#include "otp_broker_service.h"

#include <algorithm>
#include <utility>

namespace clipvault::otp::broker {

OtpBrokerService::OtpBrokerService(
    authority::PairCredentialAuthority* authority,
    std::size_t session_capacity)
    : authority_(authority), session_capacity_(session_capacity) {
  slots_.reserve(session_capacity_);
}

OtpBrokerService::~OtpBrokerService() { Clear(); }

bool OtpBrokerService::ready() const noexcept {
  return authority_ != nullptr && session_capacity_ != 0;
}

BrokerStatus OtpBrokerService::Offer(const OpaqueEnvelope& envelope,
                                     std::uint64_t wall_now_ms,
                                     std::uint64_t monotonic_now_ms) {
  std::scoped_lock lock(mutex_);
  if (!ready()) return BrokerStatus::kUnavailable;
  auto slot = std::find_if(slots_.begin(), slots_.end(),
                           [&](const Slot& candidate) {
                             return candidate.session_epoch ==
                                    envelope.session_epoch;
                           });
  if (slot == slots_.end()) {
    if (slots_.size() >= session_capacity_) return BrokerStatus::kRejected;
    authority::PairCredential credential;
    if (!authority_->Load(envelope.session_epoch, &credential) ||
        credential.session.sender_device != envelope.sender_device ||
        credential.session.target_device != envelope.target_device) {
      return BrokerStatus::kRejected;
    }
    auto core = std::make_unique<OtpBrokerCore>(
        credential.session, 8, 128, credential.high_sequence, authority_);
    if (!core->ready()) return BrokerStatus::kUnavailable;
    slots_.push_back(Slot{envelope.session_epoch, 0, std::move(core)});
    slot = std::prev(slots_.end());
  }
  const BrokerStatus status =
      slot->core->Offer(envelope, wall_now_ms, monotonic_now_ms);
  if (status == BrokerStatus::kAccepted) {
    slot->last_offer_order = ++offer_order_;
  }
  return status;
}

BrokerResponse OtpBrokerService::ArmLatest(
    const ContextBinding& context, std::uint32_t verified_window_owner_pid,
    std::uint32_t verified_window_thread_id,
    std::uint64_t monotonic_now_ms) {
  std::scoped_lock lock(mutex_);
  BrokerResponse result;
  result.status = BrokerStatus::kNotFound;
  std::vector<Slot*> ordered;
  ordered.reserve(slots_.size());
  for (auto& slot : slots_) ordered.push_back(&slot);
  std::sort(ordered.begin(), ordered.end(), [](const Slot* left,
                                                const Slot* right) {
    return left->last_offer_order > right->last_offer_order;
  });
  for (Slot* slot : ordered) {
    auto candidate = slot->core->ArmLatest(
        context, verified_window_owner_pid, verified_window_thread_id,
        monotonic_now_ms);
    if (candidate.status == BrokerStatus::kAccepted ||
        candidate.status == BrokerStatus::kDenied ||
        candidate.status == BrokerStatus::kUnavailable) {
      return candidate;
    }
  }
  return result;
}

BrokerResponse OtpBrokerService::Consume(
    const ConsumeRequest& request, std::uint32_t verified_window_owner_pid,
    std::uint32_t verified_window_thread_id,
    std::uint64_t monotonic_now_ms) {
  std::scoped_lock lock(mutex_);
  BrokerResponse result;
  result.status = BrokerStatus::kNotFound;
  for (auto& slot : slots_) {
    auto candidate = slot.core->Consume(
        request, verified_window_owner_pid, verified_window_thread_id,
        monotonic_now_ms);
    if (candidate.status == BrokerStatus::kConsumed ||
        candidate.status == BrokerStatus::kDenied ||
        candidate.status == BrokerStatus::kUnavailable) {
      return candidate;
    }
  }
  return result;
}

BrokerStatus OtpBrokerService::Dismiss(const crypto::UuidBytes& event_id) {
  std::scoped_lock lock(mutex_);
  for (auto& slot : slots_) {
    const auto status = slot.core->Dismiss(event_id);
    if (status != BrokerStatus::kNotFound) return status;
  }
  return BrokerStatus::kNotFound;
}

std::size_t OtpBrokerService::ExpireDue(std::uint64_t monotonic_now_ms) {
  std::scoped_lock lock(mutex_);
  std::size_t expired = 0;
  for (auto& slot : slots_) expired += slot.core->ExpireDue(monotonic_now_ms);
  return expired;
}

void OtpBrokerService::Clear() noexcept {
  std::scoped_lock lock(mutex_);
  for (auto& slot : slots_) {
    slot.core->Clear();
    crypto::SecureErase(slot.session_epoch);
  }
  slots_.clear();
  offer_order_ = 0;
}

}  // namespace clipvault::otp::broker
