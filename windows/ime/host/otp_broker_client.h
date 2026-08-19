#pragma once

#include "protocol.h"

#include <string>

namespace clipvault::ime {

// Host-only bounded client. It receives one mutable lease from the independent
// native broker, converts it immediately for the response edit session, and
// wipes the broker response buffer. It never logs, persists, or retries OTPs.
class OtpBrokerInsertClient final {
 public:
  bool ConsumeLatest(const OtpContextBinding& context,
                     std::wstring* commit_text,
                     DWORD budget_milliseconds = 35) noexcept;
};

}  // namespace clipvault::ime
