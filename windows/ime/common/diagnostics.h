#pragma once

#include <cstdint>

namespace clipvault::ime {

// Diagnostics are deliberately content-free. Never add key text, candidate text,
// application names, window titles, document contents, or identifiers derived
// from user input to this channel.
enum class DiagnosticEvent : std::uint32_t {
  kTextServiceActivate = 100,
  kKeySinkAdvised = 101,
  kKeySinkAdviseFailed = 102,
  kHostLaunchSucceeded = 103,
  kHostLaunchFailed = 104,
  kTestKeyObserved = 110,
  kTestKeyUnsupported = 111,
  kTestKeySensitive = 112,
  kTestKeyEngineUnavailable = 113,
  kKeyRpcTimedOut = 114,
  kKeyStateApplied = 115,
  kTextServiceDeactivate = 116,
  kLocalBufferUpdated = 117,
  kLocalBufferReplayed = 118,
  kKeyDownObserved = 119,
  kEditSessionRequestFailed = 120,
  kEditSessionApplyFailed = 121,
  kCandidateWindowUnavailable = 122,
  kInsertInterfaceFailed = 123,
  kInsertionRangeFailed = 124,
  kCompositionInterfaceFailed = 125,
  kCompositionStartFailed = 126,
  kCompositionRangeFailed = 127,
  kCompositionTextFailed = 128,
  kCompositionCaretFailed = 129,
  kCompositionSelectionFailed = 130,
  kRimeInitializeStarted = 200,
  kRimeInitializeReady = 201,
  kRimeInitializeUnavailable = 202,
  kRimeDeployStarted = 203,
  kRimeDeployFinished = 204,
};

enum class DiagnosticKeyClass : std::uint32_t {
  kUnsupported = 0,
  kLatinLetter = 1,
  kPunctuation = 2,
  kCompositionControl = 3,
  kCandidateNumber = 4,
  kUnicodePacket = 5,
};

struct DiagnosticRecord final {
  DiagnosticEvent event = DiagnosticEvent::kTextServiceActivate;
  std::uint32_t detail = 0;
  std::uint32_t duration_milliseconds = 0;
};

using DiagnosticCallback = void (*)(const DiagnosticRecord&, void*) noexcept;

void SetDiagnosticCallbackForTesting(DiagnosticCallback callback,
                                     void* context) noexcept;
void EmitDiagnostic(DiagnosticEvent event, std::uint32_t detail = 0,
                    std::uint32_t duration_milliseconds = 0) noexcept;

DiagnosticKeyClass ClassifyKeyForDiagnostics(std::uintptr_t key,
                                             bool composition_active) noexcept;

}  // namespace clipvault::ime
