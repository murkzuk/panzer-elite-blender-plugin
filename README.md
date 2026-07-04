# Panzer Elite (1999) Blender Importer

A Blender add-on for importing Panzer Elite `.RRF` 3D model files — geometry, part
hierarchy, pivots, gameplay attribute tags, and (where recoverable) UVs/textures from the
game's `.TLB` texture library format — plus exporting a repainted texture atlas back out
for re-use in the game.

This project is independent, clean-room reverse-engineering work: format layouts below
were derived by direct inspection of shipped game data (`.RRF`/`.TLB`/`.RRI` files) and,
where necessary, cross-checked against a live paint-and-save test in the original
`ObjEdit` tool. **No original Panzer Elite/ObjEdit source code is included in this
repository** — only the format documentation and the original Python importer written
from that documentation.

## Contents

- [`io_import_rrf.py`](io_import_rrf.py) — the Blender add-on itself
- [`docs/RRF_FORMAT.md`](docs/RRF_FORMAT.md) — the `.RRF` model geometry format
- [`docs/TLB_FORMAT.md`](docs/TLB_FORMAT.md) — the `.TLB` texture library format
- [`docs/RRI_FORMAT.md`](docs/RRI_FORMAT.md) — the `.RRI` library-list sidecar format
- [`docs/TEXTURE_ID_RESOLUTION.md`](docs/TEXTURE_ID_RESOLUTION.md) — how a face's texture
  reference resolves to actual pixels, and the real limitation that means some faces on
  older/heavily-edited models can't be resolved at all
- [`docs/PLUGIN_USAGE.md`](docs/PLUGIN_USAGE.md) — how to use the importer, current
  capabilities and known limitations
- [`docs/PAINT_AND_EXPORT_SCOPING.md`](docs/PAINT_AND_EXPORT_SCOPING.md) — feasibility
  study for a Texture-Paint-in-Blender → repack-to-`.TLB` workflow, and what's built vs.
  still open

## Status

Import is working: geometry, part hierarchy with correct pivots, UVs, and texture
resolution via a `.RRI` sidecar (when present) or best-effort auto-detection otherwise.

Export (Scenario A — repainting existing texture assignments) is working: File > Export
> Panzer Elite Texture Atlas (.bmp) saves a painted-on Image datablock back out as a
24-bit BMP the game/ObjEdit will load. Verified pixel-exact against a real atlas.
Scenario B (new texture regions / new `.TLB` entries) is scoped but not built — see the
scoping doc.

## Requirements

Blender 3.6+. Install via Edit > Preferences > Add-ons > Install from Disk, pointing at
`io_import_rrf.py`.
