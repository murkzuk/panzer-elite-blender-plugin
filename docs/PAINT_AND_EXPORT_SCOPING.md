# Scoping: paint in Blender, export back into a `.TLB`

**Status: Scenario A is built and fixed as of 2026-07-05 - it now writes an 8-bit
indexed `_8.BMP` (the format confirmed to actually work against a real running
install), quantized against the model's own real `.TLB`'s palette, instead of the
earlier `_24.BMP` approach that a real in-game test proved the engine ignores. See
"Scenario A" below for the full story: two independent real-game tests that falsified
the old assumption, and the byte-level verification of the new writer (correct row
order, exact round-trip on untouched pixels, correct BMP structure). Scenario B is
largely scoped but not built - except for one specific, narrower case ("detach a face
from a shared cell") which is built and verified - see below.**

## Can models be painted in Blender's Texture Paint workspace?

Yes, for the portion of a model that already resolved a texture on import. Blender's
Texture Paint mode needs two things per face: a UV coordinate and an active Image to
paint onto — both already exist for resolved faces, since import assigns real UVs into
the shared atlas bitmap and a material referencing that same bitmap as an Image
datablock. Switching to the Texture Paint workspace and painting directly on the model
should work today, with one important caveat below.

Faces flagged magenta (unresolved — see
[TEXTURE_ID_RESOLUTION.md](TEXTURE_ID_RESOLUTION.md)) have no meaningful UV assigned and
would need a manual UV unwrap pass before they could be painted sensibly — they're not
ready for paint out of the box.

### The shared-atlas caveat

Each `.TLB`'s atlas bitmap is a single 256×4096 image shared across **every** vehicle or
object that uses any part of that library — not one image per model. Painting directly
on the image datablock as imported would affect every other model that references any
other rectangle in that same atlas the next time it's exported. Any paint workflow needs
to either:

- work on a duplicated copy of the image, later composited back into just the specific
  rectangle(s) that changed, or
- constrain painting to the exact UV island(s) belonging to the model being edited, with
  the export step only touching those pixels.

## Can the plugin export newly-painted textures back into a `.TLB`?

Yes for Scenario A (built, and fixed to target the format confirmed to actually work -
see below). Not yet for Scenario B. The `.TLB` format itself is fully understood (see
[TLB_FORMAT.md](TLB_FORMAT.md)) — writing one, if Scenario B is pursued later, is a
small, fixed-size binary layout, not a complex format.

### Scenario A — repainting within existing texture assignments (built and fixed)

If the paint session only recolors/reworks pixels within rectangles the model already
uses (the common "new camouflage/weathering" case), **the `.RRF` doesn't need to
change at all**, and neither does the `.TLB`'s binary structure - only the atlas bitmap
itself changes.

**What the first version got wrong.** The original assumption was that the game's own
loader prefers a `<name>_24.BMP` next to the `.TLB` over the paletted `_8.BMP`
fallback, so exporting just meant saving the repainted Image datablock as a plain
24-bit BMP. That export mechanism was sound - pixel-exact output, correct file size -
but the underlying assumption was wrong, confirmed against a real running install,
twice, independently:

1. `Pz4.TLB` already ships with a real, pre-existing `Pz4_24.bmp` in active use (proof
   the mechanism is real *somewhere* in this format's history) - painting an unmissable
   mark into it and loading the exact vehicle/mission that uses it showed no trace of
   the mark in-game.
2. A second, independently-identified vehicle (`PantherG`, using `CustomA9.TLB` -
   confirmed via a genuine, pre-existing `.RRI` sidecar and a 79% face-texture-id
   resolution rate, not a guess) got the same test: paint a mark, export as
   `CustomA9_24.bmp`, load the real mission the vehicle spawns in at point-blank range.
   No trace of the mark either time.

Both tests used a real, ground-truth-confirmed library (not an auto-detect guess - see
the false starts below), a real playable mission, and a vehicle either already visible
or spawned within visual range of the player. Neither showed any effect from the painted
`_24.BMP`, with no crash and normal rendering otherwise - the game silently kept reading
the original `_8.BMP` content. This matches an account from historical PEDG community
discussion: vanilla PE's renderer "did not use the 24 bit file" - only a separate,
hardware-accelerated code mod ("PEx") reads it, and even then would crash on a
`_24.BMP` that isn't genuinely 24-bit. The real install this was tested against is very
likely running without that specific code mod's texture-loading behavior active.

**The fix**: `File > Export > Panzer Elite Texture Atlas (.bmp)` (operator id
`export_scene.pe_rrf_atlas` in `io_import_rrf.py`) now writes an 8-bit indexed
`<name>_8.BMP` instead - the format actually confirmed to work. It reads the model's
real, currently-live `_8.BMP` fresh off disk (via the Image's `pe_tlb_filepath` custom
property → `find_source_bmp8()`) to get its exact 256-color palette, quantizes the
repainted RGB pixels against that same palette (`quantize_to_palette()` - plain nearest-
Euclidean-distance, no dithering), and writes a byte-correct 8-bit BMP
(`write_bmp8()`). Repainted colors that don't already exist in the fixed 256-entry
palette land on their closest available match - an unavoidable consequence of the
paletted format the game actually reads, not a bug.

Verified byte-level, not just via Blender's own reimport (which could mask a row-order
or quantization bug): an untouched pixel round-trips to the *exact* same palette index
its original color already mapped to (zero drift), two deliberately-painted marks at
opposite ends of the atlas (one near the bottom, one near the top) land at the correct
rows with the correct colors, and the output file's header (signature, data offset,
dimensions, bit depth) matches every real `_8.BMP` checked exactly. The row-order
detail mattered enough to verify explicitly: Blender's `Image.pixels` buffer is
bottom-up (index 0 = v=0, the bottom row) and a positive-height BMP is *also* stored
bottom-up on disk, so the two already agree with no reversal needed - reversing would
have silently flipped every exported atlas upside down, which the two-mark test would
have caught immediately (top and bottom colors would come out swapped).

