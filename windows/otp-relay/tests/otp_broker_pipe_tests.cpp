#include "broker_protocol.h"
#include "broker_server.h"
#include "otp_broker_service.h"
#include "pair_credential.h"

#include <windows.h>
#include <bcrypt.h>
#include <wincred.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <iostream>
#include <string_view>
#include <thread>
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
  for (std::size_t index = 0; index + 1 < input.size(); index += 2) {
    output.push_back(static_cast<std::uint8_t>(
        (Nibble(input[index]) << 4) | Nibble(input[index + 1])));
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

broker::PairSession VectorSession() {
  static const broker::PairSession session = [] {
    broker::PairSession value{
      HexArray<32>(
          "1984dac1230b907d0d407910707577a37f0fa1d2676e3dec3903221edffb4a7d"),
      HexArray<16>("11111111111141118111111111111111"),
      HexArray<16>("33333333333343338333333333333333"),
      HexArray<16>("44444444444444448444444444444444"),
    };
    BCryptGenRandom(nullptr, value.session_epoch.data(),
                    static_cast<ULONG>(value.session_epoch.size()),
                    BCRYPT_USE_SYSTEM_PREFERRED_RNG);
    value.session_epoch[6] = static_cast<std::uint8_t>(
        (value.session_epoch[6] & 0x0fU) | 0x40U);
    value.session_epoch[8] = static_cast<std::uint8_t>(
        (value.session_epoch[8] & 0x3fU) | 0x80U);
    return value;
  }();
  return session;
}

broker::OpaqueEnvelope VectorEnvelope(const broker::PairSession& session) {
  broker::OpaqueEnvelope envelope;
  envelope.version = 1;
  envelope.algorithm = 1;
  envelope.session_epoch = session.session_epoch;
  envelope.event_id = HexArray<16>("22222222222242228222222222222222");
  envelope.sender_device =
      HexArray<16>("33333333333343338333333333333333");
  envelope.target_device =
      HexArray<16>("44444444444444448444444444444444");
  envelope.sequence = 42;
  envelope.issued_at_ms = 1'785'566'400'000ULL;
  envelope.expires_at_ms = 1'785'566'520'000ULL;
  envelope.nonce = HexArray<12>("000102030405060708090a0b");
  envelope.ciphertext = Hex("89a93a853549");
  envelope.authentication_tag =
      HexArray<16>("bd37d5d249eda03302fbe64b0014d882");
  return envelope;
}

class AllowSelf final : public broker::BrokerClientAuthorizer {
 public:
  bool Authorize(DWORD process_id, broker::BrokerClientRole) noexcept override {
    return process_id == GetCurrentProcessId();
  }
};

bool WriteCvpk(const broker::PairSession& session, const std::wstring& target) {
  authority::PairCredential record;
  record.session = session;
  std::array<std::uint8_t, authority::kPairCredentialBytes> blob{};
  if (!authority::PairCredentialAuthority::Encode(record, blob.data(),
                                                   blob.size())) return false;
  CREDENTIALW credential{};
  credential.Type = CRED_TYPE_GENERIC;
  credential.TargetName = const_cast<wchar_t*>(target.c_str());
  credential.CredentialBlobSize = static_cast<DWORD>(blob.size());
  credential.CredentialBlob = blob.data();
  credential.Persist = CRED_PERSIST_SESSION;
  wchar_t username[] = L"ClipVault OTP pipe test";
  credential.UserName = username;
  const bool wrote = CredWriteW(&credential, 0) != FALSE;
  crypto::SecureErase(blob);
  return wrote;
}

broker::OpaqueEnvelope CurrentEnvelope(const broker::PairSession& session,
                                       std::uint64_t wall_now_ms) {
  broker::OpaqueEnvelope envelope = VectorEnvelope(session);
  envelope.event_id =
      HexArray<16>("55555555555545559555555555555555");
  envelope.sequence = 43;
  envelope.issued_at_ms = wall_now_ms;
  envelope.expires_at_ms = wall_now_ms + 120'000;
  envelope.nonce = HexArray<12>("0c0d0e0f1011121314151617");

  crypto::KeySchedule schedule;
  if (!crypto::DeriveOtpKey(session.pair_verifier, session.session_epoch,
                            session.sender_device, session.target_device,
                            &schedule)) {
    return {};
  }
  const crypto::EnvelopeFields fields{
      .protocol_version = envelope.version,
      .session_epoch = envelope.session_epoch,
      .event_id = envelope.event_id,
      .sender_device = envelope.sender_device,
      .target_device = envelope.target_device,
      .sequence = envelope.sequence,
      .issued_at_unix_ms = envelope.issued_at_ms,
      .expires_at_unix_ms = envelope.expires_at_ms,
  };
  const auto aad = crypto::BuildAad(fields);
  const std::array<std::uint8_t, 6> plaintext{'4', '8', '2', '9', '1', '7'};
  if (!crypto::EncryptOtp(schedule.key, envelope.nonce, aad, plaintext,
                          &envelope.ciphertext,
                          &envelope.authentication_tag)) {
    envelope = {};
  }
  crypto::SecureErase(schedule.key);
  crypto::SecureErase(schedule.prk);
  crypto::SecureErase(schedule.salt);
  crypto::SecureErase(schedule.info);
  return envelope;
}

}  // namespace

