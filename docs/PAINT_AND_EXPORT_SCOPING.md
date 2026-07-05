# Scoping: paint in Blender, export back into a `.TLB`

**Status: Scenario A is built and verified. Scenario B is largely scoped but not built -
except for one specific, narrower case ("detach a face from a shared cell") which is now
built and verified - see below.**

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

Yes for Scenario A (built). Not yet for Scenario B. The `.TLB` format itself is fully
understood (see [TLB_FORMAT.md](TLB_FORMAT.md)) — writing one, if Scenario B is pursued
later, is a small, fixed-size binary layout, not a complex format.

### Scenario A — repainting within existing texture assignments (built)

If the paint session only recolors/reworks pixels within rectangles the model already
uses (the common "new camouflage/weathering" case), **the `.RRF` doesn't need to
change at all** — realizing this simplified the implementation a lot: the game's own
loader already prefers a `<name>_24.BMP` next to the `.TLB` over the paletted `_8.BMP`
fallback, so the export operator doesn't need to touch the `.TLB`'s binary contents at
all either. It just saves the current (possibly repainted) Image datablock back out as
a standard 24-bit BMP at the correct `256×4096` size.

`File > Export > Panzer Elite Texture Atlas (.bmp)` (operator id
`export_scene.pe_rrf_atlas` in `io_import_rrf.py`). Verified against a real atlas: paint
a region in Blender, export, and the output file is pixel-exact — painted pixels come
through correctly, untouched regions are unchanged, file size matches the expected
24-bit BMP formula exactly (`54 + width×height×3` bytes).

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

Scenario A is done and covers the most common modding use case (re-skinning an existing
vehicle) without touching mesh/UV data at all. Within Scenario B, "detach face from
shared cell" is done and covers a second real, if narrower, case. Full Scenario B (new
UV layouts for previously-unresolved or freehand-painted content) remains a candidate
follow-on once there's a concrete need for it.
