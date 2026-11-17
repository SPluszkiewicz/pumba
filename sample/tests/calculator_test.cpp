#include "calculator.hpp"

#include <catch2/catch_test_macros.hpp>

TEST_CASE("ten modulo three is one") {
    REQUIRE(calc::Evaluate("10%3") == 1);
}

TEST_CASE("modulo by zero is an error") {
    REQUIRE_THROWS_AS(calc::Evaluate("10%0"), calc::CalcError);
}

TEST_CASE("single number") {
    REQUIRE(calc::Evaluate("42") == 42);
}

TEST_CASE("addition and whitespace") {
    REQUIRE(calc::Evaluate("1 + 2") == 3);
}

TEST_CASE("multiplication alone") {
    REQUIRE(calc::Evaluate("3*4") == 12);
}

TEST_CASE("integer division") {
    REQUIRE(calc::Evaluate("8/2") == 4);
}

TEST_CASE("power") {
    REQUIRE(calc::Evaluate("2^3") == 8);
}

TEST_CASE("parentheses") {
    REQUIRE(calc::Evaluate("(2+3)*4") == 20);
}

TEST_CASE("leading unary minus") {
    REQUIRE(calc::Evaluate("-5+2") == -3);
}

TEST_CASE("empty input is an error") {
    REQUIRE_THROWS_AS(calc::Evaluate("   "), calc::CalcError);
}

TEST_CASE("known_bug: multiplication binds tighter than addition") {
    REQUIRE(calc::Evaluate("2+3*4") == 14);
}

TEST_CASE("known_bug: subtraction is left associative") {
    REQUIRE(calc::Evaluate("8-3-2") == 3);
}

TEST_CASE("known_bug: exponent is right associative") {
    REQUIRE(calc::Evaluate("2^3^2") == 512);
}

TEST_CASE("known_bug: power of zero is one") {
    REQUIRE(calc::Evaluate("5^0") == 1);
}

TEST_CASE("known_bug: unary minus after an operator") {
    REQUIRE(calc::Evaluate("1+-2") == -1);
    REQUIRE(calc::Evaluate("2*-3") == -6);
}

TEST_CASE("known_bug: division by zero is an error") {
    REQUIRE_THROWS_AS(calc::Evaluate("1/0"), calc::CalcError);
}

TEST_CASE("known_bug: negative exponent is an error") {
    REQUIRE_THROWS_AS(calc::Evaluate("2^(-1)"), calc::CalcError);
}
