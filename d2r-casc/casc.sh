#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
STORAGE="${D2R_STORAGE:-/home/ggnp/Games/Diablo II - Resurrected}"
OUTPUT="${D2R_ASSETS:-/home/ggnp/tools/d2r-assets}"

case "${1:-}" in
  build)
    cmake -S "$ROOT" -B "$ROOT/build" -DCMAKE_BUILD_TYPE=Release
    cmake --build "$ROOT/build" --parallel
    ;;
  list)
    shift
    "$ROOT/build/list_casc" "$STORAGE" "${1:-*}" "${D2R_LISTFILE:-$ROOT/CascLib/listfile/listfile.txt}"
    ;;
  extract)
    shift
    "$ROOT/build/extract_d2r" "$STORAGE" "${1:?destination required}" "${2:?manifest required}"
    ;;
  export-visual)
    shift
    python3 "$ROOT/export_visual_assets.py" --storage "$STORAGE" --output "${1:-$OUTPUT}" --listfile "${D2R_LISTFILE:-$ROOT/CascLib/listfile/listfile.txt}" "${@:2}"
    ;;
  *)
    echo "usage: $0 {build|list [mask]|extract DEST MANIFEST|export-visual [DEST] [--manifest-only]}" >&2
    exit 2
    ;;
esac
