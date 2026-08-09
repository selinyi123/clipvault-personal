#include "broker_protocol.h"
#include "otp_broker_core.h"

#include <algorithm>
#include <array>
#include <cstdint>
#include <iostream>
#include <string_view>
#include <vector>

namespace {

using namespace clipvault::otp;

int Nibble(char value) {
  if (value >= '0' && value <= '9') return value - '0';
  if (value >= 'a' && value <= 'f') return value - 'a' + 10;
  return -1;
}

std::vector<std::uint8_t> Hex(std::string_view input) {
  std::vector<std::uint8_t> output;
  if (input.size() % 2 != 0) return output;
  output.reserve(input.size() / 2);
  for (std::size_t index = 0; index < input.size(); index += 2) {
    const int high = Nibble(input[index]);
    const int low = Nibble(input[index + 1]);
    if (high < 0 || low < 0) return {};
    output.push_back(static_cast<std::uint8_t>((high << 4) | low));
  }
  return output;
}

template <std::size_t Size>
std::array<std::uint8_t, Size> HexArray(std::string_view input) {
  const auto decoded = Hex(input);
  std::array<std::uint8_t, Size> output{};
  if (decoded.size() == output.size()) {
    std::copy(decoded.begin(), decoded.end(), output.begin());
  }
  return output;
}

bool Expect(bool condition, std::string_view name) {
  if (!condition) std::cerr << "FAILED: " << name << '\n';
  return condition;
}

struct Fixture final {
  crypto::Sha256Bytes verifier = HexArray<32>(
      "1984dac1230b907d0d407910707577a37f0fa1d2676e3dec3903221edffb4a7d");
  crypto::UuidBytes epoch = HexArray<16>(
      "11111111111141118111111111111111");
  crypto::UuidBytes event = HexArray<16>(
      "22222222222242228222222222222222");
  crypto::UuidBytes sender = HexArray<16>(
      "33333333333343338333333333333333");
  crypto::UuidBytes target = HexArray<16>(
      "44444444444444448444444444444444");
  crypto::NonceBytes nonce = HexArray<12>("000102030405060708090a0b");

  broker::PairSession Session() const {
    return broker::PairSession{verifier, epoch, sender, target};
  }

  broker::OpaqueEnvelope VectorEnvelope() const {
    broker::OpaqueEnvelope envelope;
    envelope.version = 1;
    envelope.algorithm = 1;
    envelope.session_epoch = epoch;
    envelope.event_id = event;
    envelope.sender_device = sender;
    envelope.target_device = target;
    envelope.sequence = 42;
    envelope.issued_at_ms = 1'785'566'400'000ULL;
    envelope.expires_at_ms = 1'785'566'520'000ULL;
    envelope.nonce = nonce;
    envelope.ciphertext = Hex("89a93a853549");
    envelope.authentication_tag =
        HexArray<16>("bd37d5d249eda03302fbe64b0014d882");
    return envelope;
  }

  broker::OpaqueEnvelope Encrypt(std::uint64_t sequence,
                                 crypto::UuidBytes event_id,
                                 crypto::NonceBytes event_nonce,
                                 std::uint64_t issued,
                                 std::uint64_t expires) const {
    broker::OpaqueEnvelope envelope;
    envelope.version = 1;
    envelope.algorithm = 1;
    envelope.session_epoch = epoch;
    envelope.event_id = event_id;
    envelope.sender_device = sender;
    envelope.target_device = target;
    envelope.sequence = sequence;
    envelope.issued_at_ms = issued;
    envelope.expires_at_ms = expires;
    envelope.nonce = event_nonce;
    crypto::KeySchedule schedule;
    crypto::DeriveOtpKey(verifier, epoch, sender, target, &schedule);
    const crypto::EnvelopeFields fields{
        .protocol_version = 1,
        .session_epoch = epoch,
        .event_id = event_id,
        .sender_device = sender,
        .target_device = target,
        .sequence = sequence,
        .issued_at_unix_ms = issued,
        .expires_at_unix_ms = expires,
    };
    const auto aad = crypto::BuildAad(fields);
    const std::array<std::uint8_t, 6> code{'4', '8', '2', '9', '1', '7'};
    crypto::EncryptOtp(schedule.key, event_nonce, aad, code,
                       &envelope.ciphertext, &envelope.authentication_tag);
    crypto::SecureErase(schedule.key);
    crypto::SecureErase(schedule.prk);
    crypto::SecureErase(schedule.salt);
    crypto::SecureErase(schedule.info);
    return envelope;
  }
};

}  // namespace

