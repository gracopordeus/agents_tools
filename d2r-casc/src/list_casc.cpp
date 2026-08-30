#include "CascLib.h"

#include <iostream>
#include <string>

int main(int argc, char **argv) {
    if (argc < 2 || argc > 4) {
        std::cerr << "usage: list_casc STORAGE [MASK] [LISTFILE]\n";
        return 2;
    }
    const char *mask = argc >= 3 ? argv[2] : "*";
    const char *listfile = argc >= 4 ? argv[3] : nullptr;
    HANDLE storage = nullptr;
    if (!CascOpenStorage(argv[1], 0, &storage)) {
        std::cerr << "failed to open CASC storage: " << argv[1] << "\n";
        return 1;
    }
    CASC_FIND_DATA entry{};
    HANDLE finder = CascFindFirstFile(storage, mask, &entry, listfile);
    if (finder == INVALID_HANDLE_VALUE) {
        CascCloseStorage(storage);
        std::cerr << "no files found\n";
        return 1;
    }
    do {
        std::cout << entry.szFileName << '\t' << entry.FileSize << '\t'
                  << entry.bFileAvailable << '\n';
    } while (CascFindNextFile(finder, &entry));
    CascFindClose(finder);
    CascCloseStorage(storage);
    return 0;
}
