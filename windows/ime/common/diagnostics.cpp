#include "diagnostics.h"

#include <windows.h>

#include <array>
#include <atomic>
#include <cstdio>

namespace clipvault::ime {
namespace {

std::atomic<DiagnosticCallback> g_callback{nullptr};
std::atomic<void*> g_callback_context{nullptr};

}  // namespace

void SetDiagnosticCallbackForTesting(DiagnosticCallback callback,
                                     void* context) noexcept {
  g_callback_context.store(context, std::memory_order_release);
  g_callback.store(callback, std::memory_order_release);
}

void EmitDiagnostic(DiagnosticEvent event, std::uint32_t detail,
                    std::uint32_t duration_milliseconds) noexcept {
  const DiagnosticRecord record{event, detail, duration_milliseconds};
  if (const auto callback = g_callback.load(std::memory_order_acquire);
      callback != nullptr) {
    callback(record, g_callback_context.load(std::memory_order_acquire));
  }

  std::array<wchar_t, 160> message{};
  const int length = swprintf_s(
      message.data(), message.size(),
      L"ClipVaultIme event=%lu detail=%lu duration_ms=%lu\n",
      static_cast<unsigned long>(event), static_cast<unsigned long>(detail),
      static_cast<unsigned long>(duration_milliseconds));
  if (length > 0) OutputDebugStringW(message.data());
}

DiagnosticKeyClass ClassifyKeyForDiagnostics(
    std::uintptr_t key, bool composition_active) noexcept {
  if (key == VK_PACKET) return DiagnosticKeyClass::kUnicodePacket;
  if (key >= 'A' && key <= 'Z') return DiagnosticKeyClass::kLatinLetter;
  if (key == VK_OEM_1 || key == VK_OEM_PLUS || key == VK_OEM_COMMA ||
      key == VK_OEM_MINUS || key == VK_OEM_PERIOD || key == VK_OEM_2 ||
      key == VK_OEM_3 || key == VK_OEM_4 || key == VK_OEM_5 ||
      key == VK_OEM_6 || key == VK_OEM_7) {
    return DiagnosticKeyClass::kPunctuation;
  }
  if (composition_active && key >= '1' && key <= '9')
    return DiagnosticKeyClass::kCandidateNumber;
  if (composition_active &&
      (key == VK_PRIOR || key == VK_NEXT || key == VK_BACK || key == VK_SPACE ||
       key == VK_RETURN || key == VK_ESCAPE)) {
    return DiagnosticKeyClass::kCompositionControl;
  }
  return DiagnosticKeyClass::kUnsupported;
}

}  // namespace clipvault::ime
