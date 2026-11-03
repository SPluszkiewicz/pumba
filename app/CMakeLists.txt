cmake_minimum_required(VERSION 3.22)

project(llm_client LANGUAGES CXX)

find_package(CURL REQUIRED)
find_package(nlohmann_json REQUIRED)

add_library(agent_client STATIC
    src/AgentClient.cpp
)
target_include_directories(agent_client PUBLIC src)
target_compile_features(agent_client PRIVATE cxx_std_20)
target_compile_options(agent_client PRIVATE -Wall -Wextra -Wpedantic -Werror)
target_link_libraries(agent_client PUBLIC CURL::libcurl nlohmann_json::nlohmann_json)

add_executable(llm-client
    src/main.cpp
)
target_compile_features(llm-client PRIVATE cxx_std_20)
target_compile_options(llm-client PRIVATE -Wall -Wextra -Wpedantic -Werror)
target_link_libraries(llm-client PRIVATE agent_client)

enable_testing()
include(CTest)

# Catch2 v3 from the Ubuntu 24.04 `catch2` apt package (same pin as the rest
# of the C++ toolchain). FetchContent is deliberately not used: the `tools`
# service configures this tree on an isolated network and cannot download
# at cmake time. `catch_discover_tests` registers each TEST_CASE as its own
# ctest entry so `ctest -R <case>` (roadmap 3.2) works.
find_package(Catch2 3 REQUIRED)
include(Catch)

add_executable(sample_test tests/sample_test.cpp)
target_compile_features(sample_test PRIVATE cxx_std_20)
target_compile_options(sample_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
target_link_libraries(sample_test PRIVATE Catch2::Catch2WithMain nlohmann_json::nlohmann_json)
catch_discover_tests(sample_test)

add_executable(agent_client_test tests/agent_client_test.cpp)
target_compile_features(agent_client_test PRIVATE cxx_std_20)
target_compile_options(agent_client_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
target_link_libraries(agent_client_test PRIVATE agent_client Catch2::Catch2WithMain)
catch_discover_tests(agent_client_test)
