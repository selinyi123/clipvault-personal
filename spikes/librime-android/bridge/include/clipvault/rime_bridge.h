#pragma once

#include <cstddef>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

namespace clipvault::rime_poc {

struct Candidate {
  std::string text;
  std::string comment;
};

inline bool operator==(const Candidate& lhs, const Candidate& rhs) {
  return lhs.text == rhs.text && lhs.comment == rhs.comment;
}

struct Snapshot {
  std::string composition;
  std::vector<Candidate> candidates;
  std::string commit;
};

struct KeyResult {
  bool handled = false;
  Snapshot state;
};

struct InitOptions {
  std::string shared_data_dir;
  std::string user_data_dir;
  std::string schema_id = "clipvault_poc";
};

class Backend {
 public:
  virtual ~Backend() = default;
  virtual void initialize(const InitOptions& options) = 0;
  virtual void reset() = 0;
  virtual bool process_key(int keycode, int mask) = 0;
  virtual bool select_candidate(std::size_t index) = 0;
  virtual Snapshot snapshot() = 0;
  virtual void shutdown() noexcept = 0;
};

class Bridge final {
 public:
  explicit Bridge(std::unique_ptr<Backend> backend);
  ~Bridge();

  Bridge(const Bridge&) = delete;
  Bridge& operator=(const Bridge&) = delete;
  Bridge(Bridge&&) = delete;
  Bridge& operator=(Bridge&&) = delete;

  void initialize(const InitOptions& options);
  KeyResult process_key(int keycode, int mask = 0);
  Snapshot select_candidate(std::size_t index);
  Snapshot reset();
  void shutdown() noexcept;
  bool initialized() const noexcept;

 private:
  static void validate_options(const InitOptions& options);
  void require_initialized() const;

  std::unique_ptr<Backend> backend_;
  mutable std::mutex mutex_;
  bool initialized_ = false;
};

}  // namespace clipvault::rime_poc
