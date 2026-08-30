#pragma once

#include <algorithm>
#include <cctype>
#include <filesystem>
#include <string>

namespace d2r {

inline std::string normalize(std::string value) {
    std::replace(value.begin(), value.end(), '\\', '/');
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char c) {
        return static_cast<char>(std::tolower(c));
    });
    return value;
}

inline bool safe_relative_path(const std::string &archive_name, std::filesystem::path &result) {
    std::string value = archive_name;
    std::replace(value.begin(), value.end(), '\\', '/');
    const auto colon = value.find(':');
    if (colon != std::string::npos) {
        value = value.substr(colon + 1);
    }
    while (!value.empty() && value.front() == '/') {
        value.erase(value.begin());
    }
    const std::filesystem::path candidate(value);
    if (candidate.empty() || candidate.is_absolute()) {
        return false;
    }
    for (const auto &part : candidate) {
        if (part == "..") {
            return false;
        }
    }
    result = candidate.lexically_normal();
    return true;
}

}  // namespace d2r
