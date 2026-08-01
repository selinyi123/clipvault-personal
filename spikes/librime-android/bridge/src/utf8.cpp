#include "clipvault/utf8.h"

#include <cstddef>
#include <cstdint>
#include <stdexcept>
#include <string>

namespace clipvault::rime_poc {
namespace {

[[noreturn]] void invalid_utf8(std::size_t offset, const char* reason) {
  throw std::invalid_argument("invalid UTF-8 at byte " + std::to_string(offset) +
                              ": " + reason);
}

[[noreturn]] void invalid_utf16(std::size_t offset, const char* reason) {
  throw std::invalid_argument("invalid UTF-16 at code unit " +
                              std::to_string(offset) + ": " + reason);
}

std::uint8_t byte_at(std::string_view input, std::size_t index) {
  return static_cast<std::uint8_t>(
      static_cast<unsigned char>(input[index]));
}

std::uint8_t continuation(std::string_view input,
                          std::size_t index,
                          std::size_t sequence_start) {
  if (index >= input.size()) {
    invalid_utf8(sequence_start, "truncated sequence");
  }
  const auto value = byte_at(input, index);
  if ((value & 0xC0U) != 0x80U) {
    invalid_utf8(index, "expected continuation byte");
  }
  return value;
}

void append_utf16(std::u16string& output,
                  std::uint32_t code_point,
                  std::size_t offset) {
  if (code_point >= 0xD800U && code_point <= 0xDFFFU) {
    invalid_utf8(offset, "surrogate code point");
  }
  if (code_point > 0x10FFFFU) {
    invalid_utf8(offset, "code point above U+10FFFF");
  }
  if (code_point <= 0xFFFFU) {
    output.push_back(static_cast<char16_t>(code_point));
    return;
  }
  code_point -= 0x10000U;
  output.push_back(static_cast<char16_t>(0xD800U + (code_point >> 10U)));
  output.push_back(static_cast<char16_t>(0xDC00U + (code_point & 0x3FFU)));
}

void append_utf8(std::string& output, std::uint32_t code_point) {
  if (code_point <= 0x7FU) {
    output.push_back(static_cast<char>(code_point));
  } else if (code_point <= 0x7FFU) {
    output.push_back(static_cast<char>(0xC0U | (code_point >> 6U)));
    output.push_back(static_cast<char>(0x80U | (code_point & 0x3FU)));
  } else if (code_point <= 0xFFFFU) {
    output.push_back(static_cast<char>(0xE0U | (code_point >> 12U)));
    output.push_back(static_cast<char>(0x80U | ((code_point >> 6U) & 0x3FU)));
    output.push_back(static_cast<char>(0x80U | (code_point & 0x3FU)));
  } else {
    output.push_back(static_cast<char>(0xF0U | (code_point >> 18U)));
    output.push_back(static_cast<char>(0x80U | ((code_point >> 12U) & 0x3FU)));
    output.push_back(static_cast<char>(0x80U | ((code_point >> 6U) & 0x3FU)));
    output.push_back(static_cast<char>(0x80U | (code_point & 0x3FU)));
  }
}

}  // namespace

std::u16string utf8_to_utf16(std::string_view input) {
  std::u16string output;
  output.reserve(input.size());

  std::size_t index = 0;
  while (index < input.size()) {
    const std::size_t start = index;
    const auto first = byte_at(input, index++);

    if (first <= 0x7FU) {
      append_utf16(output, first, start);
      continue;
    }

    if (first >= 0xC2U && first <= 0xDFU) {
      const auto second = continuation(input, index++, start);
      const std::uint32_t code_point =
          (static_cast<std::uint32_t>(first & 0x1FU) << 6U) |
          static_cast<std::uint32_t>(second & 0x3FU);
      append_utf16(output, code_point, start);
      continue;
    }

    if (first >= 0xE0U && first <= 0xEFU) {
      const auto second = continuation(input, index++, start);
      const auto third = continuation(input, index++, start);
      if (first == 0xE0U && second < 0xA0U) {
        invalid_utf8(start, "overlong three-byte sequence");
      }
      if (first == 0xEDU && second >= 0xA0U) {
        invalid_utf8(start, "UTF-16 surrogate encoding");
      }
      const std::uint32_t code_point =
          (static_cast<std::uint32_t>(first & 0x0FU) << 12U) |
          (static_cast<std::uint32_t>(second & 0x3FU) << 6U) |
          static_cast<std::uint32_t>(third & 0x3FU);
      append_utf16(output, code_point, start);
      continue;
    }

    if (first >= 0xF0U && first <= 0xF4U) {
      const auto second = continuation(input, index++, start);
      const auto third = continuation(input, index++, start);
      const auto fourth = continuation(input, index++, start);
      if (first == 0xF0U && second < 0x90U) {
        invalid_utf8(start, "overlong four-byte sequence");
      }
      if (first == 0xF4U && second > 0x8FU) {
        invalid_utf8(start, "code point above U+10FFFF");
      }
      const std::uint32_t code_point =
          (static_cast<std::uint32_t>(first & 0x07U) << 18U) |
          (static_cast<std::uint32_t>(second & 0x3FU) << 12U) |
          (static_cast<std::uint32_t>(third & 0x3FU) << 6U) |
          static_cast<std::uint32_t>(fourth & 0x3FU);
      append_utf16(output, code_point, start);
      continue;
    }

    invalid_utf8(start, "invalid leading byte");
  }

  return output;
}

std::string utf16_to_utf8(std::u16string_view input) {
  std::string output;
  output.reserve(input.size());

  std::size_t index = 0;
  while (index < input.size()) {
    const std::size_t start = index;
    const auto first = static_cast<std::uint32_t>(input[index++]);
    std::uint32_t code_point = first;

    if (first >= 0xD800U && first <= 0xDBFFU) {
      if (index >= input.size()) {
        invalid_utf16(start, "truncated surrogate pair");
      }
      const auto second = static_cast<std::uint32_t>(input[index++]);
      if (second < 0xDC00U || second > 0xDFFFU) {
        invalid_utf16(index - 1, "expected low surrogate");
      }
      code_point = 0x10000U + ((first - 0xD800U) << 10U) +
                   (second - 0xDC00U);
    } else if (first >= 0xDC00U && first <= 0xDFFFU) {
      invalid_utf16(start, "unpaired low surrogate");
    }

    append_utf8(output, code_point);
  }

  return output;
}

}  // namespace clipvault::rime_poc
