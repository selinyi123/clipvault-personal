#pragma once

namespace clipvault::ime {

struct RpcRecoveryPlan final {
  bool preserve_preedit_as_literal = false;
  bool replay_plain_letter = false;
  bool consume_original_key = false;
};

constexpr RpcRecoveryPlan PlanRpcRecovery(bool has_preedit,
                                          bool plain_unmodified_letter) noexcept {
  return RpcRecoveryPlan{
      has_preedit,
      plain_unmodified_letter,
      // Ambiguous commit, candidate, paging, and control operations are never
      // replayed. If a preedit was preserved literally, consume that ambiguous
      // original key rather than appending a selection digit/control character.
      has_preedit && !plain_unmodified_letter,
  };
}

}  // namespace clipvault::ime
