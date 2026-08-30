# D2R CASC visual export

Local, deterministic extraction of visual and character assets from the installed
Diablo II: Resurrected CASC storage. Extracted Blizzard assets are for local
reference/study only and are not redistributable.

```bash
./casc.sh build
./casc.sh export-visual /home/ggnp/tools/d2r-assets
```

`export-visual` first writes auditable manifests under `_manifests/`, then exports
classic sprites/tiles, remastered textures/models/animation descriptors, UI,
environment, item/object imagery, cinematics, and all classic/remastered character
trees. Locale variants listed by CASC but not installed locally are excluded using
the archive's availability flag. Pass `--manifest-only` after the destination to inspect scope without writing
the assets.

Override the defaults with `D2R_STORAGE`, `D2R_ASSETS`, or `D2R_LISTFILE`.
