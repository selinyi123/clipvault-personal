#include "otp_broker_core.h"

#include <bcrypt.h>

#include <algorithm>
#include <limits>

namespace clipvault::otp::broker {
namespace {

constexpr std::uint64_t kMaximumTtlMilliseconds = 180'000;
constexpr std::uint64_t kMaximumFutureSkewMilliseconds = 30'000;
constexpr std::uint64_t kClaimTtlMilliseconds = 15'000;

bool IsZero(const crypto::UuidBytes& value) {
  return std::all_of(value.begin(), value.end(),
                     [](std::uint8_t byte) { return byte == 0; });
}

bool NewClaimId(crypto::UuidBytes* output) {
  if (output == nullptr || !BCRYPT_SUCCESS(BCryptGenRandom(
                               nullptr, output->data(),
                               static_cast<ULONG>(output->size()),
                               BCRYPT_USE_SYSTEM_PREFERRED_RNG))) {
    return false;
  }
  // Mark the random bytes as canonical RFC-4122 UUIDv4 bytes.
  (*output)[6] = static_cast<std::uint8_t>(((*output)[6] & 0x0fU) | 0x40U);
  (*output)[8] = static_cast<std::uint8_t>(((*output)[8] & 0x3fU) | 0x80U);
  return true;
}

// Decryption produces the only OTP plaintext buffer in the Broker.  Keep the
// wipe on the scope rather than relying on the normal return paths: vector
// allocation or insertion can throw after the durable sequence has advanced.
struct SensitiveVectorWipe final {
  std::vector<std::uint8_t>* value = nullptr;

