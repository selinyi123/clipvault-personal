#pragma once

#include "protocol.h"

#include <windows.h>

#include <cstdint>
#include <functional>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace clipvault::ime {

inline constexpr std::uint32_t kRuntimeSnapshotProtocolVersion = 1;
inline constexpr std::uint32_t kRuntimeSnapshotMaximumItems = 8;
inline constexpr std::uint32_t kRuntimeSnapshotMaximumFrameBytes = 65'536;
inline constexpr DWORD kRuntimeSnapshotDeadlineMilliseconds = 250;
inline constexpr std::uint64_t kRuntimeSnapshotMaximumLifetimeMilliseconds =
    30'000;

struct RuntimeSnapshotResponse final {
  std::uint64_t request_id = 0;
  SnapshotSurface surface;
};

struct RuntimeSnapshotFetchOptions final {
  std::wstring pipe_name;
  std::wstring expected_server_path;
  bool require_trusted_signature = true;
};

std::wstring RuntimeSnapshotPipeNameForCurrentSession();
std::wstring ExpectedRuntimeExecutable(const std::wstring& host_directory);
std::uint64_t UnixTimeMilliseconds() noexcept;

std::vector<std::uint8_t> EncodeRuntimeSnapshotClientHello(
    const std::string& client_instance);
std::vector<std::uint8_t> EncodeRuntimeSnapshotHostHello(
    const std::string& publisher_epoch);
std::vector<std::uint8_t> EncodeRuntimeSnapshotRequest(
    std::uint64_t request_id, std::uint32_t limit);
std::vector<std::uint8_t> EncodeRuntimeSnapshotResponse(
    const RuntimeSnapshotResponse& response);
bool DecodeRuntimeSnapshotClientHello(const std::vector<std::uint8_t>& payload,
                                      std::string* client_instance);
bool DecodeRuntimeSnapshotHostHello(const std::vector<std::uint8_t>& payload,
                                    std::string* publisher_epoch);
bool DecodeRuntimeSnapshotRequest(const std::vector<std::uint8_t>& payload,
                                  std::uint64_t* request_id,
                                  std::uint32_t* limit);
bool DecodeRuntimeSnapshotResponse(const std::vector<std::uint8_t>& payload,
                                   std::uint64_t now_ms,
                                   RuntimeSnapshotResponse* response);

bool ReadRuntimeSnapshotFrameUntil(HANDLE pipe,
                                   std::vector<std::uint8_t>* payload,
                                   ULONGLONG deadline_tick);
bool WriteRuntimeSnapshotFrameUntil(HANDLE pipe,
                                    const std::vector<std::uint8_t>& payload,
                                    ULONGLONG deadline_tick);

class RuntimeSnapshotPipeClient final {
 public:
  explicit RuntimeSnapshotPipeClient(RuntimeSnapshotFetchOptions options);

  bool Fetch(std::uint64_t request_id, std::uint32_t limit,
             std::uint64_t now_ms, RuntimeSnapshotResponse* response) const;

 private:
  RuntimeSnapshotFetchOptions options_;
};

class RuntimeSnapshotCoordinator final {
 public:
  using Fetcher = std::function<bool(std::uint64_t, std::uint32_t,
                                     std::uint64_t,
                                     RuntimeSnapshotResponse*)>;
  struct SessionHandle;

  explicit RuntimeSnapshotCoordinator(Fetcher fetcher);
  RuntimeSnapshotCoordinator(const RuntimeSnapshotCoordinator&) = delete;
  RuntimeSnapshotCoordinator& operator=(const RuntimeSnapshotCoordinator&) =
      delete;

  std::shared_ptr<SessionHandle> BeginSession(bool clipvault_allowed);
  SnapshotSurface Current(const std::shared_ptr<SessionHandle>& session) const;
  std::optional<std::wstring> Consume(
      const std::shared_ptr<SessionHandle>& session,
      const std::string& publisher_epoch, std::uint64_t generation,
      const std::string& candidate_id);
  void Invalidate(const std::shared_ptr<SessionHandle>& session) noexcept;

 private:
  struct SharedState;
  std::shared_ptr<SharedState> state_;
};

}  // namespace clipvault::ime
