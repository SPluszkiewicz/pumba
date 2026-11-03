#include <cstdlib>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>

#include "AgentClient.hpp"

namespace {

// Reads an environment variable, returning an empty optional-ish string when
// unset. Distinguishing "unset" from "set to empty" isn't needed here: both
// are treated as "not provided" by the caller.
std::string GetEnvOrEmpty(const char* name) {
    const char* value = std::getenv(name);  // NOLINT(concurrency-mt-unsafe)
    return value != nullptr ? std::string(value) : std::string();
}

std::string JoinArgs(int argc, char** argv, int start) {
    std::ostringstream joined;
    for (int i = start; i < argc; ++i) {
        if (i > start) {
            joined << ' ';
        }
        joined << argv[i];
    }
    return joined.str();
}

std::string ReadAll(std::istream& in) {
    std::ostringstream out;
    out << in.rdbuf();
    return out.str();
}

std::string ReadFile(const std::string& path) {
    std::ifstream in(path);
    if (!in) {
        throw std::runtime_error("could not open prompt file: " + path);
    }
    return ReadAll(in);
}

void PrintUsage(const char* argv0) {
    std::cerr << "usage: " << argv0 << " <question>\n"
              << "       " << argv0 << " --file <path>\n"
              << "       " << argv0 << " -f <path>\n"
              << "       " << argv0 << " -                 # read prompt from stdin\n"
              << "       " << argv0 << " --repl            # multi-turn, streams tokens\n"
              << "       " << argv0 << " --no-stream <question>\n";
}

void PrintChatResponse(const agent_client::ChatResponse& response) {
    std::cout << response.answer << '\n';

    if (response.used_context && !response.sources.empty()) {
        std::cout << "\nSources:\n";
        for (const auto& source : response.sources) {
            std::cout << "  " << source.file_path << ':' << source.start_line << '-'
                      << source.end_line;
            if (!source.symbol_name.empty()) {
                std::cout << " (" << source.symbol_name << ')';
            }
            std::cout << '\n';
        }
    }

    if (!response.tool_calls.empty()) {
        std::cout << "\nTool calls:\n";
        for (const auto& tool_call : response.tool_calls) {
            std::cout << "  " << tool_call.name << ": " << tool_call.result_summary << '\n';
        }
    }
}

}  // namespace

int RunOneTurn(const agent_client::AgentClient& client,
               const std::string& query,
               std::string& conversation_id,
               bool stream) {
    try {
        if (!stream) {
            const agent_client::ChatResponse response = client.Chat(query, conversation_id);
            if (!response.conversation_id.empty()) {
                conversation_id = response.conversation_id;
            }
            PrintChatResponse(response);
            return 0;
        }

        agent_client::ChatResponse response;
        response = client.ChatStream(
            query, conversation_id, [&](const std::string& event, const nlohmann::json& data) {
                if (event == "token" && data.contains("text")) {
                    std::cout << data.at("text").get<std::string>() << std::flush;
                } else if (event == "tool_call" && data.contains("name")) {
                    std::cerr << "\n[tool " << data.at("name").get<std::string>() << "]\n";
                }
            });
        if (!response.conversation_id.empty()) {
            conversation_id = response.conversation_id;
        }
        std::cout << '\n';
        if (response.used_context && !response.sources.empty()) {
            std::cout << "\nSources:\n";
            for (const auto& source : response.sources) {
                std::cout << "  " << source.file_path << ':' << source.start_line << '-'
                          << source.end_line;
                if (!source.symbol_name.empty()) {
                    std::cout << " (" << source.symbol_name << ')';
                }
                std::cout << '\n';
            }
        }
        if (!response.tool_calls.empty()) {
            std::cout << "\nTool calls:\n";
            for (const auto& tool_call : response.tool_calls) {
                std::cout << "  " << tool_call.name << ": " << tool_call.result_summary << '\n';
            }
        }
        return 0;
    } catch (const agent_client::AgentHttpError& e) {
        std::cerr << "error: agent returned HTTP " << e.status_code() << ": " << e.body() << '\n';
        return 1;
    } catch (const agent_client::AgentResponseParseError& e) {
        std::cerr << "error: could not parse agent response: " << e.what() << '\n';
        return 1;
    } catch (const agent_client::AgentStreamError& e) {
        std::cerr << "\nerror: " << e.what() << '\n';
        return 1;
    } catch (const agent_client::HttpTransportError& e) {
        std::cerr << "error: request to agent failed: " << e.what() << '\n';
        return 1;
    }
}

