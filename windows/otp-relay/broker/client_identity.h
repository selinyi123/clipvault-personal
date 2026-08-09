#pragma once

#include <windows.h>

#include <string>

namespace clipvault::otp::broker {

enum class BrokerClientRole {
  kOpaqueDesktopOffer,
  kDesktopControl,
  kImeHostControl,
};

class BrokerClientAuthorizer {
 public:
  virtual ~BrokerClientAuthorizer() = default;
  virtual bool Authorize(DWORD process_id, BrokerClientRole role) noexcept = 0;
};

// Production policy: the pipe peer must run as the broker user, from the
// fixed combined-install path, and share one trusted publisher with the
// Broker binary. There is no production path/env override.
class ProductionBrokerClientAuthorizer final : public BrokerClientAuthorizer {
 public:
  ProductionBrokerClientAuthorizer();
  bool Authorize(DWORD process_id, BrokerClientRole role) noexcept override;

 private:
  std::wstring broker_path_;
  std::wstring desktop_path_;
  std::wstring host_path_;
};

}  // namespace clipvault::otp::broker
