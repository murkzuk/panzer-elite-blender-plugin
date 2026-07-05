# TODO / Backlog

Running list of things flagged during work sessions, not yet done. Newest first.

---

- [x] **"Detach face from shared texture cell" operator — done 2026-07-05.** Real models
  routinely reuse the exact same `.TLB` atlas rectangle across more than one face (the
  original artist's own space-saving choice — confirmed on a Panzer IV test model). Since
  painting acts on the shared atlas image, painting one face necessarily repaints every
  other face pointing at that same cell too. Confirmed this isn't a plugin bug: it's the
  same thing as "overlapping UVs" in modern DCC tools (Blender, Substance Painter, etc.
  all have this exact gotcha), and the real PE/ObjEdit tool would behave identically,
  since it's genuinely the same underlying pixels either way.

  Ruled out Blender's built-in Smart UV Project as a fix on its own — it has no awareness
  of which atlas regions are already used by other real `.TLB` entries (this model's own
  other faces, or unrelated vehicles sharing the same atlas), so it could just as easily
  relocate a UV onto someone else's texture.

  Shipped as `MESH_OT_pe_detach_face_texture` (Edit Mode face context menu, "PE: Detach
  Face From Shared Texture Cell") — select the face(s) sharing a cell with something else,
  run it, and each selected face gets its own private copy of the texture: finds free
  atlas space (`find_free_atlas_space()`), clones the current cell's pixels there via
  Blender's Image API, allocates a new `.TLB` entry (`append_tlb_entry()`), repoints the
  face's `textureOfset` (`patch_face_texture_id()`), and shifts that face's own UVs to the
  new cell. Writes directly to the model's `.RRF` and `.TLB`, with a one-time `.bak`
  backup of each made automatically before the first edit.

  Verified end-to-end on a real model (not a synthetic test) via the actual registered
  `bpy.ops` call, using an isolated scratch copy of the asset (never the live files):
  found two real faces on Pz4H's turret sharing one cell, selected only one, ran the
  operator, and confirmed all of - the selected face's texture id changed while the
  unselected sibling's didn't; a new `.TLB` entry appeared with the same size at a
  different, non-overlapping position while the original entry stayed byte-identical;
  the new cell's pixels exactly matched the old cell's pre-edit content while the old
  cell itself was untouched; the selected face's Blender UV shifted to the new cell while
  the sibling's UV didn't move; and both `.bak` backups were created correctly.

  This covered the same ground as "Scenario B" in
  [`docs/PAINT_AND_EXPORT_SCOPING.md`](docs/PAINT_AND_EXPORT_SCOPING.md) (new texture
  regions), scoped narrowly to "clone one face off its current shared cell" rather than
  general new-content painting - PAINT_AND_EXPORT_SCOPING.md still needs updating to
  reflect that this specific case is now built.

- [ ] **Give a whole vehicle its own private, freely-paintable skin — scoped 2026-07-05,
  not started. Bigger than "detach face"; needs real new work, not just running that
  operator in bulk.** User's goal: import a model with *every* library it actually uses
  (multi-`.TLB` auto-detect is now done, see below - was a real prerequisite for starting
  from a complete texture baseline, not a nice-to-have), then generate a brand-new,
  dedicated `.TLB` + atlas used by nothing else, laid out with Blender's own Smart UV
  Project so the whole model can be painted as a clean canvas with no risk of affecting
  any other vehicle.

  Ruled out one apparent blocker already: Smart UV Project being "unaware of other
  content" (the objection raised against using it for `MESH_OT_pe_detach_face_texture`)
  doesn't apply here - that objection was specifically about relocating a UV into an
  *existing, shared* atlas with other real vehicles' textures already in it. A **brand
  new, empty** `.TLB` has nothing else in it to collide with, so Smart UV Project is
  exactly the right tool for laying it out, and needs no manual UV skill to run (one
  operator call).

  The real new work is on the `.RRF`/`.TLB` side, not the UV-unwrap side. PE's texture
  assignment isn't a generic "one continuous atlas, arbitrary per-vertex UV" system like
  a modern engine - each face is assigned to exactly one `.TLB` entry (a rectangle), and
  its own crop *within* that entry is defined by up to 4 corner pixel offsets
  (`v1`/`v2`/`v3`/`textureHalf`, see [RRF_FORMAT.md](docs/RRF_FORMAT.md)) that are each a
  single unsigned byte (0-255, confirmed in `_corner_xy()`) - so **any one face's own
  visible crop is capped at 256×256 pixels**, though the *entry* it's assigned to can be
  bigger, with different faces carving out different sub-windows of the same shared
  entry via their own distinct offsets.

  Two possible shapes for the fix, in order of how well they preserve normal painting
  (being able to brush continuously across a real surface like a hull side, not paint
  disconnected postage stamps one at a time):

  1. **Simplest, but fragments the model**: reuse today's per-face allocation
     (`find_free_atlas_space()` + `append_tlb_entry()` + `patch_face_texture_id()`, all
     already built and shipped) for *every* face against the new dedicated library
     instead of the model's existing shared one. Buildable almost immediately from what
     exists, but every face becomes its own disconnected little rectangle - painting
     would jump discontinuously at every face boundary, a real usability problem for
     someone who (by their own description) isn't an experienced painter and needs
     forgiving, continuous surfaces to work with, not a mosaic of tiny independent tiles.
  2. **Real fix, not yet started**: group adjacent/connected faces into UV islands (Smart
     UV Project's own output already does this), pack each island into one shared `.TLB`
     entry sized to fit it, and compute *real* per-face corner offsets from each face's
     actual UV position within that island (not the existing "no crop data, use the
     entry's full rectangle" fallback, which only handles the all-zero-corners case, not
     genuine non-uniform per-vertex cropping). This needs a from-scratch "corners from
     real UV coordinates" writer that doesn't exist anywhere in the codebase yet, plus
     island-aware packing logic - the two genuinely new pieces of work here.

  Also needs: a fresh, blank/paintable Image datablock at the correct `256×4096` size for
  the new atlas (trivial - `bpy.data.images.new()` at that resolution), and a check that a
  whole vehicle's worth of unique surfaces actually fits in that fixed canvas size (should
  have plenty of headroom for one vehicle, but not verified against a real model yet).

- [x] **Auto-detect now tries every library that helps, not just the single
  best-scoring one — done 2026-07-05.** Models that genuinely draw from several
  libraries at once used to resolve far fewer faces without a `.RRI` present - a Tiger1
  with a `.RRI` listing 9 libraries resolved 94% via the `.RRI`, but only 21% via
  auto-detect (which only tried the single best-scoring library, finding just 1 of the 9).

  Fixed with `find_matching_tlbs()`: scores every `.TLB` in the folder the same way as
  before (noise-floor-vs-real-match threshold, unrelated libraries score single digits,
  real matches score well above that), then greedily keeps adding qualifying libraries in
  score order as long as each one resolves at least one id nothing already-added covers -
  skips near-duplicate map variants that would only re-cover the same ids.

  Verified via the real `bpy.ops.import_scene.pe_rrf` call across every model in the
  asset set: `Pz4H_3.RRF` (picked up 2 more libraries) and `PantherG2.RRF` (1 more) both
  went from already-good (91.0%/99.8%) to fully resolved (100%/100%), and Tiger1 improved
  21%→27% (picked up a real second library, though most of its `.RRI`'s 9 never score
  high enough above the noise floor on their own to be trusted as auto-detect matches).
  Zero regression on every model that only ever needed one library - identical results
  to before. This was also a real prerequisite for the "private skin" item above - can't
  start from a complete texture baseline if most of a model's real textures were never
  found in the first place.

  **A `.RRI` file is still the better answer when one exists** - it's the authoritative
  exact list, not a scored guess (see [TEXTURE_ID_RESOLUTION.md](docs/TEXTURE_ID_RESOLUTION.md)).

- [ ] **Repaint export path untested against the real game/ObjEdit.** Only checked so far
  via an automated pixel-comparison test inside Blender (painted regions match, untouched
  regions match, correct format/size). Nobody has loaded an actual exported `_24.BMP` in
  ObjEdit or the game yet to confirm it's accepted and displays correctly.

- [ ] **Some texture placement issues still being tracked down.** Reported after the
  geometry/pivot fixes landed — "model is now accurate" but "still some odd texture
  issues." Not yet reproduced with a specific screenshot/model to diagnose.
