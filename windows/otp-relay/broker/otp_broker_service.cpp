#include "otp_broker_service.h"

#include <algorithm>
#include <utility>

namespace clipvault::otp::broker {

namespace {

constexpr DWORD kDefaultCredentialMutexBudgetMilliseconds = 100;

DWORD RemainingCredentialMutexBudget(ULONGLONG deadline_tick) noexcept {
  if (deadline_tick == 0) return kDefaultCredentialMutexBudgetMilliseconds;
  const ULONGLONG now = GetTickCount64();
  if (now >= deadline_tick) return 0;
  return static_cast<DWORD>(std::min<ULONGLONG>(
      deadline_tick - now, MAXDWORD));
}

bool DeadlineExpired(ULONGLONG deadline_tick) noexcept {
  return deadline_tick != 0 && GetTickCount64() >= deadline_tick;
}

}  // namespace

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

authority::CredentialAcquireStatus
OtpBrokerService::AcquireCurrentCredentialLocked(
    Slot& slot, authority::PairCredentialLease* lease,
    ULONGLONG deadline_tick) noexcept {
  if (authority_ == nullptr || lease == nullptr || slot.core == nullptr) {
    return authority::CredentialAcquireStatus::kUnavailable;
  }
  // AcquireDetailed performs the durable revocation check while holding the
  // same mutation mutex as the CVPK read.  Do not perform a separate check
  // here: it would double Credential Manager I/O and could consume the short
  // Host deadline without adding a stronger ordering guarantee.
  const auto status = authority_->AcquireDetailed(
      slot.session_epoch, lease,
      RemainingCredentialMutexBudget(deadline_tick));
  if (status != authority::CredentialAcquireStatus::kAcquired) return status;
  if (lease->get() == nullptr ||
      !slot.core->MatchesSession(lease->get()->session)) {
    lease->Reset();
    return authority::CredentialAcquireStatus::kInvalid;
  }
  return authority::CredentialAcquireStatus::kAcquired;
}

bool OtpBrokerService::IsRevokedSessionLocked(
    const crypto::UuidBytes& session_epoch) const noexcept {
  return std::find(revoked_sessions_.begin(), revoked_sessions_.end(),
                   session_epoch) != revoked_sessions_.end();
}

bool OtpBrokerService::RememberRevokedSessionLocked(
    const crypto::UuidBytes& session_epoch) noexcept {
  if (IsRevokedSessionLocked(session_epoch)) return true;
  if (revoked_sessions_.size() >= kRevokedSessionFenceCapacity) return false;
  try {
    revoked_sessions_.push_back(session_epoch);
    return true;
  } catch (...) {
    return false;
  }
}

void OtpBrokerService::ClearSlotLocked(Slot* slot) noexcept {
  if (slot == nullptr) return;
  if (slot->core != nullptr) slot->core->Clear();
  crypto::SecureErase(slot->session_epoch);
  slot->last_offer_order = 0;
}

authority::CredentialAcquireStatus OtpBrokerService::PruneOneInvalidSlotLocked(
    ULONGLONG deadline_tick) noexcept {
  if (slots_.empty()) {
    credential_sweep_cursor_ = 0;
    return authority::CredentialAcquireStatus::kInvalid;
  }
  if (credential_sweep_cursor_ >= slots_.size()) {
    credential_sweep_cursor_ = 0;
  }
  auto slot = slots_.begin() +
              static_cast<std::ptrdiff_t>(credential_sweep_cursor_);
  authority::PairCredentialLease lease;
  const auto acquire_status =
      AcquireCurrentCredentialLocked(*slot, &lease, deadline_tick);
  if (acquire_status != authority::CredentialAcquireStatus::kAcquired) {
    // A caller deadline is a transport budget, not evidence that the durable
    // credential became invalid.  Do not evict a healthy slot merely because
    // the mutex wait consumed the request's remaining time.
    if (DeadlineExpired(deadline_tick) ||
        acquire_status == authority::CredentialAcquireStatus::kUnavailable) {
      return authority::CredentialAcquireStatus::kUnavailable;
    }
    ClearSlotLocked(&*slot);
    slots_.erase(slot);
    if (credential_sweep_cursor_ >= slots_.size()) {
      credential_sweep_cursor_ = 0;
    }
    return authority::CredentialAcquireStatus::kInvalid;
  }
  credential_sweep_cursor_ =
      (credential_sweep_cursor_ + 1) % slots_.size();
  return authority::CredentialAcquireStatus::kAcquired;
}

BrokerStatus OtpBrokerService::Offer(const OpaqueEnvelope& envelope,
                                     std::uint64_t wall_now_ms,
                                     std::uint64_t monotonic_now_ms,
                                     ULONGLONG deadline_tick) {
  std::scoped_lock lock(mutex_);
  if (!ready()) return BrokerStatus::kUnavailable;
  // A request that has already exhausted its caller-owned budget must not
  // enter the capacity/pruning path.  Pruning uses the same deadline and
  // would otherwise mistake an expired wait for an invalid credential,
  // evicting a healthy cached session as a side effect of a timed-out offer.
  if (DeadlineExpired(deadline_tick)) return BrokerStatus::kUnavailable;
  // Check the process-lifetime fence before consulting Credential Manager. A
  // late Offer must not recreate a slot after a revoke whose durable delete
  // is still being repaired.
  if (IsRevokedSessionLocked(envelope.session_epoch)) {
    return BrokerStatus::kRejected;
  }
  auto slot = std::find_if(slots_.begin(), slots_.end(),
                           [&](const Slot& candidate) {
                             return candidate.session_epoch ==
                                    envelope.session_epoch;
                           });
  if (slot == slots_.end()) {
    if (slots_.size() >= session_capacity_ &&
        PruneOneInvalidSlotLocked(deadline_tick) ==
            authority::CredentialAcquireStatus::kUnavailable) {
      return BrokerStatus::kUnavailable;
    }
    if (slots_.size() >= session_capacity_) return BrokerStatus::kRejected;
    authority::PairCredentialLease lease;
    const auto acquire_status = authority_->AcquireDetailed(
        envelope.session_epoch, &lease,
        RemainingCredentialMutexBudget(deadline_tick));
    if (acquire_status == authority::CredentialAcquireStatus::kUnavailable) {
      return BrokerStatus::kUnavailable;
    }
    if (acquire_status != authority::CredentialAcquireStatus::kAcquired ||
        lease.get() == nullptr ||
        lease.get()->session.sender_device != envelope.sender_device ||
        lease.get()->session.target_device != envelope.target_device) {
      return BrokerStatus::kRejected;
    }
    auto core = std::make_unique<OtpBrokerCore>(
        lease.get()->session, 8, kPairSessionNonceCapacity,
        lease.get()->high_sequence, authority_);
    if (!core->ready()) return BrokerStatus::kUnavailable;
    slots_.push_back(Slot{envelope.session_epoch, 0, std::move(core)});
    slot = std::prev(slots_.end());
  }
  authority::PairCredentialLease lease;
  const auto acquire_status =
      AcquireCurrentCredentialLocked(*slot, &lease, deadline_tick);
  if (acquire_status != authority::CredentialAcquireStatus::kAcquired) {
    if (DeadlineExpired(deadline_tick)) {
      return BrokerStatus::kUnavailable;
    }
    if (acquire_status == authority::CredentialAcquireStatus::kUnavailable) {
      return BrokerStatus::kUnavailable;
    }
    ClearSlotLocked(&*slot);
    slots_.erase(slot);
    return BrokerStatus::kRejected;
  }
  if (DeadlineExpired(deadline_tick)) return BrokerStatus::kUnavailable;
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
    std::uint64_t monotonic_now_ms, ULONGLONG deadline_tick) {
  std::scoped_lock lock(mutex_);
  BrokerResponse result;
  result.status = BrokerStatus::kNotFound;
  // Do not sweep valid slots merely because the caller arrived after its
  // absolute deadline.  The operation is unavailable, not a credential
  // invalidation signal.
  if (DeadlineExpired(deadline_tick)) {
    result.status = BrokerStatus::kUnavailable;
    return result;
  }
  std::vector<std::pair<std::uint64_t, crypto::UuidBytes>> ordered;
  ordered.reserve(slots_.size());
  for (auto& slot : slots_)
    ordered.emplace_back(slot.last_offer_order, slot.session_epoch);
  std::sort(ordered.begin(), ordered.end(), [](const auto& left,
                                                const auto& right) {
    return left.first > right.first;
  });
  for (const auto& entry : ordered) {
    auto slot = std::find_if(
        slots_.begin(), slots_.end(), [&](const Slot& value) {
          return value.session_epoch == entry.second;
        });
    if (slot == slots_.end()) continue;
    authority::PairCredentialLease lease;
    const auto acquire_status =
        AcquireCurrentCredentialLocked(*slot, &lease, deadline_tick);
    if (acquire_status != authority::CredentialAcquireStatus::kAcquired) {
      if (DeadlineExpired(deadline_tick)) {
        result.status = BrokerStatus::kUnavailable;
        return result;
      }
      if (acquire_status == authority::CredentialAcquireStatus::kUnavailable) {
        result.status = BrokerStatus::kUnavailable;
        return result;
      }
      ClearSlotLocked(&*slot);
      slots_.erase(slot);
      continue;
    }
    if (DeadlineExpired(deadline_tick)) {
      result.status = BrokerStatus::kUnavailable;
      return result;
    }
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
    std::uint64_t monotonic_now_ms, ULONGLONG deadline_tick) {
  std::scoped_lock lock(mutex_);
  BrokerResponse result;
  result.status = BrokerStatus::kNotFound;
  if (DeadlineExpired(deadline_tick)) {
    result.status = BrokerStatus::kUnavailable;
    return result;
  }
  auto slot = slots_.begin();
  while (slot != slots_.end()) {
    authority::PairCredentialLease lease;
    const auto acquire_status =
        AcquireCurrentCredentialLocked(*slot, &lease, deadline_tick);
    if (acquire_status != authority::CredentialAcquireStatus::kAcquired) {
      if (DeadlineExpired(deadline_tick)) {
        result.status = BrokerStatus::kUnavailable;
        return result;
      }
      if (acquire_status == authority::CredentialAcquireStatus::kUnavailable) {
        result.status = BrokerStatus::kUnavailable;
        return result;
      }
      ClearSlotLocked(&*slot);
      slot = slots_.erase(slot);
      continue;
    }
    if (DeadlineExpired(deadline_tick)) {
      result.status = BrokerStatus::kUnavailable;
      return result;
    }
    auto candidate = slot->core->Consume(
        request, verified_window_owner_pid, verified_window_thread_id,
        monotonic_now_ms);
    if (candidate.status == BrokerStatus::kConsumed ||
        candidate.status == BrokerStatus::kDenied ||
        candidate.status == BrokerStatus::kUnavailable) {
      return candidate;
    }
    ++slot;
  }
  return result;
}

BrokerStatus OtpBrokerService::Dismiss(const crypto::UuidBytes& event_id) {
  std::scoped_lock lock(mutex_);
  auto slot = slots_.begin();
  while (slot != slots_.end()) {
    authority::PairCredentialLease lease;
    const auto acquire_status = AcquireCurrentCredentialLocked(*slot, &lease);
    if (acquire_status != authority::CredentialAcquireStatus::kAcquired) {
      if (acquire_status == authority::CredentialAcquireStatus::kUnavailable) {
        return BrokerStatus::kUnavailable;
      }
      ClearSlotLocked(&*slot);
      slot = slots_.erase(slot);
      continue;
    }
    const auto status = slot->core->Dismiss(event_id);
    if (status != BrokerStatus::kNotFound) return status;
    ++slot;
  }
  return BrokerStatus::kNotFound;
}

BrokerStatus OtpBrokerService::RevokeSession(
    const crypto::UuidBytes& session_epoch, ULONGLONG deadline_tick) {
  std::scoped_lock lock(mutex_);
  if (authority::PairCredentialAuthority::TargetForSession(session_epoch)
          .empty()) {
    return BrokerStatus::kRejected;
  }
  if (IsRevokedSessionLocked(session_epoch)) {
    // A prior revoke may have installed the in-process fence before the
    // durable Credential Manager delete completed.  Keep the fence in place,
    // but retry the durable delete on a later control request instead of
    // treating the first failed cleanup as success forever.
    if (authority_ == nullptr ||
        !authority_->Revoke(
            session_epoch, RemainingCredentialMutexBudget(deadline_tick))) {
      return BrokerStatus::kUnavailable;
    }
    return BrokerStatus::kAccepted;
  }
  if (!RememberRevokedSessionLocked(session_epoch)) {
    return BrokerStatus::kUnavailable;
  }
  const auto slot = std::find_if(slots_.begin(), slots_.end(),
                                 [&](const Slot& candidate) {
                                   return candidate.session_epoch ==
                                          session_epoch;
                                 });
  if (slot != slots_.end()) {
    ClearSlotLocked(&*slot);
    slots_.erase(slot);
  }
  // Keep the fence even when the durable delete fails. This makes the failure
  // recoverable without reopening the old CVPK to a late Offer.
  if (authority_ == nullptr ||
      !authority_->Revoke(
          session_epoch, RemainingCredentialMutexBudget(deadline_tick))) {
    return BrokerStatus::kUnavailable;
  }
  // Revocation is idempotent: a missing Credential Manager record is success.
  return BrokerStatus::kAccepted;
}

std::size_t OtpBrokerService::ExpireDue(std::uint64_t monotonic_now_ms) {
  std::scoped_lock lock(mutex_);
  std::size_t expired = 0;
  // The main loop calls this before accepting each pipe. Do not perform a
  // Credential Manager wait here: Host OTP insertion has a 35 ms budget.
  // Explicit RevokeSession clears healthy revocations; Arm/Consume revalidate
  // fail-closed, and a capacity-bound new Offer prunes one stale slot.
  for (auto& slot : slots_) expired += slot.core->ExpireDue(monotonic_now_ms);
  return expired;
}

void OtpBrokerService::Clear() noexcept {
  std::scoped_lock lock(mutex_);
  for (auto& slot : slots_) {
    ClearSlotLocked(&slot);
  }
  slots_.clear();
  for (auto& session_epoch : revoked_sessions_) {
    crypto::SecureErase(session_epoch);
  }
  revoked_sessions_.clear();
  offer_order_ = 0;
  credential_sweep_cursor_ = 0;
}

}  // namespace clipvault::otp::broker
