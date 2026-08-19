#include "diagnostics.h"

#include <windows.h>

#include <vector>

namespace {

void Capture(const clipvault::ime::DiagnosticRecord& record,
             void* context) noexcept {
  static_cast<std::vector<clipvault::ime::DiagnosticRecord>*>(context)
      ->push_back(record);
}

}  // namespace

int main() {
  using clipvault::ime::ClassifyKeyForDiagnostics;
  using clipvault::ime::DiagnosticEvent;
  using clipvault::ime::DiagnosticKeyClass;

  if (ClassifyKeyForDiagnostics('N', false) !=
          DiagnosticKeyClass::kLatinLetter ||
      ClassifyKeyForDiagnostics(VK_PACKET, false) !=
          DiagnosticKeyClass::kUnicodePacket ||
      ClassifyKeyForDiagnostics(VK_BACK, false) !=
          DiagnosticKeyClass::kUnsupported ||
      ClassifyKeyForDiagnostics(VK_BACK, true) !=
          DiagnosticKeyClass::kCompositionControl ||
      ClassifyKeyForDiagnostics('1', true) !=
          DiagnosticKeyClass::kCandidateNumber) {
    return 1;
  }

  std::vector<clipvault::ime::DiagnosticRecord> records;
  clipvault::ime::SetDiagnosticCallbackForTesting(&Capture, &records);
  clipvault::ime::EmitDiagnostic(DiagnosticEvent::kTestKeyObserved,
                                 static_cast<std::uint32_t>(
                                     DiagnosticKeyClass::kUnicodePacket),
                                 17);
  clipvault::ime::SetDiagnosticCallbackForTesting(nullptr, nullptr);
  return records.size() == 1 &&
                 records.front().event == DiagnosticEvent::kTestKeyObserved &&
                 records.front().detail == static_cast<std::uint32_t>(
                                               DiagnosticKeyClass::kUnicodePacket) &&
                 records.front().duration_milliseconds == 17
             ? 0
             : 1;
}
