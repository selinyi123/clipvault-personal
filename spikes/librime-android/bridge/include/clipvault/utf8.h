#pragma once

#include <string>
#include <string_view>

namespace clipvault::rime_poc {

// Strict Unicode conversion helpers. They reject overlong UTF-8, truncated
// sequences, unpaired UTF-16 surrogates and code points above U+10FFFF.
std::u16string utf8_to_utf16(std::string_view input);
std::string utf16_to_utf8(std::u16string_view input);

}  // namespace clipvault::rime_poc
