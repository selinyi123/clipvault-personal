#pragma once

#include "client_identity.h"
#include "otp_broker_service.h"
#include "otp_prompt.h"

#include <windows.h>

namespace clipvault::otp::broker {

// External-broker endpoint. It owns CNG/session/store state and exposes only a
// per-user local pipe. The TSF DLL never links this class or the crypto target.
class BrokerPipeServer final {
 public:
  BrokerPipeServer(OtpBrokerService* service,
                   BrokerClientAuthorizer* authorizer,
                   OtpPromptSink* prompt = nullptr)
      : service_(service), authorizer_(authorizer), prompt_(prompt) {}
  BrokerPipeServer(const BrokerPipeServer&) = delete;
  BrokerPipeServer& operator=(const BrokerPipeServer&) = delete;

  bool ServeOne(DWORD accept_timeout_ms = 5'000,
                DWORD request_budget_ms = kBrokerForwardBudgetMilliseconds);

 private:
  OtpBrokerService* service_ = nullptr;
  BrokerClientAuthorizer* authorizer_ = nullptr;
  OtpPromptSink* prompt_ = nullptr;
};

}  // namespace clipvault::otp::broker
