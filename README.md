# Panzer Elite (1999) Blender Importer

**[Download the latest release](https://github.com/murkzuk/panzer-elite-blender-plugin/releases/latest)**
(zip or the plain `.py` file, either works) — then in Blender: Edit > Preferences >
Add-ons > Install from Disk, pick the file you downloaded, and tick the checkbox to
enable it. Requires Blender 3.6+.

A Blender add-on for importing Panzer Elite `.RRF` 3D model files — geometry, part
hierarchy, pivots, gameplay attribute tags, and (where recoverable) UVs/textures from the
game's `.TLB` texture library format — plus exporting a repainted texture atlas back out
for re-use in the game.

The format layouts documented here were worked out primarily by direct inspection of
shipped game data (`.RRF`/`.TLB`/`.RRI` files) and a live paint-and-save test against the
original `ObjEdit` tool, informed in places by limited excerpts of the original codebase
the author has partial, legitimate access to. **This repository does not include any
original Panzer Elite/ObjEdit source code, in full or in part** — only the resulting
format documentation, written in original wording, and a newly-written Python importer
built from that documentation.

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

Import: geometry and part hierarchy with correct pivots are working and verified against
real models. UV/texture resolution via a `.RRI` sidecar (when present) or best-effort
auto-detection is largely working, but some texture placement issues are still being
tracked down.

Export (Scenario A — repainting existing texture assignments): File > Export > Panzer
Elite Texture Atlas (.bmp) saves a painted-on Image datablock back out as a 24-bit BMP.
Checked so far only via an automated pixel comparison of the round-tripped file (painted
regions match, untouched regions match, correct format/size) — **not yet tested by
loading an export in the real game or ObjEdit.** Scenario B (new texture regions / new
`.TLB` entries) is scoped but not built — see the scoping doc.

## Requirements

Blender 3.6+. Install via Edit > Preferences > Add-ons > Install from Disk, pointing at
`io_import_rrf.py`.
