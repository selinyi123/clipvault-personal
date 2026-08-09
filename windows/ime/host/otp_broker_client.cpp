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

void Wipe(clipvault::otp::crypto::UuidBytes* bytes) noexcept {
  if (bytes != nullptr) SecureZeroMemory(bytes->data(), bytes->size());
}

void Wipe(std::wstring* text) noexcept {
  if (text != nullptr && !text->empty()) {
    SecureZeroMemory(text->data(), text->size() * sizeof(wchar_t));
    text->clear();
  }
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
  Wipe(commit_text);
  struct OutputGuard final {
    std::wstring* value;
    bool keep = false;
    ~OutputGuard() {
      if (!keep) Wipe(value);
    }
  } output_guard{commit_text};
  try {
    const ULONGLONG deadline = GetTickCount64() + budget_milliseconds;
    const auto binding = ToBrokerContext(context);
    BrokerPipeClient arm_client;
    BrokerResponse armed;
    struct ResponseGuard final {
      BrokerResponse* response;
      ~ResponseGuard() {
        Wipe(&response->claim_id);
        Wipe(&response->secret);
      }
    } armed_guard{&armed};
    if (!arm_client.ConnectUntil(deadline) ||
        !arm_client.ExchangeUntil(EncodeArmLatest(binding), &armed, deadline) ||
        armed.status != BrokerStatus::kAccepted) {
      return false;
    }
    arm_client.Close();

    ConsumeRequest consume{armed.claim_id, binding};
    struct ConsumeGuard final {
      ConsumeRequest* request;
      ~ConsumeGuard() { Wipe(&request->claim_id); }
    } consume_guard{&consume};
    BrokerPipeClient consume_client;
    BrokerResponse consumed;
    ResponseGuard consumed_guard{&consumed};
    const bool exchanged = consume_client.ConnectUntil(deadline) &&
                           consume_client.ExchangeUntil(
                               EncodeConsume(consume), &consumed, deadline);
    if (!exchanged || consumed.status != BrokerStatus::kConsumed ||
        consumed.secret.size() < 4 || consumed.secret.size() > 8 ||
        !std::all_of(consumed.secret.begin(), consumed.secret.end(),
                     [](std::uint8_t value) {
                       return value >= '0' && value <= '9';
                     })) {
      return false;
    }
    commit_text->reserve(consumed.secret.size());
    for (const auto value : consumed.secret)
      commit_text->push_back(static_cast<wchar_t>(value));
    output_guard.keep = true;
    return true;
  } catch (...) {
    return false;
  }
}

}  // namespace clipvault::ime