int main() {
  bool ok = true;
  static_assert(broker::kPairSessionNonceCapacity == 4'096);
  const Fixture fixture;
  constexpr std::uint64_t wall_now = 1'785'566'410'000ULL;
  constexpr std::uint64_t monotonic_now = 50'000ULL;

  auto vector_envelope = fixture.VectorEnvelope();
  const auto encoded = broker::EncodeOffer(vector_envelope);
  broker::OpaqueEnvelope decoded;
  ok &= Expect(!encoded.empty() && broker::DecodeOffer(encoded, &decoded) &&
                   broker::EncodeOffer(decoded) == encoded,
               "12-field offer protocol round trip");
  auto trailing = encoded;
  trailing.push_back(0);
  ok &= Expect(!broker::DecodeOffer(trailing, &decoded),
               "strict broker frame length");

  broker::OtpBrokerCore core(fixture.Session());
  ok &= Expect(core.ready(), "broker session derives CNG key");
  auto changed_session = fixture.Session();
  changed_session.pair_verifier[0] ^= 1U;
  ok &= Expect(core.MatchesSession(fixture.Session()) &&
                   !core.MatchesSession(changed_session),
               "same epoch cannot replace verifier/key in-place");
  ok &= Expect(core.Offer(vector_envelope, wall_now, monotonic_now) ==
                   broker::BrokerStatus::kAccepted,
               "authenticated offer and delivery ACK");
  ok &= Expect(core.Offer(vector_envelope, wall_now, monotonic_now) ==
                   broker::BrokerStatus::kDuplicate,
               "duplicate sequence/event/nonce rejected");

  broker::ContextBinding context{
      .process_id = 4242,
      .thread_id = 77,
      .window_handle = 0x12345678ULL,
      .document_token = HexArray<16>("55555555555545558555555555555555"),
      .context_token = HexArray<16>("66666666666646668666666666666666"),
  };
  const auto arm_latest_frame = broker::EncodeArmLatest(context);
  broker::ContextBinding decoded_context;
  ok &= Expect(!arm_latest_frame.empty() &&
                   broker::DecodeArmLatest(arm_latest_frame, &decoded_context) &&
                   decoded_context == context,
               "strict arm-latest context round trip");
  const auto revoke_frame = broker::EncodeRevokeSession(fixture.epoch);
  crypto::UuidBytes decoded_revoke{};
  ok &= Expect(!revoke_frame.empty() &&
                   broker::DecodeRevokeSession(revoke_frame,
                                               &decoded_revoke) &&
                   decoded_revoke == fixture.epoch,
               "strict revoke-session epoch round trip");
  broker::ArmRequest arm{fixture.event, context};
  auto denied_arm = core.Arm(arm, 9999, 77, monotonic_now + 1);
  ok &= Expect(denied_arm.status == broker::BrokerStatus::kDenied,
               "window owner PID must match TSF context");
  auto armed = core.Arm(arm, 4242, 77, monotonic_now + 1);
  ok &= Expect(armed.status == broker::BrokerStatus::kAccepted &&
                   std::any_of(armed.claim_id.begin(), armed.claim_id.end(),
                               [](std::uint8_t byte) { return byte != 0; }),
               "context-bound short-lived claim");

  auto changed_context = context;
  changed_context.document_token[0] ^= 1U;
  broker::ConsumeRequest wrong_consume{armed.claim_id, changed_context};
  ok &= Expect(core.Consume(wrong_consume, 4242, 77, monotonic_now + 2).status ==
                   broker::BrokerStatus::kDenied,
               "changed document context denied");
  broker::ConsumeRequest consume{armed.claim_id, context};
  auto consumed = core.Consume(consume, 4242, 77, monotonic_now + 2);
  const std::vector<std::uint8_t> expected{'4', '8', '2', '9', '1', '7'};
  ok &= Expect(consumed.status == broker::BrokerStatus::kConsumed &&
                   consumed.secret == expected,
               "exactly-once consume returns mutable OTP lease");
  crypto::SecureErase(consumed.secret);
  ok &= Expect(core.Consume(consume, 4242, 77, monotonic_now + 3).status ==
                   broker::BrokerStatus::kNotFound,
               "second consume fails");

  auto same_nonce = fixture.Encrypt(
      43, HexArray<16>("66666666666646668666666666666666"), fixture.nonce,
      wall_now, wall_now + 120'000);
  ok &= Expect(core.Offer(same_nonce, wall_now, monotonic_now) ==
                   broker::BrokerStatus::kDuplicate,
               "nonce replay rejected after consume");

  auto bad_tag = fixture.Encrypt(
      43, HexArray<16>("77777777777747778777777777777777"),
      HexArray<12>("101112131415161718191a1b"), wall_now,
      wall_now + 120'000);
  bad_tag.authentication_tag[0] ^= 1U;
  ok &= Expect(core.Offer(bad_tag, wall_now, monotonic_now) ==
                   broker::BrokerStatus::kRejected,
               "CNG tag failure does not advance sequence");
  bad_tag.authentication_tag[0] ^= 1U;
  ok &= Expect(core.Offer(bad_tag, wall_now, monotonic_now) ==
                   broker::BrokerStatus::kAccepted,
               "valid retry after unauthenticated failure");

  auto expired = fixture.Encrypt(
      44, HexArray<16>("88888888888848888888888888888888"),
      HexArray<12>("202122232425262728292a2b"), wall_now - 120'000,
      wall_now);
  ok &= Expect(core.Offer(expired, wall_now, monotonic_now) ==
                   broker::BrokerStatus::kExpired,
               "expired envelope never enters local store");

  broker::OtpBrokerCore expiry_core(fixture.Session());
  auto short_lived = fixture.Encrypt(
      1, HexArray<16>("99999999999949998999999999999999"),
      HexArray<12>("303132333435363738393a3b"), wall_now,
      wall_now + 25);
  ok &= Expect(expiry_core.Offer(short_lived, wall_now, monotonic_now) ==
                   broker::BrokerStatus::kAccepted &&
                   expiry_core.ExpireDue(monotonic_now + 25) == 1,
               "single derived monotonic deadline expires payload");

  broker::OtpBrokerCore claim_expiry_core(fixture.Session());
  auto claim_event = fixture.Encrypt(
      1, HexArray<16>("aaaaaaaaaaaa4aaa8aaaaaaaaaaaaaaa"),
      HexArray<12>("404142434445464748494a4b"), wall_now,
      wall_now + 120'000);
  ok &= Expect(claim_expiry_core.Offer(claim_event, wall_now, monotonic_now) ==
                   broker::BrokerStatus::kAccepted,
               "claim-expiry fixture accepted");
  const auto first_claim = claim_expiry_core.ArmLatest(
      context, 4242, 77, monotonic_now + 1);
  const auto replacement_claim = claim_expiry_core.ArmLatest(
      context, 4242, 77, monotonic_now + 15'001);
  ok &= Expect(first_claim.status == broker::BrokerStatus::kAccepted &&
                   replacement_claim.status == broker::BrokerStatus::kAccepted &&
                   replacement_claim.claim_id != first_claim.claim_id,
               "expired claim releases still-live OTP for a fresh arm");
  auto replacement_consumed = claim_expiry_core.Consume(
      broker::ConsumeRequest{replacement_claim.claim_id, context}, 4242, 77,
      monotonic_now + 15'002);
  ok &= Expect(replacement_consumed.status == broker::BrokerStatus::kConsumed &&
                   replacement_consumed.secret == expected,
               "replacement claim consumes exactly once");
  crypto::SecureErase(replacement_consumed.secret);

  broker::OtpBrokerCore rotation_core(fixture.Session(), 1, 3);
  auto rotation_first = fixture.Encrypt(
      1, HexArray<16>("bbbbbbbbbbbb4bbb8bbbbbbbbbbbbbbb"),
      HexArray<12>("505152535455565758595a5b"), wall_now,
      wall_now + 120'000);
  auto rotation_second = fixture.Encrypt(
      2, HexArray<16>("cccccccccccc4ccc8ccccccccccccccc"),
      HexArray<12>("606162636465666768696a6b"), wall_now,
      wall_now + 120'000);
  auto rotation_third = fixture.Encrypt(
      3, HexArray<16>("dddddddddddd4ddd8ddddddddddddddd"),
      HexArray<12>("707172737475767778797a7b"), wall_now,
      wall_now + 120'000);
  ok &= Expect(rotation_core.Offer(rotation_first, wall_now, monotonic_now) ==
                       broker::BrokerStatus::kAccepted &&
                   rotation_core.Dismiss(rotation_first.event_id) ==
                       broker::BrokerStatus::kAccepted &&
                   rotation_core.Offer(rotation_second, wall_now,
                                       monotonic_now + 1) ==
                       broker::BrokerStatus::kAccepted &&
                   rotation_core.Dismiss(rotation_second.event_id) ==
                       broker::BrokerStatus::kAccepted &&
                   rotation_core.Offer(rotation_third, wall_now,
                                       monotonic_now + 2) ==
                       broker::BrokerStatus::kRotationRequired,
               "nonce ledger exhaustion requires a fresh pair epoch without LRU");
  broker::BrokerResponse rotation_response;
  rotation_response.status = broker::BrokerStatus::kRotationRequired;
  broker::BrokerResponse decoded_rotation_response;
  const auto encoded_rotation_response =
      broker::EncodeResponse(rotation_response);
  ok &= Expect(!encoded_rotation_response.empty() &&
                   broker::DecodeResponse(encoded_rotation_response,
                                          &decoded_rotation_response) &&
                   decoded_rotation_response.status ==
                       broker::BrokerStatus::kRotationRequired,
               "rotation-required is an explicit content-free wire status");
  broker::BrokerResponse empty_consumed_response;
  empty_consumed_response.status = broker::BrokerStatus::kConsumed;
  ok &= Expect(broker::EncodeResponse(empty_consumed_response).empty(),
               "consumed response requires a four-to-eight digit lease");
  broker::BrokerResponse stale_response;
  stale_response.status = broker::BrokerStatus::kConsumed;
  stale_response.claim_id.fill(0x7f);
  stale_response.secret = {'1', '2', '3', '4'};
  ok &= Expect(!broker::DecodeResponse({}, &stale_response) &&
                   stale_response.status == broker::BrokerStatus::kRejected &&
                   stale_response.secret.empty() &&
                   std::all_of(stale_response.claim_id.begin(),
                               stale_response.claim_id.end(),
                               [](std::uint8_t value) { return value == 0; }),
               "failed response decode erases a previous one-use lease");
  stale_response.status = broker::BrokerStatus::kConsumed;
  stale_response.secret = {'5', '6', '7', '8'};
  broker::BrokerPipeClient disconnected_client;
  ok &= Expect(!disconnected_client.ExchangeUntil(
                   std::vector<std::uint8_t>{1}, &stale_response,
                   GetTickCount64() + 1) &&
                   stale_response.status == broker::BrokerStatus::kRejected &&
                   stale_response.secret.empty(),
               "pre-I/O exchange failure erases a previous one-use lease");
  broker::OtpBrokerCore restarted_rotation_core(fixture.Session(), 1, 3, 2);
  ok &= Expect(restarted_rotation_core.ready() &&
                   restarted_rotation_core.Offer(
                       rotation_third, wall_now, monotonic_now + 3) ==
                       broker::BrokerStatus::kRotationRequired,
               "persisted sequence preserves the reserved rotation slot across restart");
  auto expired_rotation = rotation_third;
  expired_rotation.issued_at_ms = wall_now - 120;
  expired_rotation.expires_at_ms = wall_now - 1;
  ok &= Expect(restarted_rotation_core.Offer(
                   expired_rotation, wall_now, monotonic_now + 4) ==
                   broker::BrokerStatus::kRotationRequired,
               "expired final reservation still returns the repair signal");

  core.Clear();
  ok &= Expect(!core.ready() &&
                   core.Offer(bad_tag, wall_now, monotonic_now) ==
                       broker::BrokerStatus::kRejected,
               "session clear closes replay boundary");
  return ok ? 0 : 1;
}
