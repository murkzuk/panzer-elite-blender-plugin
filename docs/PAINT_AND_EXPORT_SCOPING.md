# Scoping: paint in Blender, export back into a `.TLB`

**Status: scoped, not yet built.**

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

Not yet, but the format is fully understood (see
[TLB_FORMAT.md](TLB_FORMAT.md)) and writing one is straightforward — it's a small,
fixed-size binary layout, not a complex format. Two different scenarios, with very
different amounts of work:

### Scenario A — repainting within existing texture assignments (straightforward)

If the paint session only recolors/reworks pixels within rectangles the model already
uses (the common "new camouflage/weathering" case), **the `.RRF` doesn't need to
change at all.** Only the `.TLB`'s companion atlas bitmap needs updating:

1. Read back the current pixel data from Blender's Image datablock for the painted
   region(s).
2. Write those pixels into the correct rectangle of the atlas bitmap (`posX×16,
   posY×16, sizeX, sizeY` from the `.TLB` entry).
3. Save the updated `_8.BMP`/`_24.BMP` (and re-derive the 8-bit palette version from the
   24-bit if the artist painted in truecolor).

This is the practical near-term feature to build — no new IDs, no UV repacking, no
`.RRF` changes.

### Scenario B — new texture regions (bigger job)

Adding genuinely new painted content (a new part variant, or giving previously-magenta
unresolved faces a real texture for the first time) requires:

1. Finding free space in the atlas's tile-packing grid for a new rectangle.
2. Allocating a fresh `.TLB` entry (`id`, `posX/posY`, `sizeX/sizeY`) — `libNextID`
   tracks the next available ID.
3. Writing that rectangle's pixel data into the atlas.
4. **Updating the `.RRF` face records** for the affected faces: `textureOfset` needs the
   new ID encoded with the correct top-bit/slot convention, and `v1`/`v2`/`v3`/
   `textureHalf` need the UV corner bytes rewritten to point at the new rectangle
   (format documented in [RRF_FORMAT.md](RRF_FORMAT.md)).

This is materially bigger — closer to a small texture-atlas packer plus a `.RRF` mesh
editor — and would be its own follow-on project, not a quick addition.

## Recommendation

Build Scenario A first: a "bake painted image back into `.TLB`" export operator. It
covers the most common modding use case (re-skinning an existing vehicle) without
touching mesh/UV data at all, and is a well-bounded, achievable piece of work given the
format is already fully decoded. Scenario B can follow once there's a concrete need for
genuinely new texture layouts rather than repainting existing ones.
