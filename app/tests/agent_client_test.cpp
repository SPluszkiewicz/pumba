// Tests for AgentClient. These exercise the injectable HttpPostFn transport
// seam (see AgentClient.hpp) so no real sockets or HTTP server are needed.
// Each TEST_CASE is a separate ctest entry (catch_discover_tests).

#include <catch2/catch_test_macros.hpp>
#include <nlohmann/json.hpp>
#include <stdexcept>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

#include "AgentClient.hpp"

using agent_client::AgentClient;
using agent_client::AgentHttpError;
using agent_client::AgentResponseParseError;
using agent_client::ChatResponse;
using agent_client::HttpResponse;
using agent_client::HttpTransportError;

namespace {

constexpr const char* kBaseUrl = "http://agent:8090";
constexpr const char* kToken = "test-token-123";

const char* kValidResponseBody = R"json({
  "answer": "The indexer chunks C++ files by top-level symbol.",
  "sources": [
    { "file_path": "app/src/main.cpp", "start_line": 12, "end_line": 40, "symbol_name": "main" }
  ],
  "used_context": true,
  "tool_calls": [
    { "name": "build", "arguments": {"preset": "release"}, "result_summary": "build succeeded" }
  ]
})json";

}  // namespace

TEST_CASE("request_url_headers_and_body") {
    std::string captured_url;
    std::string captured_body;
    std::vector<std::string> captured_headers;

    AgentClient client(kBaseUrl,
                       kToken,
                       [&](const std::string& url,
                           const std::string& body,
                           const std::vector<std::string>& headers,
                           long) -> HttpResponse {
                           captured_url = url;
                           captured_body = body;
                           captured_headers = headers;
                           return HttpResponse{200, kValidResponseBody};
                       });

    std::ignore = client.Chat("How does the indexer chunk C++ files?");

    REQUIRE(captured_url == "http://agent:8090/chat");

    const nlohmann::json body_json = nlohmann::json::parse(captured_body);
    REQUIRE(body_json.at("query").get<std::string>() == "How does the indexer chunk C++ files?");

    bool found_auth_header = false;
    for (const auto& header : captured_headers) {
        if (header == "Authorization: Bearer test-token-123") {
            found_auth_header = true;
        }
    }
    REQUIRE(found_auth_header);
}

TEST_CASE("successful_response_parsing") {
    AgentClient client(kBaseUrl,
                       kToken,
                       [](const std::string&,
                          const std::string&,
                          const std::vector<std::string>&,
                          long) -> HttpResponse { return HttpResponse{200, kValidResponseBody}; });

    const ChatResponse response = client.Chat("anything");

    REQUIRE(response.answer == "The indexer chunks C++ files by top-level symbol.");
    REQUIRE(response.used_context);

    REQUIRE(response.sources.size() == 1);
    REQUIRE(response.sources[0].file_path == "app/src/main.cpp");
    REQUIRE(response.sources[0].start_line == 12);
    REQUIRE(response.sources[0].end_line == 40);
    REQUIRE(response.sources[0].symbol_name == "main");

    REQUIRE(response.tool_calls.size() == 1);
    REQUIRE(response.tool_calls[0].name == "build");
    REQUIRE(response.tool_calls[0].result_summary == "build succeeded");
    REQUIRE(response.tool_calls[0].arguments.at("preset").get<std::string>() == "release");
}

TEST_CASE("response_without_context") {
    const char* body = R"json({
      "answer": "I don't have enough context to answer that.",
      "sources": [],
      "used_context": false,
      "tool_calls": []
    })json";

    AgentClient client(kBaseUrl,
                       kToken,
                       [&](const std::string&,
                           const std::string&,
                           const std::vector<std::string>&,
                           long) -> HttpResponse { return HttpResponse{200, body}; });

    const ChatResponse response = client.Chat("anything");
    REQUIRE_FALSE(response.used_context);
    REQUIRE(response.sources.empty());
    REQUIRE(response.tool_calls.empty());
}

TEST_CASE("non_200_status_throws_agent_http_error") {
    AgentClient client(
        kBaseUrl,
        kToken,
        [](const std::string&, const std::string&, const std::vector<std::string>&, long)
            -> HttpResponse { return HttpResponse{401, R"({"error": "missing bearer token"})"}; });

    REQUIRE_THROWS_AS(client.Chat("anything"), AgentHttpError);
    try {
        std::ignore = client.Chat("anything");
    } catch (const AgentHttpError& e) {
        REQUIRE(e.status_code() == 401);
        REQUIRE(e.body().find("missing bearer token") != std::string::npos);
    }
}

