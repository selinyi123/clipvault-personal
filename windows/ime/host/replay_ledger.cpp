#include "replay_ledger.h"

#include <objbase.h>

#include <algorithm>
#include <chrono>
#include <cstring>
#include <limits>

namespace clipvault::ime {
namespace {

constexpr std::uint64_t kSipConstant0 = 0x736f6d6570736575ULL;
constexpr std::uint64_t kSipConstant1 = 0x646f72616e646f6dULL;
constexpr std::uint64_t kSipConstant2 = 0x6c7967656e657261ULL;
constexpr std::uint64_t kSipConstant3 = 0x7465646279746573ULL;

std::uint64_t RotateLeft(std::uint64_t value, unsigned bits) noexcept {
  return (value << bits) | (value >> (64u - bits));
}

void SipRound(std::uint64_t* v0, std::uint64_t* v1, std::uint64_t* v2,
              std::uint64_t* v3) noexcept {
  *v0 += *v1;
  *v1 = RotateLeft(*v1, 13);
  *v1 ^= *v0;
  *v0 = RotateLeft(*v0, 32);
  *v2 += *v3;
  *v3 = RotateLeft(*v3, 16);
  *v3 ^= *v2;
  *v0 += *v3;
  *v3 = RotateLeft(*v3, 21);
  *v3 ^= *v0;
  *v2 += *v1;
  *v1 = RotateLeft(*v1, 17);
  *v1 ^= *v2;
  *v2 = RotateLeft(*v2, 32);
}

std::uint64_t ReadLittleEndian64(const std::uint8_t* input) noexcept {
  std::uint64_t value = 0;
  for (unsigned index = 0; index < 8; ++index)
    value |= static_cast<std::uint64_t>(input[index]) << (index * 8u);
  return value;
}

std::uint64_t SipHash24(std::span<const std::uint8_t> input,
                        std::uint64_t key0, std::uint64_t key1) noexcept {
  std::uint64_t v0 = key0 ^ kSipConstant0;
  std::uint64_t v1 = key1 ^ kSipConstant1;
  std::uint64_t v2 = key0 ^ kSipConstant2;
  std::uint64_t v3 = key1 ^ kSipConstant3;
  std::size_t offset = 0;
  while (input.size() - offset >= 8) {
    const std::uint64_t message = ReadLittleEndian64(input.data() + offset);
    v3 ^= message;
    SipRound(&v0, &v1, &v2, &v3);
    SipRound(&v0, &v1, &v2, &v3);
    v0 ^= message;
    offset += 8;
  }
  std::uint64_t tail = static_cast<std::uint64_t>(input.size()) << 56u;
  for (std::size_t index = 0; index < input.size() - offset; ++index)
    tail |= static_cast<std::uint64_t>(input[offset + index]) << (index * 8u);
  v3 ^= tail;
  SipRound(&v0, &v1, &v2, &v3);
  SipRound(&v0, &v1, &v2, &v3);
  v0 ^= tail;
  v2 ^= 0xffu;
  for (int index = 0; index < 4; ++index) SipRound(&v0, &v1, &v2, &v3);
  return v0 ^ v1 ^ v2 ^ v3;
}

void Wipe(std::vector<std::uint8_t>* bytes) noexcept {
  if (!bytes->empty()) SecureZeroMemory(bytes->data(), bytes->size());
  bytes->clear();
}

}  // namespace

ReplayLedger::ReplayLedger(std::size_t maximum_responses,
                           std::size_t maximum_tombstones,
                           std::size_t maximum_response_bytes,
                           std::size_t maximum_total_response_bytes,
                           DWORD retry_deadline_milliseconds,
                           DWORD tombstone_lifetime_milliseconds)
    : maximum_responses_(std::max<std::size_t>(1, maximum_responses)),
      maximum_tombstones_(std::max<std::size_t>(1, maximum_tombstones)),
      maximum_response_bytes_(std::max<std::size_t>(1, maximum_response_bytes)),
      maximum_total_response_bytes_(
          std::max(maximum_response_bytes_, maximum_total_response_bytes)),
      retry_deadline_milliseconds_(
          std::max<DWORD>(1, retry_deadline_milliseconds)),
      tombstone_lifetime_milliseconds_(
          std::max<DWORD>(1, tombstone_lifetime_milliseconds)) {
  GUID seed{};
  if (FAILED(CoCreateGuid(&seed))) {
    seed.Data1 = GetCurrentProcessId();
    seed.Data2 = static_cast<unsigned short>(GetCurrentThreadId());
    seed.Data3 = static_cast<unsigned short>(GetTickCount64());
    const auto fallback = reinterpret_cast<std::uintptr_t>(this);
    std::memcpy(seed.Data4, &fallback,
                std::min(sizeof(seed.Data4), sizeof(fallback)));
  }
  static_assert(sizeof(seed) == sizeof(fingerprint_key_));
  std::memcpy(fingerprint_key_.data(), &seed, sizeof(seed));
  reaper_ = std::jthread(
      [this](std::stop_token stop_token) { Reaper(stop_token); });
}

ReplayLedger::~ReplayLedger() {
  reaper_.request_stop();
  wake_reaper_.notify_all();
  if (reaper_.joinable()) reaper_.join();
  std::scoped_lock lock(mutex_);
  while (!responses_.empty()) WipeResponseLocked(responses_.begin());
  tombstones_.clear();
  SecureZeroMemory(fingerprint_key_.data(), sizeof(fingerprint_key_));
}

ReplayLedger::Fingerprint ReplayLedger::FingerprintRequest(
    std::span<const std::uint8_t> request) const noexcept {
  Fingerprint result;
  result.words[0] = SipHash24(request, fingerprint_key_[0], fingerprint_key_[1]);
  result.words[1] =
      SipHash24(request, fingerprint_key_[0] ^ 0xa5a5a5a5a5a5a5a5ULL,
                fingerprint_key_[1] ^ 0x5a5a5a5a5a5a5a5aULL);
  return result;
}

void ReplayLedger::WipeResponseLocked(
    std::unordered_map<std::string, ResponseEntry>::iterator entry) noexcept {
  retained_response_bytes_ -= entry->second.response.size();
  Wipe(&entry->second.response);
  responses_.erase(entry);
}

void ReplayLedger::PruneLocked(ULONGLONG now) noexcept {
  for (auto entry = responses_.begin(); entry != responses_.end();) {
    if (entry->second.expires_at <= now) {
      auto expired = entry++;
      WipeResponseLocked(expired);
    } else {
      ++entry;
    }
  }
  for (auto entry = tombstones_.begin(); entry != tombstones_.end();) {
    if (entry->second.expires_at <= now)
      entry = tombstones_.erase(entry);
    else
      ++entry;
  }
}

bool ReplayLedger::CacheResponse(
    const std::string& session_id, std::uint64_t request_seq,
    std::span<const std::uint8_t> request,
    std::span<const std::uint8_t> response) {
  if (session_id.empty() || request_seq == 0 || request.empty() ||
      response.empty() || response.size() > maximum_response_bytes_) {
    return false;
  }
  const Fingerprint fingerprint = FingerprintRequest(request);
  std::scoped_lock lock(mutex_);
  const ULONGLONG now = GetTickCount64();
  PruneLocked(now);
  if (auto existing = responses_.find(session_id); existing != responses_.end())
    WipeResponseLocked(existing);
  while (!responses_.empty() &&
         (responses_.size() >= maximum_responses_ ||
          retained_response_bytes_ + response.size() >
              maximum_total_response_bytes_)) {
    const auto oldest = std::min_element(
        responses_.begin(), responses_.end(), [](const auto& left,
                                                  const auto& right) {
          return left.second.expires_at < right.second.expires_at;
        });
    WipeResponseLocked(oldest);
  }
  if (retained_response_bytes_ + response.size() >
      maximum_total_response_bytes_) {
    return false;
  }
  ResponseEntry entry;
  entry.request_seq = request_seq;
  entry.fingerprint = fingerprint;
  entry.response.assign(response.begin(), response.end());
  entry.expires_at = now + retry_deadline_milliseconds_;
  const auto [stored, inserted] =
      responses_.emplace(session_id, std::move(entry));
  if (!inserted) return false;
  retained_response_bytes_ += stored->second.response.size();
  wake_reaper_.notify_all();
  return true;
}

ReplayLookup ReplayLedger::LookupResponse(
    const std::string& session_id, std::uint64_t request_seq,
    std::span<const std::uint8_t> request,
    std::vector<std::uint8_t>* response) {
  if (response == nullptr) return ReplayLookup::kConflict;
  response->clear();
  const Fingerprint fingerprint = FingerprintRequest(request);
  std::scoped_lock lock(mutex_);
  PruneLocked(GetTickCount64());
  const auto entry = responses_.find(session_id);
  if (entry == responses_.end() || entry->second.request_seq != request_seq)
    return ReplayLookup::kMissing;
  if (entry->second.fingerprint != fingerprint) return ReplayLookup::kConflict;
  *response = entry->second.response;
  return ReplayLookup::kExact;
}

bool ReplayLedger::Acknowledge(const std::string& session_id,
                               std::uint64_t ack_request_seq) noexcept {
  std::scoped_lock lock(mutex_);
  PruneLocked(GetTickCount64());
  const auto entry = responses_.find(session_id);
  if (entry == responses_.end()) return false;
  if (entry->second.request_seq != ack_request_seq) return false;
  WipeResponseLocked(entry);
  return true;
}

void ReplayLedger::ClearSession(const std::string& session_id) noexcept {
  std::scoped_lock lock(mutex_);
  if (const auto entry = responses_.find(session_id); entry != responses_.end())
    WipeResponseLocked(entry);
}

bool ReplayLedger::RememberEnded(
    const std::string& session_id, std::uint64_t request_seq,
    std::span<const std::uint8_t> request) {
  if (session_id.empty() || request_seq == 0 || request.empty()) return false;
  const Fingerprint fingerprint = FingerprintRequest(request);
  std::scoped_lock lock(mutex_);
  const ULONGLONG now = GetTickCount64();
  PruneLocked(now);
  if (const auto response = responses_.find(session_id);
      response != responses_.end()) {
    WipeResponseLocked(response);
  }
  while (tombstones_.size() >= maximum_tombstones_) {
    const auto oldest = std::min_element(
        tombstones_.begin(), tombstones_.end(), [](const auto& left,
                                                   const auto& right) {
          return left.second.expires_at < right.second.expires_at;
        });
    tombstones_.erase(oldest);
  }
  tombstones_[session_id] =
      Tombstone{request_seq, fingerprint,
                now + tombstone_lifetime_milliseconds_};
  wake_reaper_.notify_all();
  return true;
}

ReplayLookup ReplayLedger::LookupEnded(
    const std::string& session_id, std::uint64_t request_seq,
    std::span<const std::uint8_t> request) {
  const Fingerprint fingerprint = FingerprintRequest(request);
  std::scoped_lock lock(mutex_);
  PruneLocked(GetTickCount64());
  const auto entry = tombstones_.find(session_id);
  if (entry == tombstones_.end()) return ReplayLookup::kMissing;
  if (entry->second.request_seq != request_seq ||
      entry->second.fingerprint != fingerprint) {
    return ReplayLookup::kConflict;
  }
  return ReplayLookup::kExact;
}

bool ReplayLedger::IsTombstoned(const std::string& session_id) noexcept {
  std::scoped_lock lock(mutex_);
  PruneLocked(GetTickCount64());
  return tombstones_.contains(session_id);
}

bool ReplayLedger::IsEndedSequence(const std::string& session_id,
                                   std::uint64_t request_seq) noexcept {
  std::scoped_lock lock(mutex_);
  PruneLocked(GetTickCount64());
  const auto entry = tombstones_.find(session_id);
  return entry != tombstones_.end() &&
         entry->second.request_seq == request_seq;
}

std::size_t ReplayLedger::response_count() const noexcept {
  std::scoped_lock lock(mutex_);
  return responses_.size();
}

std::size_t ReplayLedger::tombstone_count() const noexcept {
  std::scoped_lock lock(mutex_);
  return tombstones_.size();
}

std::size_t ReplayLedger::retained_response_bytes() const noexcept {
  std::scoped_lock lock(mutex_);
  return retained_response_bytes_;
}

void ReplayLedger::Reaper(std::stop_token stop_token) noexcept {
  while (!stop_token.stop_requested()) {
    std::unique_lock lock(mutex_);
    wake_reaper_.wait_for(lock, std::chrono::milliseconds(50));
    PruneLocked(GetTickCount64());
  }
}

}  // namespace clipvault::ime