int main(int argc, char** argv) {
    std::string query;
    bool repl = false;
    bool stream = true;

    try {
        if (argc < 2) {
            PrintUsage(argv[0]);
            return 2;
        }

        const std::string first = argv[1];
        if (first == "--help" || first == "-h") {
            PrintUsage(argv[0]);
            return 0;
        }
        if (first == "--repl") {
            repl = true;
        } else if (first == "--no-stream") {
            stream = false;
            if (argc < 3) {
                std::cerr << "error: --no-stream requires a question.\n";
                return 2;
            }
            query = JoinArgs(argc, argv, 2);
        } else if (first == "--file" || first == "-f") {
            if (argc < 3) {
                std::cerr << "error: " << first << " requires a path argument.\n";
                PrintUsage(argv[0]);
                return 2;
            }
            query = ReadFile(argv[2]);
        } else if (first == "-") {
            query = ReadAll(std::cin);
        } else {
            query = JoinArgs(argc, argv, 1);
        }
    } catch (const std::exception& e) {
        std::cerr << "error: " << e.what() << '\n';
        return 1;
    }

    const std::string base_url = GetEnvOrEmpty("AGENT_BASE_URL");
    const std::string bearer_token = GetEnvOrEmpty("AGENT_BEARER_TOKEN");
    const std::string timeout_raw = GetEnvOrEmpty("APP_REQUEST_TIMEOUT_SECONDS");

    long request_timeout_seconds = 172800;
    if (!timeout_raw.empty()) {
        try {
            request_timeout_seconds = std::stol(timeout_raw);
        } catch (const std::exception&) {
            std::cerr << "error: APP_REQUEST_TIMEOUT_SECONDS must be an integer (got "
                      << timeout_raw << ").\n";
            return 1;
        }
        if (request_timeout_seconds <= 0) {
            std::cerr << "error: APP_REQUEST_TIMEOUT_SECONDS must be positive.\n";
            return 1;
        }
    }

    if (bearer_token.empty()) {
        std::cerr << "error: AGENT_BEARER_TOKEN is not set. Refusing to send an unauthenticated "
                     "request. Set AGENT_BEARER_TOKEN (see .env.example / CONTRACTS.md).\n";
        return 1;
    }

    if (base_url.empty()) {
        std::cerr << "error: AGENT_BASE_URL is not set (e.g. http://agent:8090).\n";
        return 1;
    }

    const agent_client::AgentClient client(base_url, bearer_token, request_timeout_seconds);
    std::string conversation_id;

    if (repl) {
        std::cerr << "repl: type /quit to exit. Tokens stream as they arrive.\n";
        while (true) {
            std::cout << "> " << std::flush;
            if (!std::getline(std::cin, query)) {
                break;
            }
            while (!query.empty() && (query.back() == '\n' || query.back() == '\r')) {
                query.pop_back();
            }
            if (query.empty()) {
                continue;
            }
            if (query == "/quit" || query == "/exit") {
                break;
            }
            if (RunOneTurn(client, query, conversation_id, true) != 0) {
                return 1;
            }
        }
        return 0;
    }

    while (!query.empty() && (query.back() == '\n' || query.back() == '\r')) {
        query.pop_back();
    }
    if (query.empty()) {
        std::cerr << "error: prompt is empty.\n";
        return 2;
    }
    return RunOneTurn(client, query, conversation_id, stream);
}
