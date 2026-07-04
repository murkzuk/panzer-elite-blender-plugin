# `.TLB` texture library format

A `.TLB` file is a directory of named texture "parts" (rectangular sub-regions) packed
into one shared atlas bitmap, plus a palette. It's always exactly **461,064 bytes**
regardless of content.

## Layout

```
offset 0     libNextID      int32   - running ID counter (next ID to be assigned)
offset 4     libEntryCount  int32   - number of populated entries
offset 8     libPal         2048 bytes  - palette, 4 bytes/entry (R,G,B,flags)
offset 2056  libMatPal      256 bytes   - 1 byte/palette-index, material-type tag
offset 2312  libParts[4096] 112 bytes each, fixed-size array (always all 4096 slots
                            present in the file; only the first libEntryCount matter)
```

`8 + 2048 + 256 + 4096×112 = 461,064` — exact for every `.TLB` file checked.

### Part entry (112 bytes)

| offset | field | type |
|---|---|---|
| 0 | id | int32 — see [TEXTURE_ID_RESOLUTION.md](TEXTURE_ID_RESOLUTION.md) |
| 4 | filename | char[80] — original source bitmap path (author-time only, often a stale absolute path from whoever painted it) |
| 84 | cutX | int32 — crop X in the *original* source bitmap (not the packed atlas) |
| 88 | cutY | int32 |
| 92 | sizeX | int32 — width in pixels |
| 96 | sizeY | int32 — height in pixels |
| 100 | posX | int32 — packed-atlas tile-grid X (× 16 = pixel X) |
| 104 | posY | int32 — packed-atlas tile-grid Y (× 16 = pixel Y) |
| 108 | (unused) | 4 bytes — an editor-only in-memory pointer field, meaningless on disk |

## The shared atlas bitmap

Every `.TLB` has a companion bitmap with the same base name: `<name>_24.BMP` (24-bit
truecolor) and/or `<name>_8.BMP` (8-bit paletted). Both are **plain, standard Windows
BMP files** — no proprietary tiling or swizzling, directly croppable with any image
library.

**The atlas is always exactly 256×4096 pixels**, not square. This is easy to get wrong:
`256 × 4096 = 1024 × 1024` in total pixel count, so file size alone can't distinguish the
two — confirmed by reading the actual BMP header dimensions, and cross-checked against
the `16`-wide × `256`-tall tile-grid constants used by the original editor
(`16 × 16px = 256`, `256 × 16px = 4096`).

To get a part's actual pixels: crop the atlas bitmap at
`(posX × 16, posY × 16, sizeX, sizeY)`.
