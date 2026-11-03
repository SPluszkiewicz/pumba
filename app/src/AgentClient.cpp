#include "AgentClient.hpp"

#include <curl/curl.h>

#include <sstream>
#include <utility>

namespace agent_client {

namespace {

size_t CurlWriteCallback(char* contents, size_t size, size_t nmemb, void* user_data) {
    auto* out = static_cast<std::string*>(user_data);
    out->append(contents, size * nmemb);
    return size * nmemb;
}

std::string Lstrip(std::string value) {
    const auto start = value.find_first_not_of(" \t");
    return start == std::string::npos ? std::string() : value.substr(start);
}

void DispatchSseBlock(const std::string& block, const StreamEventFn& on_event) {
    if (on_event == nullptr) {
        return;
    }
    std::string event = "message";
    std::string data;
    std::istringstream in(block);
    std::string line;
    while (std::getline(in, line)) {
        if (!line.empty() && line.back() == '\r') {
            line.pop_back();
        }
        if (line.rfind("event:", 0) == 0) {
            event = Lstrip(line.substr(6));
        } else if (line.rfind("data:", 0) == 0) {
            if (!data.empty()) {
                data.push_back('\n');
            }
            data += Lstrip(line.substr(5));
        }
    }
    if (data.empty()) {
        return;
    }
    on_event(event, nlohmann::json::parse(data));
}

struct StreamWriteState {
    std::string buffer;
    StreamEventFn on_event;
    ChatResponse done;
    bool saw_done = false;
    std::string error;
    // `event: error` from the agent (the turn failed after the stream opened).
    std::string server_error;
};

size_t CurlStreamWriteCallback(char* contents, size_t size, size_t nmemb, void* user_data) {
    auto* state = static_cast<StreamWriteState*>(user_data);
    const size_t nbytes = size * nmemb;
    try {
        FeedSse(state->buffer,
                std::string_view(contents, nbytes),
                [&](const std::string& event, const nlohmann::json& data) {
                    if (state->on_event) {
                        state->on_event(event, data);
                    }
                    if (event == "done") {
                        state->done = ParseChatResponse(data.dump());
                        state->saw_done = true;
                    } else if (event == "error") {
                        state->server_error = data.value("message", std::string("unknown error"));
                    }
                });
    } catch (const std::exception& e) {
        state->error = e.what();
        return 0;
    }
    return nbytes;
}

}  // namespace

void FeedSse(std::string& buffer, std::string_view chunk, const StreamEventFn& on_event) {
    buffer.append(chunk.data(), chunk.size());
    while (true) {
        const auto sep = buffer.find("\n\n");
        const auto sep_cr = buffer.find("\r\n\r\n");
        size_t pos = std::string::npos;
        size_t skip = 0;
        if (sep != std::string::npos && (sep_cr == std::string::npos || sep < sep_cr)) {
            pos = sep;
            skip = 2;
        } else if (sep_cr != std::string::npos) {
            pos = sep_cr;
            skip = 4;
        }
        if (pos == std::string::npos) {
            break;
        }
        DispatchSseBlock(buffer.substr(0, pos), on_event);
        buffer.erase(0, pos + skip);
    }
}

HttpResponse AgentClient::CurlHttpPost(const std::string& url,
                                       const std::string& body,
                                       const std::vector<std::string>& headers,
                                       long timeout_seconds) {
    CURL* curl = curl_easy_init();
    if (curl == nullptr) {
        throw HttpTransportError("failed to initialize curl handle");
    }

    std::string response_body;
    struct curl_slist* header_list = nullptr;
    for (const auto& header : headers) {
        header_list = curl_slist_append(header_list, header.c_str());
    }

    curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
    curl_easy_setopt(curl, CURLOPT_POST, 1L);
    curl_easy_setopt(curl, CURLOPT_POSTFIELDS, body.c_str());
    curl_easy_setopt(curl, CURLOPT_POSTFIELDSIZE, static_cast<long>(body.size()));
    curl_easy_setopt(curl, CURLOPT_HTTPHEADER, header_list);
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, CurlWriteCallback);
    curl_easy_setopt(curl, CURLOPT_WRITEDATA, &response_body);
    curl_easy_setopt(curl, CURLOPT_TIMEOUT, timeout_seconds);
    curl_easy_setopt(curl, CURLOPT_FOLLOWLOCATION, 1L);

    CURLcode result = curl_easy_perform(curl);

    if (result != CURLE_OK) {
        std::string message = curl_easy_strerror(result);
        curl_slist_free_all(header_list);
        curl_easy_cleanup(curl);
        throw HttpTransportError("curl request failed: " + message);
    }

    long status_code = 0;
    curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &status_code);

    curl_slist_free_all(header_list);
    curl_easy_cleanup(curl);

    return HttpResponse{status_code, std::move(response_body)};
}

AgentClient::AgentClient(std::string base_url,
                         std::string bearer_token,
                         long request_timeout_seconds)
    : AgentClient(std::move(base_url),
                  std::move(bearer_token),
                  &AgentClient::CurlHttpPost,
                  request_timeout_seconds) {}

AgentClient::AgentClient(std::string base_url,
                         std::string bearer_token,
                         HttpPostFn transport,
                         long request_timeout_seconds)
    : base_url_(std::move(base_url)),
      bearer_token_(std::move(bearer_token)),
      request_timeout_seconds_(request_timeout_seconds),
      transport_(std::move(transport)) {}

