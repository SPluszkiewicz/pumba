// Smoke tests independent of AgentClient. Each TEST_CASE is a separate
// ctest entry (see catch_discover_tests in CMakeLists.txt).

#include <catch2/catch_test_macros.hpp>
#include <nlohmann/json.hpp>
#include <string>

TEST_CASE("json_round_trip") {
    const nlohmann::json original{{"query", "hello world"}};
    const std::string serialized = original.dump();
    const nlohmann::json parsed = nlohmann::json::parse(serialized);

    REQUIRE(parsed.at("query").get<std::string>() == "hello world");
}

TEST_CASE("arithmetic_sanity") {
    constexpr int a = 2;
    constexpr int b = 2;
    REQUIRE(a + b == 4);
}
