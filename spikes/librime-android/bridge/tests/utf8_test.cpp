#include "clipvault/utf8.h"

#include <stdexcept>
#include <string>
#include <string_view>

using clipvault::rime_poc::utf16_to_utf8;
using clipvault::rime_poc::utf8_to_utf16;

namespace {

void require(bool condition, const char* message) {
  if (!condition) {
    throw std::runtime_error(message);
  }
}

template <typename Function>
void require_invalid(Function&& function) {
  try {
    function();
  } catch (const std::invalid_argument&) {
    return;
  }
  throw std::runtime_error("expected invalid Unicode rejection");
}

}  // namespace

int main() {
  require(utf8_to_utf16("").empty(), "empty UTF-8 mismatch");
  require(utf8_to_utf16("ASCII") == u"ASCII", "ASCII mismatch");
  require(utf8_to_utf16("你好") == u"你好", "BMP Chinese mismatch");
  require(utf8_to_utf16("A中😀") == u"A中😀", "supplementary-plane mismatch");
  require(utf16_to_utf8(u"A中😀") == "A中😀", "UTF-16 reverse mismatch");
  require(utf8_to_utf16(utf16_to_utf8(u"Clip😀中")) == u"Clip😀中",
          "Unicode round-trip mismatch");

  require_invalid([] { utf8_to_utf16(std::string_view("\xC0\x80", 2)); });
  require_invalid([] { utf8_to_utf16(std::string_view("\xE4\xB8", 2)); });
  require_invalid([] { utf8_to_utf16(std::string_view("\xED\xA0\x80", 3)); });
  require_invalid([] { utf8_to_utf16(std::string_view("\xF4\x90\x80\x80", 4)); });
  require_invalid([] { utf8_to_utf16(std::string_view("\x80", 1)); });
  require_invalid([] { utf16_to_utf8(std::u16string_view(u"\xD800", 1)); });
  require_invalid([] { utf16_to_utf8(std::u16string_view(u"\xDC00", 1)); });
  require_invalid([] {
    const char16_t value[] = {static_cast<char16_t>(0xD800), u'A'};
    utf16_to_utf8(std::u16string_view(value, 2));
  });
  return 0;
}
