#include "otp_broker_client.h"

#include "broker_protocol.h"

#include <algorithm>

namespace clipvault::ime {
namespace {

clipvault::otp::broker::ContextBinding ToBrokerContext(
    const OtpContextBinding& input) {
  return clipvault::otp::broker::ContextBinding{
      .process_id = input.process_id,
      .thread_id = input.thread_id,
      .window_handle = input.window_handle,
      .document_token = input.document_token,
      .context_token = input.context_token,
  };
}

void Wipe(std::vector<std::uint8_t>* bytes) noexcept {
  if (bytes != nullptr && !bytes->empty())
    SecureZeroMemory(bytes->data(), bytes->size());
}

}  // namespace

bool OtpBrokerInsertClient::ConsumeLatest(
    const OtpContextBinding& context, std::wstring* commit_text,
    DWORD budget_milliseconds) noexcept {
  using namespace clipvault::otp::broker;
  if (commit_text == nullptr || budget_milliseconds == 0 ||
      context.process_id == 0 || context.thread_id == 0 ||
      context.window_handle == 0) {
    return false;
  }
  commit_text->clear();
  const ULONGLONG deadline = GetTickCount64() + budget_milliseconds;
  const auto binding = ToBrokerContext(context);
  BrokerPipeClient arm_client;
  BrokerResponse armed;
  if (!arm_client.ConnectUntil(deadline) ||
      !arm_client.ExchangeUntil(EncodeArmLatest(binding), &armed, deadline) ||
      armed.status != BrokerStatus::kAccepted) {
    Wipe(&armed.secret);
    return false;
  }
  arm_client.Close();

  ConsumeRequest consume{armed.claim_id, binding};
  BrokerPipeClient consume_client;
  BrokerResponse consumed;
  const bool exchanged = consume_client.ConnectUntil(deadline) &&
                         consume_client.ExchangeUntil(
                             EncodeConsume(consume), &consumed, deadline);
  if (!exchanged || consumed.status != BrokerStatus::kConsumed ||
      consumed.secret.size() < 4 || consumed.secret.size() > 8 ||
      !std::all_of(consumed.secret.begin(), consumed.secret.end(),
                   [](std::uint8_t value) {
                     return value >= '0' && value <= '9';
                   })) {
    Wipe(&consumed.secret);
    return false;
  }
  commit_text->reserve(consumed.secret.size());
  for (const auto value : consumed.secret)
    commit_text->push_back(static_cast<wchar_t>(value));
  Wipe(&consumed.secret);
  return true;
}

}  // namespace clipvault::ime
