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
| 108 | (unused) | 4 bytes — an editor-only in-memory pointer field, meaningless to interpret |

**Two real wrinkles found while building a writer for this format** (both required for a
byte-exact round trip, neither obvious just from reading the format):

- The offset-108 "unused" field is **not always zero on disk** - real files have
  non-zero leftover bytes there (an in-memory pointer the editor apparently never clears
  before saving). Meaningless to interpret either way, but a writer that zeroes it will
  produce a file that differs from a re-saved original.
- **Slots beyond `libEntryCount` aren't zeroed either.** Confirmed on a real file
  (`libEntryCount=75`) with non-zero, structured-looking data (a real crop rectangle and
  reserved bytes) sitting in slot 75 onward - almost certainly a deleted/replaced entry's
  leftover data that the editor never bothered clearing when the count shrank. A writer
  that only reconstructs the first `libEntryCount` slots and zero-fills the rest will
  produce a file that differs from the original in exactly this range.
- **Entry `id` values are not guaranteed to fit in `[0, 4096)`**, despite every ordinary
  entry doing so. Confirmed on a real library: 2 of 275 entries carry an id in the
  millions, with a real, legitimate source filename pointing at a *different* library's
  temp path - clearly content copied in from another `.TLB` at author time, keeping its
  original id rather than being renumbered. These entries can never actually be resolved
  by any face's `texture_id` (the modulo lookup in
  [TEXTURE_ID_RESOLUTION.md](TEXTURE_ID_RESOLUTION.md) always reduces to a candidate
  under 4096), but they're real on-disk content, not corruption, and a writer must
  round-trip them untouched rather than assuming the field is always small.
- **One real file (`_Normandy7.TLB`) doesn't match the expected 461,064-byte size at
  all** - a completely normal-looking header and entry table, followed by ~3.1MB of
  repeating junk bytes appended after the real structure. The leading underscore matches
  a "disabled/deprecated asset" naming convention seen elsewhere in this asset set, so
  this is a leftover corrupted/superseded file, not a genuine format variant to support.

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