**False starts along the way, worth remembering**: auto-detect's scoring heuristic
picked the wrong library twice before a real `.RRI` (or manually verified resolution
rate) gave the right answer - once for a vehicle with few, mostly-generic texture IDs
(scoring tied across 8 different libraries, all wrong), and once for a vehicle
(`PantherG`) that turned out to have a per-unit skin override (`Modification0` not the
default `255`) pointing at a completely different library than its generic siblings.
Always check for a non-default `Modification0` before trusting a "closest to player
spawn" unit as representative of the plain, unmodified vehicle.

**Not yet done**: an actual in-game confirmation of the new `_8.BMP` writer specifically
(the byte-level verification above is thorough, but nobody has loaded a real exported
`_8.BMP` in the actual game yet to see a repainted vehicle in the flesh - worth doing
before calling Scenario A fully closed).

### Scenario B — new texture regions (largely still not built)

Adding genuinely new painted content (a new part variant, or giving previously-magenta
unresolved faces a real texture for the first time) requires:

1. Finding free space in the atlas's tile-packing grid for a new rectangle. **Built** —
   `find_free_atlas_space()`.
2. Allocating a fresh `.TLB` entry (`id`, `posX/posY`, `sizeX/sizeY`) — `libNextID`
   tracks the next available ID. **Built** — `append_tlb_entry()`.
3. Writing that rectangle's pixel data into the atlas. **Built** for the "clone an
   existing rectangle's pixels" case (`_copy_atlas_region()`) - not built for genuinely
   new, freehand-painted content with no existing source rectangle.
4. **Updating the `.RRF` face records** for the affected faces: `textureOfset` needs the
   new ID encoded with the correct top-bit/slot convention. **Built** for repointing an
   *existing* face at a *different* entry of the *same* crop size
   (`patch_face_texture_id()`) - not built for genuinely new UV corner layouts (`v1`/
   `v2`/`v3`/`textureHalf`, format documented in [RRF_FORMAT.md](RRF_FORMAT.md)), which
   would need a real UV-unwrap-to-crop-rectangle step that doesn't exist yet.

**What this combination of built pieces actually covers**: "detach a face from a shared
texture cell" (see `TODO.md`) - giving an existing face, whose UVs and crop rectangle are
already valid, its own independent copy of the *same* content so it stops sharing a cell
with unrelated faces. Shipped as `MESH_OT_pe_detach_face_texture` (Edit Mode face context
menu). Verified end-to-end on a real model: detaching one of two faces sharing a cell
changes only the selected face's texture reference and UV, leaves the sibling and the
original entry completely untouched, and the cloned cell's pixels match exactly.

**What's still not covered**: assigning a *previously-unresolved* (magenta) face a real
texture for the first time, or painting content that doesn't already exist as a croppable
rectangle somewhere. Both of those need real UV unwrapping into a newly-allocated
rectangle - a materially bigger piece of work than reusing an existing crop, and not
started.

## Recommendation

Scenario A now writes the format confirmed to actually work (`_8.BMP`, palette-
quantized) instead of the `_24.BMP` a real in-game test proved the engine ignores -
re-skinning an existing vehicle should now actually reach the game, pending the
in-game confirmation noted above. Within Scenario B, "detach face from shared cell"
writes its `.RRF`/`.TLB` structural changes (new entry, repointed face) directly to
disk, independent of this whole question - but making the cloned cell's actual pixels
visible in-game still requires a separate atlas export afterward
(`MESH_OT_pe_detach_face_texture` has no `image.save()` call of its own), which now
goes through the same fixed `_8.BMP` writer as Scenario A rather than the broken
`_24.BMP` one. Full Scenario B (new UV layouts for previously-unresolved or
freehand-painted content) remains a candidate follow-on once there's a concrete need
for it.
