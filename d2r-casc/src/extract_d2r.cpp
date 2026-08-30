#include "CascLib.h"
#include "common.hpp"

#include <array>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>

namespace fs = std::filesystem;

static bool extract(HANDLE storage, const std::string &name, const fs::path &destination) {
    fs::path relative;
    if (!d2r::safe_relative_path(name, relative)) {
        std::cerr << "unsafe archive path: " << name << '\n';
        return false;
    }
    HANDLE file = nullptr;
    if (!CascOpenFile(storage, name.c_str(), 0, 0, &file)) {
        std::cerr << "cannot open: " << name << '\n';
        return false;
    }
    fs::path output = destination / relative;
    std::error_code error;
    fs::create_directories(output.parent_path(), error);
    std::ofstream stream(output, std::ios::binary);
    if (error || !stream) {
        std::cerr << "cannot create: " << output << '\n';
        CascCloseFile(file);
        return false;
    }
    std::array<char, 1024 * 1024> buffer{};
    DWORD bytes_read = 0;
    bool ok = true;
    do {
        if (!CascReadFile(file, buffer.data(), static_cast<DWORD>(buffer.size()), &bytes_read)) {
            ok = false;
            break;
        }
        stream.write(buffer.data(), bytes_read);
    } while (bytes_read != 0);
    CascCloseFile(file);
    if (!ok || !stream) {
        stream.close();
        fs::remove(output, error);
        std::cerr << "read failed: " << name << '\n';
        return false;
    }
    return true;
}

int main(int argc, char **argv) {
    if (argc != 4) {
        std::cerr << "usage: extract_d2r STORAGE DESTINATION MANIFEST\n";
        return 2;
    }
    std::ifstream manifest(argv[3]);
    if (!manifest) {
        std::cerr << "cannot read manifest: " << argv[3] << '\n';
        return 2;
    }
    HANDLE storage = nullptr;
    if (!CascOpenStorage(argv[1], 0, &storage)) {
        std::cerr << "failed to open CASC storage: " << argv[1] << '\n';
        return 1;
    }
    std::size_t total = 0;
    std::size_t failed = 0;
    std::string name;
    while (std::getline(manifest, name)) {
        if (name.empty() || name.front() == '#') {
            continue;
        }
        ++total;
        if (!extract(storage, name, argv[2])) {
            ++failed;
        }
        if (total % 1000 == 0) {
            std::cerr << "extracted " << (total - failed) << '/' << total << "\n";
        }
    }
    CascCloseStorage(storage);
    std::cerr << "complete: " << (total - failed) << " extracted, " << failed << " failed\n";
    return failed == 0 ? 0 : 1;
}
