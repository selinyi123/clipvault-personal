#include "runtime_snapshot.h"

#include <windows.h>

#include <algorithm>
#include <atomic>
#include <iostream>
#include <string>

namespace {

using namespace clipvault::ime;

constexpr char kEpochA[] = "01234567-89ab-4def-8abc-0123456789ab";
constexpr char kEpochB[] = "fedcba98-7654-4321-9abc-fedcba987654";

bool Expect(bool condition, const char* label) {
  if (!condition) std::cerr << "FAILED: " << label << '\n';
  return condition;
}

RuntimeSnapshotResponse MakeResponse(std::uint64_t request_id,
                                     const std::string& epoch,
                                     std::uint64_t generation,
                                     const std::string& candidate_id = "item-1",
                                     const std::wstring& text = L"value") {
  RuntimeSnapshotResponse response;
  response.request_id = request_id;
  response.surface.publisher_epoch = epoch;
  response.surface.generation = generation;
  response.surface.expires_at_ms = UnixTimeMilliseconds() + 10'000;
  response.surface.candidates.push_back(
      {candidate_id, 1, L"Memory", text});
  return response;
}

bool WaitForSurface(RuntimeSnapshotCoordinator* coordinator,
                    const std::shared_ptr<RuntimeSnapshotCoordinator::SessionHandle>&
                        session,
                    bool expect_nonempty) {
  const ULONGLONG deadline = GetTickCount64() + 2000;
  while (GetTickCount64() < deadline) {
    const bool nonempty = !coordinator->Current(session).empty();
    if (nonempty == expect_nonempty) return true;
    Sleep(1);
  }
  return false;
}

bool ProtocolAndBounds() {
  bool ok = true;
  const auto now = UnixTimeMilliseconds();
  RuntimeSnapshotResponse response = MakeResponse(7, kEpochA, 1);
  response.surface.expires_at_ms = now + 10'000;
  const auto encoded = EncodeRuntimeSnapshotResponse(response);
  RuntimeSnapshotResponse decoded;
  ok &= Expect(!encoded.empty() &&
                   DecodeRuntimeSnapshotResponse(encoded, now, &decoded) &&
                   decoded.request_id == 7 &&
                   decoded.surface.publisher_epoch == kEpochA &&
                   decoded.surface.generation == 1 &&
                   decoded.surface.candidates.size() == 1 &&
                   decoded.surface.candidates.front().text == L"value",
               "SNAP-V001 bounded response accepted");

  RuntimeSnapshotResponse nine = response;
  for (int index = 1; index < 9; ++index) {
    nine.surface.candidates.push_back(
        {"item-" + std::to_string(index + 1), 2, L"Clipboard", L"value"});
  }
  ok &= Expect(EncodeRuntimeSnapshotResponse(nine).empty(),
               "SNAP-V005 item count rejected");
  RuntimeSnapshotResponse long_id = response;
  long_id.surface.candidates.front().candidate_id.assign(129, 'a');
  ok &= Expect(EncodeRuntimeSnapshotResponse(long_id).empty(),
               "SNAP-V005 candidate ID bound");
  RuntimeSnapshotResponse long_label = response;
  long_label.surface.candidates.front().label.assign(65, L'a');
  ok &= Expect(EncodeRuntimeSnapshotResponse(long_label).empty(),
               "SNAP-V005 label bound");
  RuntimeSnapshotResponse long_text = response;
  long_text.surface.candidates.front().text.assign(16'385, L'a');
  ok &= Expect(EncodeRuntimeSnapshotResponse(long_text).empty(),
               "SNAP-V005 text bound");
  std::vector<std::uint8_t> aggregate(65'537, 0);
  ok &= Expect(!DecodeRuntimeSnapshotResponse(aggregate, now, &decoded),
               "SNAP-V005 aggregate bound");

  auto duplicate_field = encoded;
  duplicate_field.push_back(0x08);
  duplicate_field.push_back(0x07);
  ok &= Expect(!DecodeRuntimeSnapshotResponse(duplicate_field, now, &decoded),
               "SNAP-V006 duplicate singleton rejected");
  auto unknown_field = encoded;
  unknown_field.push_back(0x30);
  unknown_field.push_back(0x01);
  ok &= Expect(!DecodeRuntimeSnapshotResponse(unknown_field, now, &decoded),
               "SNAP-V006 unknown field rejected");
  auto invalid_utf8 = encoded;
  const std::string needle = "item-1";
  const auto found = std::search(invalid_utf8.begin(), invalid_utf8.end(),
                                 needle.begin(), needle.end());
  if (found != invalid_utf8.end()) *found = 0xff;
  ok &= Expect(found != invalid_utf8.end() &&
                   !DecodeRuntimeSnapshotResponse(invalid_utf8, now, &decoded),
               "SNAP-V006 invalid UTF-8 rejected");
  RuntimeSnapshotResponse duplicate_id = response;
  duplicate_id.surface.candidates.push_back(
      duplicate_id.surface.candidates.front());
  ok &= Expect(EncodeRuntimeSnapshotResponse(duplicate_id).empty(),
               "SNAP-V006 duplicate candidate ID rejected");
  return ok;
}

bool SensitiveAndLate() {
  bool ok = true;
  std::atomic_int calls{0};
  RuntimeSnapshotCoordinator private_coordinator(
      [&calls](std::uint64_t request_id, std::uint32_t, std::uint64_t,
               RuntimeSnapshotResponse* response) {
        ++calls;
        *response = MakeResponse(request_id, kEpochA, 1);
        return true;
      });
  const auto private_session = private_coordinator.BeginSession(false);
  Sleep(20);
  ok &= Expect(private_coordinator.Current(private_session).empty() &&
                   calls.load() == 0,
               "SNAP-V002 sensitive context remains empty");

  std::atomic_bool release{false};
  std::atomic_bool finished{false};
  RuntimeSnapshotCoordinator late_coordinator(
      [&release, &finished](std::uint64_t request_id, std::uint32_t,
                           std::uint64_t,
                           RuntimeSnapshotResponse* response) {
        while (!release.load()) Sleep(1);
        *response = MakeResponse(request_id, kEpochA, 1);
        finished.store(true);
        return true;
      });
  const auto late = late_coordinator.BeginSession(true);
  late_coordinator.Invalidate(late);
  release.store(true);
  const ULONGLONG deadline = GetTickCount64() + 1000;
  while (!finished.load() && GetTickCount64() < deadline) Sleep(1);
  Sleep(10);
  ok &= Expect(finished.load() && late_coordinator.Current(late).empty(),
               "SNAP-V003 late response discarded after invalidation");
  return ok;
}

bool EpochRollbackAndSelection() {
  bool ok = true;
  std::atomic_int call{0};
  RuntimeSnapshotCoordinator coordinator(
      [&call](std::uint64_t request_id, std::uint32_t, std::uint64_t,
              RuntimeSnapshotResponse* response) {
        const int current = ++call;
        if (current == 1)
          *response = MakeResponse(request_id, kEpochA, 1, "old", L"old");
        else if (current == 2)
          *response = MakeResponse(request_id, kEpochB, 1, "new", L"new");
        else
          *response = MakeResponse(request_id, kEpochA, 2, "replayed",
                                   L"replayed");
        return true;
      });
  const auto old_session = coordinator.BeginSession(true);
  ok &= Expect(WaitForSurface(&coordinator, old_session, true),
               "SNAP-V004 first epoch accepted");
  const auto new_session = coordinator.BeginSession(true);
  ok &= Expect(WaitForSurface(&coordinator, new_session, true) &&
                   WaitForSurface(&coordinator, old_session, false),
               "SNAP-V004 epoch change wipes old IDs");
  const auto replayed_session = coordinator.BeginSession(true);
  Sleep(50);
  ok &= Expect(coordinator.Current(replayed_session).empty(),
               "SNAP-V006 retired publisher epoch rejected");

  const auto visible = coordinator.Current(new_session);
  const auto selected = coordinator.Consume(
      new_session, visible.publisher_epoch, visible.generation, "new");
  const auto selected_again = coordinator.Consume(
      new_session, visible.publisher_epoch, visible.generation, "new");
  ok &= Expect(selected == L"new" && !selected_again.has_value() &&
                   coordinator.Current(new_session).empty(),
               "SNAP-V008 local selection consumes exactly once");
  return ok;
}

bool RuntimeFailureDoesNotAffectEngineProtocol() {
  RuntimeSnapshotCoordinator coordinator(
      [](std::uint64_t, std::uint32_t, std::uint64_t,
         RuntimeSnapshotResponse*) { return false; });
  const auto session = coordinator.BeginSession(true);
  Sleep(20);
  EngineState engine;
  engine.host_instance_id = "host";
  engine.session_id = "session";
  engine.ack_request_seq = 1;
  engine.handled = true;
  engine.preedit = L"ni";
  engine.caret_utf16 = 2;
  engine.composition_active = true;
  EngineState decoded;
  return Expect(coordinator.Current(session).empty() &&
                    DecodeEngineState(EncodeEngineState(engine), &decoded) &&
                    decoded.preedit == L"ni",
                "SNAP-V007 Runtime failure leaves engine operational");
}

bool RefreshAndConcurrentSessions() {
  bool ok = true;
  std::atomic_int refresh_calls{0};
  std::atomic_bool release_refresh{false};
  RuntimeSnapshotCoordinator refresh_coordinator(
      [&refresh_calls, &release_refresh](std::uint64_t request_id,
                                        std::uint32_t, std::uint64_t now_ms,
                                        RuntimeSnapshotResponse* response) {
        const int current = ++refresh_calls;
        if (current > 1) {
          while (!release_refresh.load()) Sleep(1);
        }
        *response = MakeResponse(request_id, kEpochA,
                                 static_cast<std::uint64_t>(current));
        response->surface.expires_at_ms =
            now_ms + (current == 1 ? 1'000 : 10'000);
        return true;
      });
  const auto refresh_session = refresh_coordinator.BeginSession(true);
  ok &= Expect(WaitForSurface(&refresh_coordinator, refresh_session, true),
               "SNAP-V009 initial surface published");
  Sleep(1'100);
  ok &= Expect(refresh_coordinator.Current(refresh_session).empty(),
               "SNAP-V009 expired surface hidden while refresh starts");
  release_refresh.store(true);
  ok &= Expect(WaitForSurface(&refresh_coordinator, refresh_session, true) &&
                   refresh_calls.load() >= 2 &&
                   refresh_coordinator.Current(refresh_session).generation >= 2,
               "SNAP-V009 expired surface refreshes in-session");

  std::atomic_int concurrent_calls{0};
  std::atomic_bool first_started{false};
  std::atomic_bool release_first{false};
  RuntimeSnapshotCoordinator concurrent_coordinator(
      [&concurrent_calls, &first_started, &release_first](
          std::uint64_t request_id, std::uint32_t, std::uint64_t,
          RuntimeSnapshotResponse* response) {
        const int current = ++concurrent_calls;
        if (current == 1) {
          first_started.store(true);
          while (!release_first.load()) Sleep(1);
        } else {
          release_first.store(true);
        }
        *response = MakeResponse(request_id, kEpochA,
                                 static_cast<std::uint64_t>(current));
        return true;
      });
  const auto first = concurrent_coordinator.BeginSession(true);
  const ULONGLONG start_deadline = GetTickCount64() + 1000;
  while (!first_started.load() && GetTickCount64() < start_deadline) Sleep(1);
  const auto second = concurrent_coordinator.BeginSession(true);
  ok &= Expect(first_started.load() && concurrent_calls.load() == 1,
               "SNAP-V010 snapshot fetches are globally single-flight");
  release_first.store(true);
  ok &= Expect(WaitForSurface(&concurrent_coordinator, first, true),
               "SNAP-V010 first serialized response is accepted");
  const ULONGLONG retry_deadline = GetTickCount64() + 2'000;
  while (concurrent_calls.load() < 2 && GetTickCount64() < retry_deadline) {
    concurrent_coordinator.Current(second);
    Sleep(10);
  }
  ok &= Expect(concurrent_calls.load() == 2 &&
                   WaitForSurface(&concurrent_coordinator, second, true) &&
                   concurrent_coordinator.Current(second).generation == 2,
               "SNAP-V010 waiting session retries after serialized fetch");
  return ok;
}

}  // namespace

int main() {
  return ProtocolAndBounds() && SensitiveAndLate() &&
                 EpochRollbackAndSelection() &&
                 RuntimeFailureDoesNotAffectEngineProtocol() &&
                 RefreshAndConcurrentSessions()
             ? 0
             : 1;
}
