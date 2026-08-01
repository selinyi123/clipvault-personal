#pragma once

#include <windows.h>

#include <array>
#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <mutex>
#include <span>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

namespace clipvault::ime {

enum class ReplayLookup : std::uint8_t {
  kMissing,
  kExact,
  kConflict,
};

// Host-wide bounded replay protection. Request bodies are reduced immediately
// to a per-process keyed 128-bit fingerprint, so raw key/preedit text is never
// retained for duplicate detection. Content-bearing responses are wiped on an
// authenticated acknowledgement, retry deadline, session end, or destruction.
class ReplayLedger final {
 public:
  static constexpr std::size_t kDefaultMaximumResponses = 64;
  static constexpr std::size_t kDefaultMaximumTombstones = 256;
  static constexpr std::size_t kDefaultMaximumResponseBytes = 256 * 1024;
  static constexpr std::size_t kDefaultMaximumTotalResponseBytes = 4 * 1024 * 1024;
  static constexpr DWORD kDefaultRetryDeadlineMilliseconds = 2000;
  static constexpr DWORD kDefaultTombstoneLifetimeMilliseconds = 30000;

  explicit ReplayLedger(
      std::size_t maximum_responses = kDefaultMaximumResponses,
      std::size_t maximum_tombstones = kDefaultMaximumTombstones,
      std::size_t maximum_response_bytes = kDefaultMaximumResponseBytes,
      std::size_t maximum_total_response_bytes =
          kDefaultMaximumTotalResponseBytes,
      DWORD retry_deadline_milliseconds = kDefaultRetryDeadlineMilliseconds,
      DWORD tombstone_lifetime_milliseconds =
          kDefaultTombstoneLifetimeMilliseconds);
  ~ReplayLedger();
  ReplayLedger(const ReplayLedger&) = delete;
  ReplayLedger& operator=(const ReplayLedger&) = delete;

  bool CacheResponse(const std::string& session_id, std::uint64_t request_seq,
                     std::span<const std::uint8_t> request,
                     std::span<const std::uint8_t> response);
  ReplayLookup LookupResponse(const std::string& session_id,
                              std::uint64_t request_seq,
                              std::span<const std::uint8_t> request,
                              std::vector<std::uint8_t>* response);
  bool Acknowledge(const std::string& session_id,
                   std::uint64_t ack_request_seq) noexcept;
  void ClearSession(const std::string& session_id) noexcept;

  bool RememberEnded(const std::string& session_id,
                     std::uint64_t request_seq,
                     std::span<const std::uint8_t> request);
  ReplayLookup LookupEnded(const std::string& session_id,
                           std::uint64_t request_seq,
                           std::span<const std::uint8_t> request);
  bool IsTombstoned(const std::string& session_id) noexcept;
  bool IsEndedSequence(const std::string& session_id,
                       std::uint64_t request_seq) noexcept;

  [[nodiscard]] std::size_t response_count() const noexcept;
  [[nodiscard]] std::size_t tombstone_count() const noexcept;
  [[nodiscard]] std::size_t retained_response_bytes() const noexcept;

 private:
  struct Fingerprint final {
    std::array<std::uint64_t, 2> words{};
    bool operator==(const Fingerprint&) const = default;
  };
  struct ResponseEntry final {
    std::uint64_t request_seq = 0;
    Fingerprint fingerprint;
    std::vector<std::uint8_t> response;
    ULONGLONG expires_at = 0;
  };
  struct Tombstone final {
    std::uint64_t request_seq = 0;
    Fingerprint fingerprint;
    ULONGLONG expires_at = 0;
  };

  Fingerprint FingerprintRequest(
      std::span<const std::uint8_t> request) const noexcept;
  void PruneLocked(ULONGLONG now) noexcept;
  void WipeResponseLocked(
      std::unordered_map<std::string, ResponseEntry>::iterator entry) noexcept;
  void Reaper(std::stop_token stop_token) noexcept;

  const std::size_t maximum_responses_;
  const std::size_t maximum_tombstones_;
  const std::size_t maximum_response_bytes_;
  const std::size_t maximum_total_response_bytes_;
  const DWORD retry_deadline_milliseconds_;
  const DWORD tombstone_lifetime_milliseconds_;
  std::array<std::uint64_t, 2> fingerprint_key_{};
  mutable std::mutex mutex_;
  std::condition_variable wake_reaper_;
  std::unordered_map<std::string, ResponseEntry> responses_;
  std::unordered_map<std::string, Tombstone> tombstones_;
  std::size_t retained_response_bytes_ = 0;
  std::jthread reaper_;
};

}  // namespace clipvault::ime
