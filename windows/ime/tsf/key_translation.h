#pragma once

namespace clipvault::ime {

constexpr bool LatinUppercase(bool shift, bool caps_lock) noexcept {
  return shift != caps_lock;
}

}  // namespace clipvault::ime
