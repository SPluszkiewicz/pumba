#pragma once

#include <stdexcept>
#include <string>
#include <string_view>

namespace calc {

// Integer arithmetic. Whitespace is ignored and parentheses group.
//
// Precedence, high to low:
//   unary minus
//   ^   right-associative, and n^0 == 1
//   * / %
//   + - left-associative
//
// Division by zero and a negative exponent throw CalcError.
class CalcError : public std::runtime_error {
 public:
    explicit CalcError(const std::string& message) : std::runtime_error(message) {}
};

int Evaluate(std::string_view expression);

}  // namespace calc