int wmain() {
  bool ok = true;
  const std::wstring test_namespace =
      L"pipe-" + std::to_wstring(GetCurrentProcessId()) + L"-" +
      std::to_wstring(GetTickCount64());
  if (!SetEnvironmentVariableW(L"CLIPVAULT_OTP_TEST_NAMESPACE",
                               test_namespace.c_str()) ||
      !SetEnvironmentVariableW(L"CLIPVAULT_INSECURE_TEST_PIPE_TRUST", L"1")) {
    return 2;
  }

  const auto session = VectorSession();
  const auto credential_target =
      authority::PairCredentialAuthority::TargetForSession(
          session.session_epoch);
  CredDeleteW(credential_target.c_str(), CRED_TYPE_GENERIC, 0);
  if (!WriteCvpk(session, credential_target)) return 3;
  authority::PairCredentialAuthority credential_authority;
  authority::PairCredential loaded_for_probe;
  ok &= Expect(credential_authority.Load(session.session_epoch,
                                         &loaded_for_probe) &&
                   loaded_for_probe.session.session_epoch ==
                       session.session_epoch &&
                   loaded_for_probe.session.sender_device ==
                       session.sender_device &&
                   loaded_for_probe.session.target_device ==
                       session.target_device &&
                   loaded_for_probe.session.pair_verifier ==
                       session.pair_verifier,
               "WinCred probe loads exact pair before pipe");
  broker::OtpBrokerService service(&credential_authority);
  AllowSelf allow_self;
  broker::BrokerPipeServer server(&service, &allow_self);
  std::atomic_bool offer_server_result = false;
  std::thread server_thread(
      [&] { offer_server_result = server.ServeOne(3'000, 500); });
  broker::BrokerPipeClient client;
  const auto wall_now_ms = static_cast<std::uint64_t>(
      std::chrono::duration_cast<std::chrono::milliseconds>(
          std::chrono::system_clock::now().time_since_epoch())
          .count());
  const auto envelope = CurrentEnvelope(session, wall_now_ms);
  ok &= Expect(envelope.ciphertext.size() == 6,
               "CNG probe produced bounded ciphertext");
  crypto::KeySchedule probe_schedule;
  const crypto::EnvelopeFields probe_fields{
      .protocol_version = envelope.version,
      .session_epoch = envelope.session_epoch,
      .event_id = envelope.event_id,
      .sender_device = envelope.sender_device,
      .target_device = envelope.target_device,
      .sequence = envelope.sequence,
      .issued_at_unix_ms = envelope.issued_at_ms,
      .expires_at_unix_ms = envelope.expires_at_ms,
  };
  const auto probe_aad = crypto::BuildAad(probe_fields);
  std::vector<std::uint8_t> probe_secret;
  ok &= Expect(
      crypto::DeriveOtpKey(loaded_for_probe.session.pair_verifier,
                           loaded_for_probe.session.session_epoch,
                           loaded_for_probe.session.sender_device,
                           loaded_for_probe.session.target_device,
                           &probe_schedule) &&
          crypto::DecryptOtp(probe_schedule.key, envelope.nonce, probe_aad,
                             envelope.ciphertext,
                             envelope.authentication_tag, &probe_secret) &&
          probe_secret ==
              std::vector<std::uint8_t>({'4', '8', '2', '9', '1', '7'}),
      "WinCred-derived CNG key decrypts the current envelope");
  crypto::SecureErase(probe_secret);
  crypto::SecureErase(probe_schedule.key);
  crypto::SecureErase(probe_schedule.prk);
  crypto::SecureErase(probe_schedule.salt);
  crypto::SecureErase(probe_schedule.info);
  broker::OpaqueEnvelope decoded_offer;
  const auto encoded_offer = broker::EncodeOffer(envelope);
  ok &= Expect(broker::DecodeOffer(encoded_offer, &decoded_offer) &&
                   decoded_offer.version == envelope.version &&
                   decoded_offer.algorithm == envelope.algorithm &&
                   decoded_offer.session_epoch == envelope.session_epoch &&
                   decoded_offer.event_id == envelope.event_id &&
                   decoded_offer.sender_device == envelope.sender_device &&
                   decoded_offer.target_device == envelope.target_device &&
                   decoded_offer.sequence == envelope.sequence &&
                   decoded_offer.issued_at_ms == envelope.issued_at_ms &&
                   decoded_offer.expires_at_ms == envelope.expires_at_ms &&
                   decoded_offer.nonce == envelope.nonce &&
                   decoded_offer.ciphertext == envelope.ciphertext &&
                   decoded_offer.authentication_tag ==
                       envelope.authentication_tag,
               "offer wire round trip preserves authenticated fields");
  const ULONGLONG deadline = GetTickCount64() + 1'000;
  broker::BrokerResponse response;
  ok &= Expect(client.ConnectUntil(deadline) &&
                   client.ExchangeUntil(broker::EncodeOffer(envelope),
                                        &response, deadline) &&
                   response.status == broker::BrokerStatus::kAccepted,
               "real per-user pipe round trip accepts authenticated offer");
  if (response.status != broker::BrokerStatus::kAccepted)
    std::cerr << "offer status=" << static_cast<int>(response.status) << '\n';
  client.Close();
  server_thread.join();
  ok &= Expect(offer_server_result, "offer server completes one exchange");

  broker::OtpBrokerService restarted_service(&credential_authority);
  ok &= Expect(restarted_service.Offer(envelope, wall_now_ms,
                                       GetTickCount64()) ==
                   broker::BrokerStatus::kDuplicate,
               "WinCred high sequence rejects replay after broker restart");

  HWND test_window = CreateWindowExW(0, L"STATIC", L"", WS_OVERLAPPED,
                                     0, 0, 10, 10, nullptr, nullptr,
                                     GetModuleHandleW(nullptr), nullptr);
  broker::ContextBinding context{
      .process_id = GetCurrentProcessId(),
      .thread_id = GetCurrentThreadId(),
      .window_handle = static_cast<std::uint64_t>(
          reinterpret_cast<std::uintptr_t>(test_window)),
      .document_token =
          HexArray<16>("66666666666646668666666666666666"),
      .context_token =
          HexArray<16>("77777777777747778777777777777777"),
  };
  std::atomic_bool arm_server_result = false;
  std::thread arm_server(
      [&] { arm_server_result = server.ServeOne(3'000, 500); });
  broker::BrokerPipeClient arm_client;
  broker::BrokerResponse armed;
  const ULONGLONG arm_deadline = GetTickCount64() + 1'000;
  ok &= Expect(test_window != nullptr && arm_client.ConnectUntil(arm_deadline) &&
                   arm_client.ExchangeUntil(broker::EncodeArmLatest(context),
                                            &armed, arm_deadline) &&
                   armed.status == broker::BrokerStatus::kAccepted,
               "real pipe creates context-bound latest claim");
  if (armed.status != broker::BrokerStatus::kAccepted)
    std::cerr << "arm status=" << static_cast<int>(armed.status) << '\n';
  arm_client.Close();
  arm_server.join();
  ok &= Expect(arm_server_result, "arm server completes one exchange");

  std::atomic_bool consume_server_result = false;
  std::thread consume_server(
      [&] { consume_server_result = server.ServeOne(3'000, 500); });
  broker::BrokerPipeClient consume_client;
  broker::BrokerResponse consumed;
  const ULONGLONG consume_deadline = GetTickCount64() + 1'000;
  ok &= Expect(
      consume_client.ConnectUntil(consume_deadline) &&
          consume_client.ExchangeUntil(
              broker::EncodeConsume(
                  broker::ConsumeRequest{armed.claim_id, context}),
              &consumed, consume_deadline) &&
          consumed.status == broker::BrokerStatus::kConsumed &&
          consumed.secret ==
              std::vector<std::uint8_t>({'4', '8', '2', '9', '1', '7'}),
      "real pipe consumes one mutable OTP lease");
  if (consumed.status != broker::BrokerStatus::kConsumed)
    std::cerr << "consume status=" << static_cast<int>(consumed.status) << '\n';
  crypto::SecureErase(consumed.secret);
  consume_client.Close();
  consume_server.join();
  ok &= Expect(consume_server_result, "consume server completes one exchange");

  std::atomic_bool revoke_server_result = false;
  std::thread revoke_server(
      [&] { revoke_server_result = server.ServeOne(3'000, 500); });
  broker::BrokerPipeClient revoke_client;
  broker::BrokerResponse revoked;
  const ULONGLONG revoke_deadline = GetTickCount64() + 1'000;
  ok &= Expect(
      revoke_client.ConnectUntil(revoke_deadline) &&
          revoke_client.ExchangeUntil(
              broker::EncodeRevokeSession(session.session_epoch), &revoked,
              revoke_deadline) &&
          revoked.status == broker::BrokerStatus::kAccepted,
      "Desktop-authorized revoke clears the cached session idempotently");
  revoke_client.Close();
  revoke_server.join();
  ok &= Expect(revoke_server_result, "revoke server completes one exchange");
  ok &= Expect(service.ArmLatest(context, GetCurrentProcessId(),
                                 GetCurrentThreadId(), GetTickCount64())
                       .status == broker::BrokerStatus::kNotFound,
               "revoked session has no pending OTP, claim, replay or key slot");
  if (test_window != nullptr) DestroyWindow(test_window);

  // A server that accepts but never replies must not outlive the one absolute
  // client deadline. BrokerPipeClient cancels the pending ReadFile via
  // CancelIoEx and closes the handle.
  std::thread silent_server([] {
    HANDLE pipe = CreateNamedPipeW(
        broker::BrokerPipeNameForCurrentSession().c_str(),
        PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED |
            FILE_FLAG_FIRST_PIPE_INSTANCE,
        PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT |
            PIPE_REJECT_REMOTE_CLIENTS,
        1, 512, 512, 0, nullptr);
    if (pipe == INVALID_HANDLE_VALUE) return;
    OVERLAPPED overlapped{};
    HANDLE event = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    overlapped.hEvent = event;
    const BOOL connected = ConnectNamedPipe(pipe, &overlapped);
    if (!connected && GetLastError() == ERROR_IO_PENDING) {
      WaitForSingleObject(event, 2'000);
    }
    Sleep(300);
    CancelIoEx(pipe, nullptr);
    CloseHandle(event);
    DisconnectNamedPipe(pipe);
    CloseHandle(pipe);
  });
  broker::BrokerPipeClient timeout_client;
  const ULONGLONG started = GetTickCount64();
  const ULONGLONG timeout_deadline = started + 75;
  broker::BrokerResponse ignored;
  const bool unexpectedly_succeeded =
      timeout_client.ConnectUntil(timeout_deadline) &&
      timeout_client.ExchangeUntil(broker::EncodeOffer(VectorEnvelope(session)),
                                   &ignored, timeout_deadline);
  const ULONGLONG elapsed = GetTickCount64() - started;
  timeout_client.Close();
  silent_server.join();
  ok &= Expect(!unexpectedly_succeeded && elapsed < 250,
               "bounded CancelIoEx read deadline");

  CredDeleteW(credential_target.c_str(), CRED_TYPE_GENERIC, 0);
  return ok ? 0 : 1;
}