ChatResponse AgentClient::Chat(const std::string& query, const std::string& conversation_id) const {
    nlohmann::json request_json{{"query", query}};
    if (!conversation_id.empty()) {
        request_json["conversation_id"] = conversation_id;
    }
    const std::string request_body = request_json.dump();

    const std::vector<std::string> headers{
        "Content-Type: application/json",
        "Authorization: Bearer " + bearer_token_,
    };

    const std::string url = base_url_ + "/chat";
    const HttpResponse response = transport_(url, request_body, headers, request_timeout_seconds_);

    if (response.status_code != 200) {
        throw AgentHttpError(response.status_code, response.body);
    }

    return ParseChatResponse(response.body);
}

ChatResponse AgentClient::ChatStream(const std::string& query,
                                     const std::string& conversation_id,
                                     StreamEventFn on_event) const {
    nlohmann::json request_json{{"query", query}};
    if (!conversation_id.empty()) {
        request_json["conversation_id"] = conversation_id;
    }
    const std::string request_body = request_json.dump();

    CURL* curl = curl_easy_init();
    if (curl == nullptr) {
        throw HttpTransportError("failed to initialize curl handle");
    }

    StreamWriteState state;
    state.on_event = std::move(on_event);

    struct curl_slist* header_list = nullptr;
    header_list = curl_slist_append(header_list, "Content-Type: application/json");
    header_list = curl_slist_append(header_list, "Accept: text/event-stream");
    const std::string auth = "Authorization: Bearer " + bearer_token_;
    header_list = curl_slist_append(header_list, auth.c_str());

    const std::string url = base_url_ + "/chat/stream";
    curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
    curl_easy_setopt(curl, CURLOPT_POST, 1L);
    curl_easy_setopt(curl, CURLOPT_POSTFIELDS, request_body.c_str());
    curl_easy_setopt(curl, CURLOPT_POSTFIELDSIZE, static_cast<long>(request_body.size()));
    curl_easy_setopt(curl, CURLOPT_HTTPHEADER, header_list);
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, CurlStreamWriteCallback);
    curl_easy_setopt(curl, CURLOPT_WRITEDATA, &state);
    curl_easy_setopt(curl, CURLOPT_TIMEOUT, request_timeout_seconds_);
    curl_easy_setopt(curl, CURLOPT_FOLLOWLOCATION, 1L);
    curl_easy_setopt(curl, CURLOPT_HTTP_TRANSFER_DECODING, 1L);

    const CURLcode result = curl_easy_perform(curl);
    long status_code = 0;
    curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &status_code);
    curl_slist_free_all(header_list);
    curl_easy_cleanup(curl);

    if (!state.error.empty()) {
        throw AgentResponseParseError(state.error);
    }
    if (result != CURLE_OK) {
        throw HttpTransportError(std::string("curl request failed: ") + curl_easy_strerror(result));
    }
    if (status_code != 200) {
        throw AgentHttpError(status_code, state.buffer);
    }
    if (!state.saw_done) {
        if (!state.server_error.empty()) {
            throw AgentStreamError(state.server_error);
        }
        throw AgentResponseParseError("stream ended without a done event");
    }
    return state.done;
}

ChatResponse ParseChatResponse(const std::string& body) {
    nlohmann::json parsed;
    try {
        parsed = nlohmann::json::parse(body);
    } catch (const nlohmann::json::parse_error& e) {
        throw AgentResponseParseError(std::string("invalid JSON in agent response: ") + e.what());
    }

    ChatResponse chat_response;
    try {
        // `answer` is the one field every response carries. The rest follow
        // CONTRACTS.md but are read defensively: `symbol_name` is nullable
        // (working-copy overlay chunks have none), and a cancelled stream's
        // `done` is a minimal ChatResponse.
        chat_response.answer = parsed.at("answer").get<std::string>();
        chat_response.used_context = parsed.value("used_context", false);

        const auto sources_it = parsed.find("sources");
        if (sources_it != parsed.end() && sources_it->is_array()) {
            for (const auto& source_json : *sources_it) {
                Source source;
                source.file_path = source_json.at("file_path").get<std::string>();
                source.start_line = source_json.at("start_line").get<int64_t>();
                source.end_line = source_json.at("end_line").get<int64_t>();
                const auto symbol_it = source_json.find("symbol_name");
                if (symbol_it != source_json.end() && symbol_it->is_string()) {
                    source.symbol_name = symbol_it->get<std::string>();
                }
                chat_response.sources.push_back(std::move(source));
            }
        }

        const auto tool_calls_it = parsed.find("tool_calls");
        if (tool_calls_it != parsed.end() && tool_calls_it->is_array()) {
            for (const auto& tool_call_json : *tool_calls_it) {
                ToolCall tool_call;
                tool_call.name = tool_call_json.at("name").get<std::string>();
                tool_call.arguments = tool_call_json.value("arguments", nlohmann::json::object());
                tool_call.result_summary = tool_call_json.value("result_summary", "");
                chat_response.tool_calls.push_back(std::move(tool_call));
            }
        }

        const auto conversation_it = parsed.find("conversation_id");
        if (conversation_it != parsed.end() && conversation_it->is_string()) {
            chat_response.conversation_id = conversation_it->get<std::string>();
        }
    } catch (const nlohmann::json::exception& e) {
        throw AgentResponseParseError(std::string("malformed agent response shape: ") + e.what());
    }

    return chat_response;
}

}  // namespace agent_client
