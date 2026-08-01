#include "replay_ledger.h"

#include <windows.h>

#include <cstdint>
#include <iostream>
#include <string>
#include <vector>

namespace {

bool Expect(bool condition, const char* label) {
  if (!condition) std::cerr << "FAILED: " << label << '\n';
  return condition;
}

std::vector<std::uint8_t> Bytes(const std::string& value) {
  return {value.begin(), value.end()};
}

}  // namespace

int main() {
  using namespace clipvault::ime;
  bool ok = true;
  ReplayLedger ledger(/*maximum_responses=*/2, /*maximum_tombstones=*/2,
                      /*maximum_response_bytes=*/64,
                      /*maximum_total_response_bytes=*/80,
                      /*retry_deadline_milliseconds=*/40,
                      /*tombstone_lifetime_milliseconds=*/200);

  const auto request = Bytes("frame-with-super-secret-raw-key-text");
  const auto conflicting = Bytes("frame-with-different-key-text");
  const auto response = Bytes("encoded-engine-state-with-commit");
  ok &= Expect(ledger.CacheResponse("session-a", 4, request, response),
               "ENG2-V003 cache exact transition");
  std::vector<std::uint8_t> recovered;
  ok &= Expect(ledger.LookupResponse("session-a", 4, request, &recovered) ==
                       ReplayLookup::kExact &&
                   recovered == response,
               "ENG2-V003 duplicate request receives byte-identical response");
  ok &= Expect(ledger.LookupResponse("session-a", 4, conflicting, &recovered) ==
                   ReplayLookup::kConflict,
               "ENG2-V003 conflicting duplicate fails closed");
  ok &= Expect(ledger.Acknowledge("session-a", 4) &&
                   ledger.response_count() == 0 &&
                   ledger.retained_response_bytes() == 0,
               "ENG2-V008 authenticated acknowledgement wipes response cache");

  ok &= Expect(ledger.CacheResponse("session-timeout", 1, request, response),
               "cache response for deadline cleanup");
  const ULONGLONG deadline = GetTickCount64() + 1000;
  while (ledger.response_count() != 0 && GetTickCount64() < deadline) Sleep(5);
  ok &= Expect(ledger.response_count() == 0 &&
                   ledger.retained_response_bytes() == 0,
               "ENG2-V008 retry deadline wipes unacknowledged response");

  const auto small_response = Bytes("01234567890123456789");
  ok &= Expect(ledger.CacheResponse("session-1", 1, request, small_response) &&
                   ledger.CacheResponse("session-2", 1, request,
                                        small_response) &&
                   ledger.CacheResponse("session-3", 1, request,
                                        small_response) &&
                   ledger.response_count() <= 2 &&
                   ledger.retained_response_bytes() <= 80,
               "ENG2-V003 response state remains bounded");
  const std::vector<std::uint8_t> oversized(65, 0x41);
  ok &= Expect(!ledger.CacheResponse("session-too-large", 1, request,
                                    oversized),
               "oversized cached response rejected");

  ok &= Expect(ledger.RememberEnded("ended-1", 8, request) &&
                   ledger.RememberEnded("ended-2", 9, request) &&
                   ledger.RememberEnded("ended-3", 10, request) &&
                   ledger.tombstone_count() <= 2,
               "ENG2-V008 content-free tombstones remain bounded");
  ok &= Expect(ledger.LookupEnded("ended-3", 10, request) ==
                       ReplayLookup::kExact &&
                   ledger.LookupEnded("ended-3", 10, conflicting) ==
                       ReplayLookup::kConflict,
               "ENG2-V008 duplicate EndSession fingerprint is idempotent");
  ok &= Expect(ledger.response_count() <= 2 &&
                   ledger.retained_response_bytes() <= 80,
               "request fingerprints retain no request body allocation");
  return ok ? 0 : 1;
}