  ~SensitiveVectorWipe() {
    if (value != nullptr) crypto::SecureErase(*value);
  }
};

}  // namespace

struct OtpBrokerCore::Event final {
  crypto::UuidBytes event_id{};
  std::uint64_t deadline_monotonic_ms = 0;
  std::vector<std::uint8_t> secret;
  std::optional<ContextBinding> claimed_context;
  crypto::UuidBytes claim_id{};
  std::uint64_t claim_deadline_monotonic_ms = 0;
};

struct OtpBrokerCore::ReplayMarker final {
  crypto::UuidBytes event_id{};
  crypto::NonceBytes nonce{};
};

OtpBrokerCore::OtpBrokerCore(const PairSession& session,
                             std::size_t live_capacity,
                             std::size_t replay_capacity,
                             std::uint64_t high_sequence,
                             PersistentSequenceAuthority* sequence_authority)
    : session_(session),
      live_capacity_(live_capacity),
      replay_capacity_(replay_capacity),
      high_sequence_(high_sequence),
      sequence_authority_(sequence_authority) {
  try {
    events_.reserve(live_capacity_);
    replay_.reserve(replay_capacity_);
    ready_ = live_capacity_ != 0 && replay_capacity_ >= live_capacity_ &&
             !IsZero(session_.session_epoch) &&
             !IsZero(session_.sender_device) &&
             !IsZero(session_.target_device) &&
             session_.sender_device != session_.target_device &&
             crypto::DeriveOtpKey(session_.pair_verifier,
                                  session_.session_epoch,
                                  session_.sender_device,
                                  session_.target_device, &schedule_);
  } catch (...) {
    // A throwing constructor does not run OtpBrokerCore::~OtpBrokerCore().
    // Erase copied credential material and any partial key schedule before the
    // member storage is released by constructor unwinding.
    crypto::SecureErase(session_.pair_verifier);
    crypto::SecureErase(schedule_.key);
    crypto::SecureErase(schedule_.prk);
    crypto::SecureErase(schedule_.salt);
    crypto::SecureErase(schedule_.info);
    throw;
  }
  if (!ready_) {
    crypto::SecureErase(session_.pair_verifier);
  }
  // A ready core deliberately retains the verifier until Clear(). The
  // PersistentSequenceAuthority compares it with the still-leased WinCred
  // record before advancing the durable replay high-water mark. Clearing it
  // here would make every production Offer fail with kUnavailable.
}

OtpBrokerCore::~OtpBrokerCore() {
  Clear();
  crypto::SecureErase(schedule_.key);
  crypto::SecureErase(schedule_.prk);
  crypto::SecureErase(schedule_.salt);
  crypto::SecureErase(schedule_.info);
}

bool OtpBrokerCore::ready() const noexcept { return ready_; }

bool OtpBrokerCore::MatchesSession(
    const PairSession& session) const noexcept {
  std::scoped_lock lock(mutex_);
  if (!ready_ || session.session_epoch != session_.session_epoch ||
      session.sender_device != session_.sender_device ||
      session.target_device != session_.target_device) {
    return false;
  }
  crypto::KeySchedule candidate;
  try {
    if (!crypto::DeriveOtpKey(session.pair_verifier, session.session_epoch,
                              session.sender_device, session.target_device,
                              &candidate)) {
      return false;
    }
    std::uint8_t difference = 0;
    for (std::size_t index = 0; index < candidate.key.size(); ++index) {
      difference |= static_cast<std::uint8_t>(candidate.key[index] ^
                                              schedule_.key[index]);
    }
    crypto::SecureErase(candidate.key);
    crypto::SecureErase(candidate.prk);
    crypto::SecureErase(candidate.salt);
    crypto::SecureErase(candidate.info);
    return difference == 0;
  } catch (...) {
    crypto::SecureErase(candidate.key);
    crypto::SecureErase(candidate.prk);
    crypto::SecureErase(candidate.salt);
    crypto::SecureErase(candidate.info);
    return false;
  }
}

BrokerStatus OtpBrokerCore::Offer(const OpaqueEnvelope& envelope,
                                  std::uint64_t wall_now_ms,
                                  std::uint64_t monotonic_now_ms) {
  std::scoped_lock lock(mutex_);
  ExpireDueLocked(monotonic_now_ms);
  if (!ready_ || envelope.version != 1 || envelope.algorithm != 1 ||
      envelope.session_epoch != session_.session_epoch ||
      envelope.sender_device != session_.sender_device ||
      envelope.target_device != session_.target_device || envelope.sequence == 0 ||
      envelope.sequence > static_cast<std::uint64_t>(INT64_MAX) ||
      (envelope.issued_at_ms > wall_now_ms &&
       envelope.issued_at_ms - wall_now_ms >
           kMaximumFutureSkewMilliseconds) ||
      envelope.expires_at_ms <= envelope.issued_at_ms ||
      envelope.expires_at_ms - envelope.issued_at_ms > kMaximumTtlMilliseconds) {
    return BrokerStatus::kRejected;
  }
  // The sender's persisted sequence is its durable count of nonce
  // reservations, while this Broker's replay vector resets on process restart.
  // Return the repair signal even if this final request arrived after its OTP
  // TTL; an ordinary expired response would strand a full sender nonce ledger.
  if (replay_capacity_ <= 1 || envelope.sequence >= replay_capacity_) {
    return BrokerStatus::kRotationRequired;
  }
  if (envelope.expires_at_ms <= wall_now_ms) return BrokerStatus::kExpired;
  if (envelope.sequence <= high_sequence_) return BrokerStatus::kDuplicate;
  const auto replayed = std::find_if(
      replay_.begin(), replay_.end(), [&](const ReplayMarker& marker) {
        return marker.event_id == envelope.event_id ||
               marker.nonce == envelope.nonce;
      });
  if (replayed != replay_.end()) return BrokerStatus::kDuplicate;
  // Keep the in-process replay ledger as an independent fail-closed bound for
  // malformed/non-consecutive senders; markers are never LRU-evicted.
  if (replay_.size() >= replay_capacity_ - 1) {
    return BrokerStatus::kRotationRequired;
  }
  if (events_.size() >= live_capacity_) {
    return BrokerStatus::kRejected;
  }

  crypto::EnvelopeFields aad_fields{
      .protocol_version = envelope.version,
      .session_epoch = envelope.session_epoch,
      .event_id = envelope.event_id,
      .sender_device = envelope.sender_device,
      .target_device = envelope.target_device,
      .sequence = envelope.sequence,
      .issued_at_unix_ms = envelope.issued_at_ms,
      .expires_at_unix_ms = envelope.expires_at_ms,
  };
  const auto aad = crypto::BuildAad(aad_fields);
  std::vector<std::uint8_t> secret;
  SensitiveVectorWipe wipe_secret{&secret};
  if (!crypto::DecryptOtp(schedule_.key, envelope.nonce, aad,
                          envelope.ciphertext,
                          envelope.authentication_tag, &secret)) {
    return BrokerStatus::kRejected;
  }

  // Persist the high-water mark before ownership/ACK. A Credential Manager
  // failure destroys the decrypted lease and fails closed, so a restart can
  // never acknowledge and later accept the same sequence again.
  if (sequence_authority_ != nullptr &&
      !sequence_authority_->AdvanceHighSequence(session_, envelope.sequence)) {
    return BrokerStatus::kUnavailable;
  }

  const std::uint64_t remaining = envelope.expires_at_ms - wall_now_ms;
  const std::uint64_t deadline =
      monotonic_now_ms > std::numeric_limits<std::uint64_t>::max() - remaining
          ? std::numeric_limits<std::uint64_t>::max()
          : monotonic_now_ms + remaining;
  Event event;
  event.event_id = envelope.event_id;
  event.deadline_monotonic_ms = deadline;
  event.secret = std::move(secret);
  events_.push_back(std::move(event));
  replay_.push_back(ReplayMarker{envelope.event_id, envelope.nonce});
  high_sequence_ = envelope.sequence;
  return BrokerStatus::kAccepted;  // Delivery ACK: ownership is now local.
}

BrokerResponse OtpBrokerCore::Arm(const ArmRequest& request,
                                  std::uint32_t verified_window_owner_pid,
                                  std::uint32_t verified_window_thread_id,
                                  std::uint64_t monotonic_now_ms) {
  std::scoped_lock lock(mutex_);
  ExpireDueLocked(monotonic_now_ms);
  BrokerResponse response;
  response.status = BrokerStatus::kDenied;
  if (!ContextIsValid(request.context, verified_window_owner_pid,
                      verified_window_thread_id)) {
    return response;
  }
  const auto event = std::find_if(events_.begin(), events_.end(),
                                  [&](const Event& candidate) {
                                    return candidate.event_id == request.event_id;
                                  });
  if (event == events_.end()) {
    response.status = BrokerStatus::kNotFound;
    return response;
  }
  return ArmEventLocked(&*event, request.context, monotonic_now_ms);
}

BrokerResponse OtpBrokerCore::ArmLatest(
    const ContextBinding& context, std::uint32_t verified_window_owner_pid,
    std::uint32_t verified_window_thread_id,
    std::uint64_t monotonic_now_ms) {
  std::scoped_lock lock(mutex_);
  ExpireDueLocked(monotonic_now_ms);
  BrokerResponse response;
  response.status = BrokerStatus::kDenied;
  if (!ContextIsValid(context, verified_window_owner_pid,
                      verified_window_thread_id)) {
    return response;
  }
  const auto event = std::find_if(events_.rbegin(), events_.rend(),
                                  [](const Event& candidate) {
                                    return !candidate.claimed_context.has_value();
                                  });
  if (event == events_.rend()) {
    response.status = BrokerStatus::kNotFound;
    return response;
  }
  return ArmEventLocked(&*event, context, monotonic_now_ms);
}

BrokerResponse OtpBrokerCore::Consume(
    const ConsumeRequest& request, std::uint32_t verified_window_owner_pid,
    std::uint32_t verified_window_thread_id,
    std::uint64_t monotonic_now_ms) {
  std::scoped_lock lock(mutex_);
  ExpireDueLocked(monotonic_now_ms);
  BrokerResponse response;
  response.status = BrokerStatus::kDenied;
  if (!ContextIsValid(request.context, verified_window_owner_pid,
                      verified_window_thread_id)) {
    return response;
  }
  const auto event = std::find_if(events_.begin(), events_.end(),
                                  [&](const Event& candidate) {
                                    return candidate.claim_id == request.claim_id;
                                  });
  if (event == events_.end()) {
    response.status = BrokerStatus::kNotFound;
    return response;
  }
  if (!event->claimed_context.has_value() ||
      *event->claimed_context != request.context ||
      monotonic_now_ms >= event->claim_deadline_monotonic_ms) {
    return response;
  }

  // Detach exactly once before returning plaintext. A failed downstream TSF
  // insert intentionally loses the OTP rather than making it reusable.
  response.secret = std::move(event->secret);
  response.status = BrokerStatus::kConsumed;
  EraseEvent(&*event);
  events_.erase(event);
  return response;
}

BrokerStatus OtpBrokerCore::Dismiss(const crypto::UuidBytes& event_id) {
  std::scoped_lock lock(mutex_);
  const auto event = std::find_if(events_.begin(), events_.end(),
                                  [&](const Event& candidate) {
                                    return candidate.event_id == event_id;
                                  });
  if (event == events_.end()) return BrokerStatus::kNotFound;
  EraseEvent(&*event);
  events_.erase(event);
  return BrokerStatus::kAccepted;
}

std::size_t OtpBrokerCore::ExpireDue(std::uint64_t monotonic_now_ms) {
  std::scoped_lock lock(mutex_);
  const std::size_t before = events_.size();
  ExpireDueLocked(monotonic_now_ms);
  return before - events_.size();
}

void OtpBrokerCore::Clear() noexcept {
  std::scoped_lock lock(mutex_);
  for (auto& event : events_) EraseEvent(&event);
  events_.clear();
  for (auto& marker : replay_) {
    crypto::SecureErase(marker.event_id);
    crypto::SecureErase(marker.nonce);
  }
  replay_.clear();
  ready_ = false;
  crypto::SecureErase(schedule_.key);
  crypto::SecureErase(schedule_.prk);
  crypto::SecureErase(schedule_.salt);
  crypto::SecureErase(schedule_.info);
  crypto::SecureErase(session_.pair_verifier);
  crypto::SecureErase(session_.session_epoch);
  crypto::SecureErase(session_.sender_device);
  crypto::SecureErase(session_.target_device);
}

bool OtpBrokerCore::ContextIsValid(
    const ContextBinding& context, std::uint32_t verified_window_owner_pid,
    std::uint32_t verified_window_thread_id) const noexcept {
  return context.process_id != 0 && context.thread_id != 0 &&
         context.window_handle != 0 && !IsZero(context.document_token) &&
         !IsZero(context.context_token) &&
         context.process_id == verified_window_owner_pid &&
         context.thread_id == verified_window_thread_id;
}

BrokerResponse OtpBrokerCore::ArmEventLocked(
    Event* event, const ContextBinding& context,
    std::uint64_t monotonic_now_ms) {
  BrokerResponse response;
  response.status = BrokerStatus::kDenied;
  if (event == nullptr || event->claimed_context.has_value()) return response;
  if (!NewClaimId(&event->claim_id)) {
    response.status = BrokerStatus::kUnavailable;
    return response;
  }
  event->claimed_context = context;
  event->claim_deadline_monotonic_ms =
      std::min(event->deadline_monotonic_ms,
               monotonic_now_ms + kClaimTtlMilliseconds);
  response.status = BrokerStatus::kAccepted;
  response.claim_id = event->claim_id;
  return response;
}

void OtpBrokerCore::EraseEvent(Event* event) noexcept {
  crypto::SecureErase(event->secret);
  ReleaseClaim(event);
  event->deadline_monotonic_ms = 0;
}

void OtpBrokerCore::ReleaseClaim(Event* event) noexcept {
  if (event == nullptr) return;
  crypto::SecureErase(event->claim_id);
  event->claimed_context.reset();
  event->claim_deadline_monotonic_ms = 0;
}

void OtpBrokerCore::ExpireDueLocked(std::uint64_t monotonic_now_ms) {
  auto first_expired = std::remove_if(
      events_.begin(), events_.end(), [&](Event& event) {
        const bool expired = monotonic_now_ms >= event.deadline_monotonic_ms;
        if (expired) {
          EraseEvent(&event);
        } else if (event.claimed_context.has_value() &&
                   monotonic_now_ms >=
                       event.claim_deadline_monotonic_ms) {
          ReleaseClaim(&event);
        }
        return expired;
      });
  events_.erase(first_expired, events_.end());
}

}  // namespace clipvault::otp::broker