TEST_CASE("malformed_json_throws_parse_error") {
    AgentClient client(kBaseUrl,
                       kToken,
                       [](const std::string&,
                          const std::string&,
                          const std::vector<std::string>&,
                          long) -> HttpResponse { return HttpResponse{200, "{not valid json"}; });

    REQUIRE_THROWS_AS(client.Chat("anything"), AgentResponseParseError);
}

TEST_CASE("missing_field_throws_parse_error") {
    AgentClient client(
        kBaseUrl,
        kToken,
        [](const std::string&, const std::string&, const std::vector<std::string>&, long)
            -> HttpResponse {
            return HttpResponse{200, R"({"used_context": false, "sources": [], "tool_calls": []})"};
        });

    REQUIRE_THROWS_AS(client.Chat("anything"), AgentResponseParseError);
}

TEST_CASE("sse_parser_dispatches_complete_frames") {
    std::string buffer;
    std::vector<std::pair<std::string, std::string>> events;

    agent_client::FeedSse(buffer,
                          "event: token\ndata: {\"text\": \"Hel\"}\n\n",
                          [&](const std::string& event, const nlohmann::json& data) {
                              events.emplace_back(event, data.at("text").get<std::string>());
                          });
    REQUIRE(events.size() == 1);
    REQUIRE(events[0].first == "token");
    REQUIRE(events[0].second == "Hel");

    agent_client::FeedSse(buffer,
                          "event: token\ndata: {\"text\": \"lo\"}\n\neve",
                          [&](const std::string& event, const nlohmann::json& data) {
                              events.emplace_back(event, data.at("text").get<std::string>());
                          });
    REQUIRE(events.size() == 2);
    REQUIRE(events[1].second == "lo");

    agent_client::FeedSse(buffer,
                          "nt: done\ndata: {\"answer\": \"Hello\", \"sources\": [], "
                          "\"used_context\": false, \"tool_calls\": [], "
                          "\"conversation_id\": \"c1\"}\n\n",
                          [&](const std::string& event, const nlohmann::json& data) {
                              events.emplace_back(event, data.value("conversation_id", ""));
                          });
    REQUIRE(events.back().first == "done");
    REQUIRE(events.back().second == "c1");
}

TEST_CASE("parse_accepts_optional_conversation_id") {
    const ChatResponse response = agent_client::ParseChatResponse(kValidResponseBody);
    REQUIRE(response.conversation_id.empty());
}

TEST_CASE("parse_accepts_null_symbol_name") {
    // Working-copy overlay chunks carry `symbol_name: null` (CONTRACTS.md: string | null).
    const ChatResponse response = agent_client::ParseChatResponse(R"({
        "answer": "see the dirty file",
        "sources": [
            { "file_path": "src/calculator.cpp", "start_line": 1, "end_line": 30, "symbol_name": null }
        ],
        "used_context": true,
        "tool_calls": []
    })");
    REQUIRE(response.sources.size() == 1);
    REQUIRE(response.sources[0].symbol_name.empty());
    REQUIRE(response.sources[0].file_path == "src/calculator.cpp");
}

TEST_CASE("parse_accepts_minimal_stopped_payload") {
    // A cancelled stream ends with a `done` that only promises `answer`.
    const ChatResponse response = agent_client::ParseChatResponse(
        R"({"answer": "Stopped.", "conversation_id": "c9", "tool_calls": []})");
    REQUIRE(response.answer == "Stopped.");
    REQUIRE(response.conversation_id == "c9");
    REQUIRE_FALSE(response.used_context);
    REQUIRE(response.sources.empty());
}

TEST_CASE("transport_exception_propagates") {
    AgentClient client(
        kBaseUrl,
        kToken,
        [](const std::string&, const std::string&, const std::vector<std::string>&, long)
            -> HttpResponse { throw HttpTransportError("simulated timeout after 120s"); });

    REQUIRE_THROWS_AS(client.Chat("anything"), HttpTransportError);
    try {
        std::ignore = client.Chat("anything");
    } catch (const HttpTransportError& e) {
        REQUIRE(std::string(e.what()).find("simulated timeout") != std::string::npos);
    }
}
