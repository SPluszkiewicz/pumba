#pragma once

#include <cstdint>
#include <functional>
#include <nlohmann/json.hpp>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace agent_client {

// Raw result of an HTTP POST, decoupled from whichever HTTP library actually
// performs the request. This is the seam that makes AgentClient testable
// without a real socket: production code plugs in a libcurl-backed
// HttpPostFn, tests plug in a lambda that returns canned responses.
struct HttpResponse {
    long status_code = 0;
    std::string body;
};

// `url`, `body` (raw request payload), `headers` (each entry is a full
// "Name: value" header line, matching the shape curl expects). Implementations
// are expected to throw HttpTransportError on transport-level failures
// (connection refused, DNS failure, timeout, ...); a well-formed HTTP
// response - even a non-200 one - should be returned normally, not thrown.
using HttpPostFn = std::function<HttpResponse(const std::string& url,
                                              const std::string& body,
                                              const std::vector<std::string>& headers,
                                              long timeout_seconds)>;

// Thrown for transport-level failures (timeouts, connection errors, DNS,
// TLS, ...), i.e. anything where we never got a well-formed HTTP response.
class HttpTransportError : public std::runtime_error {
 public:
    explicit HttpTransportError(const std::string& message) : std::runtime_error(message) {}
};

// Thrown when the agent responded with a non-200 status code.
class AgentHttpError : public std::runtime_error {
 public:
    AgentHttpError(long status_code, std::string body)
        : std::runtime_error("agent returned HTTP " + std::to_string(status_code)),
          status_code_(status_code),
          body_(std::move(body)) {}

    [[nodiscard]] long status_code() const noexcept {
        return status_code_;
    }
    [[nodiscard]] const std::string& body() const noexcept {
        return body_;
    }

 private:
    long status_code_;
    std::string body_;
};

// Thrown when a 200 response body could not be parsed as the expected JSON
// shape (invalid JSON, or missing/mistyped required fields).
class AgentResponseParseError : public std::runtime_error {
 public:
    explicit AgentResponseParseError(const std::string& message) : std::runtime_error(message) {}
};

// Thrown when the agent reported a failure over the stream itself
// (`event: error`, see docs/reference/agent-api.md) instead of a `done`.
class AgentStreamError : public std::runtime_error {
 public:
    explicit AgentStreamError(const std::string& message)
        : std::runtime_error("agent reported an error during the stream: " + message) {}
};

struct Source {
    std::string file_path;
    int64_t start_line = 0;
    int64_t end_line = 0;
    // Empty when the agent sent `null` (e.g. a working-copy overlay chunk).
    std::string symbol_name;
};

struct ToolCall {
    std::string name;
    nlohmann::json arguments;
    std::string result_summary;
};

struct ChatResponse {
    std::string answer;
    std::vector<Source> sources;
    bool used_context = false;
    std::vector<ToolCall> tool_calls;
    std::string conversation_id;
};

using StreamEventFn = std::function<void(const std::string& event, const nlohmann::json& data)>;

// Thin client for the `agent` service's `POST /chat` endpoint (see
// CONTRACTS.md). The actual HTTP POST is delegated to an injectable
// HttpPostFn so tests can exercise request-building and response-parsing
// logic without any real network I/O or a mocking framework.
class AgentClient {
 public:
    // Uses a real libcurl-backed transport. `request_timeout_seconds` is the
    // total curl timeout for one /chat round-trip (see APP_REQUEST_TIMEOUT_SECONDS).
    explicit AgentClient(std::string base_url,
                         std::string bearer_token,
                         long request_timeout_seconds = 120);

    // Uses `transport` instead of libcurl. This is the constructor tests use
    // to inject a fake transport.
    AgentClient(std::string base_url,
                std::string bearer_token,
                HttpPostFn transport,
                long request_timeout_seconds = 120);

    // POSTs {"query": query} to "<base_url>/chat" with the bearer token
    // header. Throws HttpTransportError, AgentHttpError, or
    // AgentResponseParseError on failure.
    [[nodiscard]] ChatResponse Chat(const std::string& query,
                                    const std::string& conversation_id = "") const;

    // POSTs to "<base_url>/chat/stream" (SSE). `on_event` is called for each
    // complete event (`meta`, `route`, `rag`, `context`, `activity`,
    // `tool_delta`, `tool_call`, `tool_result`, `token`, `done`, `error`).
    // Returns the payload of the `done` event. Throws AgentStreamError when
    // the agent sent `error` instead of `done`.
    [[nodiscard]] ChatResponse ChatStream(const std::string& query,
                                          const std::string& conversation_id,
                                          StreamEventFn on_event) const;

    // Default production transport: performs a real HTTP POST via libcurl.
    static HttpResponse CurlHttpPost(const std::string& url,
                                     const std::string& body,
                                     const std::vector<std::string>& headers,
                                     long timeout_seconds);

 private:
    std::string base_url_;
    std::string bearer_token_;
    long request_timeout_seconds_;
    HttpPostFn transport_;
};

// Parses the `/chat` JSON response body into a ChatResponse. Exposed
// separately so tests can exercise parsing in isolation.
ChatResponse ParseChatResponse(const std::string& body);

// Incremental SSE parser (`event:` / `data:` blocks separated by a blank
// line). `FeedSse` appends `chunk` to `buffer` and invokes `on_event` for
// every complete frame.
void FeedSse(std::string& buffer, std::string_view chunk, const StreamEventFn& on_event);

}  // namespace agent_client
