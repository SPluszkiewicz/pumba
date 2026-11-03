cmake_minimum_required(VERSION 3.22)

project(calculator LANGUAGES CXX)

add_library(calculator STATIC src/calculator.cpp)
target_include_directories(calculator PUBLIC src)
target_compile_features(calculator PRIVATE cxx_std_20)
target_compile_options(calculator PRIVATE -Wall -Wextra -Wpedantic -Werror)

enable_testing()
include(CTest)

find_package(Catch2 3 REQUIRED)
include(Catch)

add_executable(calculator_test tests/calculator_test.cpp)
target_compile_features(calculator_test PRIVATE cxx_std_20)
target_compile_options(calculator_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
target_link_libraries(calculator_test PRIVATE calculator Catch2::Catch2WithMain)
catch_discover_tests(calculator_test)
