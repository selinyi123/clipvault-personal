#pragma once

#include <cstddef>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace clipvault::rime_poc {

struct EnginePaths {
  std::string shared_data_dir;
  std::string user_data_dir;
};

struct Candidate {
  std::string text;
  std::string comment;
};

struct Snapshot {
  std::string composition;
  int highlighted_candidate_index = -1;
  std::vector<Candidate> candidates;
};

class RimeEngine final {
 public:
  static std::unique_ptr<RimeEngine> Open(const EnginePaths& paths,
                                          std::string* error);

  ~RimeEngine();

  RimeEngine(const RimeEngine&) = delete;
  RimeEngine& operator=(const RimeEngine&) = delete;
  RimeEngine(RimeEngine&&) = delete;
  RimeEngine& operator=(RimeEngine&&) = delete;

  bool Reset(std::string* error);
  bool ProcessKey(int keycode, int mask, std::string* error);
  bool GetSnapshot(Snapshot* snapshot, std::string* error) const;
  bool SelectCandidate(std::size_t index, std::string* error);
  std::optional<std::string> TakeCommit(std::string* error);
  std::string EngineVersion() const;

 private:
  class Impl;
  explicit RimeEngine(std::unique_ptr<Impl> impl);

  std::unique_ptr<Impl> impl_;
};

}  // namespace clipvault::rime_poc
