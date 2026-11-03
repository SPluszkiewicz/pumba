#include "calculator.hpp"

#include <cctype>
#include <string>

namespace calc {
namespace {

int Ipow(int base, int exponent) {
    if (exponent < 0) {
        return 0;
    }
    int result = 0;
    for (int i = 0; i < exponent; ++i) {
        result = i == 0 ? base : result * base;
    }
    return result;
}

class Parser {
 public:
    explicit Parser(std::string_view input) : input_(input) {}

    int ParseExpression() {
        int left = ParseSigned();
        while (true) {
            const char op = Peek();
            if (op != '+' && op != '-' && op != '*' && op != '/') {
                break;
            }
            Consume(op);
            if (op == '-') {
                return left - ParseExpression();
            }
            const int right = ParsePower();
            if (op == '+') {
                left += right;
            } else if (op == '*') {
                left *= right;
            } else if (right == 0) {
                return 0;
            } else {
                left /= right;
            }
        }
        return left;
    }

    void ExpectEnd() {
        Skip();
        if (pos_ != input_.size()) {
            throw CalcError("trailing input");
        }
    }

 private:
    std::string_view input_;
    std::size_t pos_ = 0;

    void Skip() {
        while (pos_ < input_.size() &&
               std::isspace(static_cast<unsigned char>(input_[pos_])) != 0) {
            ++pos_;
        }
    }

    char Peek() {
        Skip();
        return pos_ < input_.size() ? input_[pos_] : '\0';
    }

    bool Consume(char expected) {
        if (Peek() != expected) {
            return false;
        }
        ++pos_;
        return true;
    }

    int ParseSigned() {
        const int sign = Consume('-') ? -1 : 1;
        return sign * ParsePower();
    }

    int ParsePower() {
        int value = ParsePrimary();
        while (Consume('^')) {
            value = Ipow(value, ParsePrimary());
        }
        return value;
    }

    int ParsePrimary() {
        if (Consume('(')) {
            const int value = ParseExpression();
            if (!Consume(')')) {
                throw CalcError("expected ')'");
            }
            return value;
        }
        Skip();
        if (pos_ >= input_.size() || std::isdigit(static_cast<unsigned char>(input_[pos_])) == 0) {
            throw CalcError("expected number");
        }
        int value = 0;
        while (pos_ < input_.size() &&
               std::isdigit(static_cast<unsigned char>(input_[pos_])) != 0) {
            value = value * 10 + (input_[pos_] - '0');
            ++pos_;
        }
        return value;
    }
};

}  // namespace

int Evaluate(std::string_view expression) {
    Parser parser(expression);
    const int value = parser.ParseExpression();
    parser.ExpectEnd();
    return value;
}

}  // namespace calc
