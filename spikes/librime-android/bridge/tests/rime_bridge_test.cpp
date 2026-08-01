#include "clipvault/rime_bridge.h"

#include <memory>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

using clipvault::rime_poc::Backend;
using clipvault::rime_poc::Bridge;
using clipvault::rime_poc::Candidate;
using clipvault::rime_poc::InitOptions;
using clipvault::rime_poc::Snapshot;

namespace {

void require(bool condition, const char* message) {
  if (!condition) {
    throw std::runtime_error(message);
  }
}

class FakeBackend final : public Backend {
 public:
  void initialize(const InitOptions& options) override {
    initialized = true;
    last_options = options;
    if (throw_on_initialize) {
      throw std::runtime_error("synthetic initialization failure");
    }
  }

  void reset() override {
    composition.clear();
    candidates.clear();
    if (!leave_commit_on_reset) {
      commit.clear();
    }
  }

  bool process_key(int keycode, int) override {
    if (!initialized || keycode == '!') return false;
    composition.push_back(static_cast<char>(keycode));
    if (composition == "nihao") {
      candidates = {{"你好", "synthetic"}};
    }
    return true;
  }

  bool select_candidate(std::size_t index) override {
    if (index >= candidates.size()) return false;
    commit = candidates[index].text;
    composition.clear();
    candidates.clear();
    return true;
  }

  Snapshot snapshot() override {
    Snapshot value{composition, candidates, commit};
    commit.clear();
    return value;
  }

  void shutdown() noexcept override {
    initialized = false;
    ++shutdown_calls;
  }

  bool initialized = false;
  bool throw_on_initialize = false;
  bool leave_commit_on_reset = false;
  int shutdown_calls = 0;
  InitOptions last_options;
  std::string composition;
  std::vector<Candidate> candidates;
  std::string commit;
};

template <typename Error, typename Fn>
void expect_error(Fn&& fn) {
  try {
    std::forward<Fn>(fn)();
  } catch (const Error&) {
    return;
  }
  throw std::runtime_error("expected exception was not thrown");
}

void lifecycle_and_candidate_flow() {
  auto backend = std::make_unique<FakeBackend>();
  auto* raw = backend.get();
  Bridge bridge(std::move(backend));

  bridge.initialize({"/tmp/shared", "/tmp/user", "clipvault_poc"});
  require(bridge.initialized(), "bridge must be initialized");
  require(raw->last_options.schema_id == "clipvault_poc", "schema must be forwarded");

  Snapshot current;
  for (char key : std::string("nihao")) {
    const auto result = bridge.process_key(static_cast<unsigned char>(key));
    require(result.handled, "synthetic key must be handled");
    current = result.state;
  }
  require(current.composition == "nihao", "composition mismatch");
  const std::vector<Candidate> expected_candidates{{"你好", "synthetic"}};
  require(current.candidates == expected_candidates, "candidate mismatch");

  current = bridge.select_candidate(0);
  require(current.composition.empty(), "selection must clear composition");
  require(current.candidates.empty(), "selection must clear candidates");
  require(current.commit == "你好", "commit mismatch");

  const auto unhandled = bridge.process_key('!');
  require(!unhandled.handled, "unhandled key must not be treated as an engine error");

  current = bridge.process_key('n').state;
  require(current.composition == "n", "composition must contain n");
  current = bridge.reset();
  require(current.composition.empty(), "reset must clear composition");
  require(current.candidates.empty(), "reset must clear candidates");

  bridge.shutdown();
  require(!bridge.initialized(), "bridge must be shut down");
  require(!raw->initialized, "backend must be shut down");
}

void initialization_failure_is_cleaned_up() {
  auto backend = std::make_unique<FakeBackend>();
  auto* raw = backend.get();
  raw->throw_on_initialize = true;
  Bridge bridge(std::move(backend));

  expect_error<std::runtime_error>(
      [&] { bridge.initialize({"/tmp/shared", "/tmp/user", "clipvault_poc"}); });
  require(!bridge.initialized(), "failed initialization must not publish initialized state");
  require(!raw->initialized, "failed initialization must shut down the backend");
  require(raw->shutdown_calls == 1, "failed initialization must clean up exactly once");
}

void reset_rejects_stale_commit() {
  auto backend = std::make_unique<FakeBackend>();
  auto* raw = backend.get();
  Bridge bridge(std::move(backend));
  bridge.initialize({"/tmp/shared", "/tmp/user", "clipvault_poc"});
  raw->leave_commit_on_reset = true;
  raw->commit = "stale";
  expect_error<std::runtime_error>([&] { bridge.reset(); });
}

void fail_closed_contracts() {
  expect_error<std::invalid_argument>([] { Bridge bridge(nullptr); });

  auto backend = std::make_unique<FakeBackend>();
  Bridge bridge(std::move(backend));
  expect_error<std::logic_error>([&] { bridge.process_key('n'); });
  expect_error<std::invalid_argument>(
      [&] { bridge.initialize({"", "/tmp/user", "clipvault_poc"}); });
  expect_error<std::invalid_argument>(
      [&] { bridge.initialize({"relative/shared", "/tmp/user", "clipvault_poc"}); });
  expect_error<std::invalid_argument>(
      [&] { bridge.initialize({"/tmp/same", "/tmp/same", "clipvault_poc"}); });

  bridge.initialize({"/tmp/shared", "/tmp/user", "clipvault_poc"});
  expect_error<std::logic_error>(
      [&] { bridge.initialize({"/tmp/shared", "/tmp/user2", "clipvault_poc"}); });
  expect_error<std::invalid_argument>([&] { bridge.process_key(-1); });
  expect_error<std::out_of_range>([&] { bridge.select_candidate(99); });
}

}  // namespace

int main() {
  lifecycle_and_candidate_flow();
  initialization_failure_is_cleaned_up();
  reset_rejects_stale_commit();
  fail_closed_contracts();
  return 0;
}
