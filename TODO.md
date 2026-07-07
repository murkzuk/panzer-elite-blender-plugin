# TODO / Backlog

Running list of things flagged during work sessions, not yet done. Newest first.

---

- [x] **`.RRF` geometry writer Phase 2, "delete faces" case — re-scoped from real engine
  source AND built/verified/real-tool-confirmed, all 2026-07-08.** Phase 2 was previously
  blocked on two total unknowns (`sortList`, `attribVList` - see the Phase 1 entry below).
  Both got resolved by finding and reading the real source that defines them, not just
  data analysis:

  - **`sortList`**: confirmed in `rrobjpex\Rrdraw.c` (`rrDirectionToSortListNo()`,
    `rrCalcSortDirection()`) plus the `SORT_XSMALL`/`SORT_YSMALL`/`SORT_ZSMALL` constants
    (`Headers\SCENE.H`, also independently defined in `rrobjpex\Tank.c`) - the 8 blocks are
    the 8 octants of 3D space. Normalizing the empirical correlation results for the
    ascending/descending sign ambiguity revealed a genuine **closed-form recipe**, the
    same across every real part tested: block index bit 0/1/2 directly encodes that axis's
    sort-direction sign (1=positive, 0=negative), and each block sorts its faces by
    ascending centroid depth along that direction - implemented as `compute_sort_list()`.
    Empirically strong (Spearman's ρ 0.85-0.985 on every real part checked) but not proven
    byte-exact (per-block exact match ~7-19%, vs. ~1% random-chance baseline).
  - **`attribVList`**: confirmed in `Rrdwire.c` (the same face-subdivision function
    RRF_FORMAT.md's own corner-encoding facts came from) - it's read per-corner-vertex and
    passed through `rrCalcAttribList(sx,sy,va1,va2,va3,va4,newAttribVList)`, interpolated
    across a new subdivision grid exactly like vertex position/normal in the same function.
    A genuine interpolatable per-vertex value tied to a face-splitting/tessellation
    feature, not a flag - Phase 2 preserves existing values and zero-fills new ones without
    needing to know what the value actually represents.
  - **Real memory layout, also confirmed via offset-gap analysis across real files**: a
    part's LOD0 mesh region is `faceList → faceNormList → vertexList → vertexNormList →
    sortList → attribVList`, contiguous, zero padding, `faceNormList`/`vertexNormList`
    entries 12 bytes each (same 3×int32 convention as vertices) - never measured before.
    All 8 LOD slots in every real part are exact duplicates of LOD0's fields.

  **Built and shipped**: `compute_sort_list()`, `_region_size()`, `_pack_face_record()`,
  `rebuild_part_mesh_region()` in `io_import_rrf.py` - the general resize/rebuild/
  offset-shift machinery, wired into `MESH_OT_pe_delete_faces` ("PE: Delete Face(s) (write
  to .RRF)", Edit Mode mesh context menu, v0.10.0). Two new per-element tracking
  attributes stamped at import (`pe_face_index`/`pe_vertex_index`) let a surviving face/
  vertex's real original texture/UV/attribVList data be found again after Blender's own
  indices change from a delete. Verified on real files (`PantherG.RRF`): a simple 1-face
  delete (byte-exact 52-byte file shrink, every surviving face's texture id unchanged, the
  part before it untouched, the part after it correctly shifted), a harder 4-face/
  6-orphaned-vertex delete on the 122-face hull (all 8 new `sortList` blocks confirmed
  valid permutations, 0 unresolved faces on re-import), and finally a real visual
  confirmation in the user's own ObjEdit build (deleted 56 of 84 faces on the gun barrel -
  loaded with no crash, barrel rendered correctly truncated, rest of the model normal).

  **"Add new faces" is a separate, unbuilt follow-on** - not a format blocker, but a real
  design gap: a genuinely new face has no existing texture assignment to read, and this
  plugin's materials each represent a whole `.TLB` library, not one specific atlas
  rectangle, so material+UV alone can't tell you which crop a new face should show. Needs
  real UV-island-to-atlas allocation, most likely reusing `plan_private_skin()`/
  `apply_private_skin()`'s own machinery rather than inventing something new.

  Full write-up (including the closed-form `sortList` derivation and the build report) is
  in [docs/RRF_WRITER_SCOPING.md](docs/RRF_WRITER_SCOPING.md).

- [x] **Full `.RRF` geometry writer — scoped 2026-07-08. Phase 1 (reposition existing
  vertices, same topology) BUILT, verified, and real-tool-confirmed the same day.** The last major piece needed
  for real OE parity on geometry (not just texturing). Full scoping (3-phase plan:
  reposition existing vertices only → add/remove faces within a part → add/remove whole
  parts/hierarchy edits) is in
  [docs/RRF_WRITER_SCOPING.md](docs/RRF_WRITER_SCOPING.md).

  Before writing any code, ran the cheap prerequisite check the scoping doc called for:
  surveyed all 5,166 real `.RRF` files under `L:\Panzer Elite Ostpak3\` (33,023 parts) to
  see whether per-part `maxVertex` ever differs from the actual LOD0 `vertexCount` —
  **zero mismatches**. `maxVertex` is just a duplicate of `vertexCount`, not a separate
  pre-allocated capacity, removing one of the two unknowns blocking Phase 2/3.

  `read_vertex_position()`/`patch_vertex_position()` in `io_import_rrf.py` mirror the
  existing `patch_face_texture_id()`/`patch_face_corners()` surgical-patch pattern exactly
  (a `_vertex_record_offset()` helper, re-read fresh from the buffer on every call).
  `MESH_OT_pe_write_vertex_positions` ("PE: Write Vertex Positions", Edit Mode mesh context
  menu, v0.9.0) wires this to a real operator - refuses to run if the vertex count changed,
  converts Blender-local mesh coordinates back to the file's raw convention (root part
  needs its pivot added back; every other part's mesh data is already identical to the raw
  file value), using a new `pe_pivot` custom property stamped on every object at import
  time (not the object's own possibly-since-moved `obj.location`).

  Verified on a real file (`PantherG.RRF`, scratch copy): byte-level (patched one vertex
  each on the root part and a non-root part in memory, full-file diff confirmed only those
  two 12-byte records changed anywhere), the real `bpy.ops.mesh.pe_write_vertex_positions()`
  operator end to end on both a root and non-root part, and a fresh re-import showing the
  moved vertex at exactly the new position while every other vertex on both parts (229 +
  9) re-imported byte-for-byte identical to the pristine original.

  **Real-tool confirmation, done 2026-07-08**: loaded a deliberately exaggerated test (one
  hull vertex moved straight up 3 units) in the user's real, working ObjEdit build
  (`PEx_105_ObjEdit.exe`) - rendered as a clean, isolated, correctly-placed spike with the
  rest of the model completely intact, no crash, no wider distortion. Two real environment
  gotchas hit and fixed along the way (worth remembering for future native-tool testing):
  launching without an explicit working directory made the exe inherit the launcher's cwd
  instead of its own install folder, so it couldn't find its own `MTYPE.DAT` and crashed in
  its renderer DLL on the resulting null config; and this build doesn't support a file path
  passed as a command-line argument at all (a Delphi "Range check error" plus another
  access violation even with the cwd fixed) - the real fix was launching with no arguments
  and using the app's own File > Open dialog. Phase 1 is now closed end to end. Phases 2
  and 3 not started - see RRF_WRITER_SCOPING.md's updated recommendation for what Phase 2
  needs to investigate first (`sortList` behavior once face *count* changes, not just
  vertex position).


- [x] **Give a whole vehicle its own private, freely-paintable skin — done 2026-07-07
  (the item below, "scoped 2026-07-05, not started," is now built).** Full pipeline,
  wired into a real operator:

  `detect_uv_islands()` groups faces into UV islands by connectivity - a mesh edge only
  links two faces if their UV also matches there, so an unwrap seam (which breaks UV
  continuity) correctly becomes an island boundary rather than merging the whole model
  into one blob. `size_islands_to_tiles()` sizes each island proportional to its own UV
  footprint, clamped to this format's 256×256 per-face-crop cap (an engineering choice,
  not a reverse-engineered fact - documented as such in its own docstring).
  `pack_islands_shelf()` packs the sized islands into a fresh, empty atlas via simple
  shelf packing (not space-optimal, but simple and correct - a real 2D bin-packer would
  be a reasonable future upgrade if packing density ever becomes a real problem).

  `plan_private_skin()`/`apply_private_skin()` wire all of that to the corner writer
  from the entry above: every face in an island gets a new `.TLB` entry sized to fit it
  and a real per-face crop computed from its actual UV position
  (`patch_face_corners()`), not the all-zero fallback every previous writer used, and
  its Blender-side UV is remapped to match the packed position.

  `MESH_OT_pe_give_private_skin` ("PE: Give This Part a Private Skin") runs the whole
  thing as one operator call: given a mesh already unwrapped (Smart UV Project or any
  other), it writes a new dedicated `<name>_private.TLB` and a blank
  `<name>_private_8.BMP` (borrows the part's own real palette rather than guessing one),
  updates the `.RRF` in place with the usual automatic `.bak` backup, and assigns a
  ready-to-paint material - no re-import needed to start painting.

  Verified at every layer against real files, not just synthetically: the packing/sizing
  algorithms unit-tested standalone (no overlaps, correct proportionality, correctly
  raises rather than silently truncating when something doesn't fit), island detection
  on a real 122-face PantherG part after a real Smart UV Project unwrap (31 islands,
  proper partition of every face, zero overlaps), the full plan+apply pipeline with a
  real new `.TLB`/`.BMP` pair written and successfully re-imported, and finally the
  actual `bpy.ops.mesh.pe_give_private_skin()` operator end to end (poll, real execute,
  correct material/image/file/backup state afterward).

  **Known, documented scope limits, not silent gaps**: one mesh part (one Blender
  object) at a time - these models are already split into one object per `.RRF` part,
  so giving a whole multi-part vehicle a full private skin means running this once per
  part, not yet a single click for an entire vehicle. Doesn't unwrap for you - requires
  a real UV unwrap already applied. Shelf packing works but isn't space-optimal.

- [x] **"The RRF opening in Blender rarely has the correct BMP on it" — texture
  resolution reliability overhaul, done 2026-07-06.** Explicit user framing after a
  night of repeated real-world failures: the same underlying problem (guessing the
  wrong `.TLB`/vehicle, only caught after real in-game testing, never by the plugin
  itself) kept coming up from different angles across three real cases:

  - **Psw232** (Desert_Obj): auto-detect guessed `Desert5.TLB`, then `CustomB14.TLB` —
    both wrong. Only a genuine `.RRI` revealed the real answer
    (`Desert1`/`Desert11`).
  - **PantherG** "II01" (Normandy_Obj): the real, correct `.RRI` existed on disk all
    along, but sitting directly in the shared `Texture\` folder rather than next to the
    `.RRF` — `find_rri_path()` never looked there, so the importer fell back to a
    worse auto-detect guess despite the authoritative answer being one folder away.
  - **Pz4E** (Desert_Obj): auto-detect found a clean, unambiguous, cross-variant-
    consistent 100% match — and was still the wrong *vehicle*, because the active
    mod's `units.csv` pointed the identifier at a different real tank than the file on
    disk depicts. This one turned out to be genuinely unfixable at the file level (see
    [KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md)), not a bug.

  Fixed the two real, fixable gaps:

  1. `find_rri_path()` now also checks `<texture_folder>\<basename>.RRI`, not just
     next to the `.RRF` — directly fixes the exact bug that bit PantherG. Verified via
     an isolated synthetic test (RRI in a sibling `Texture\`, RRF elsewhere): found
     with the new parameter, correctly not found without it.
  2. `find_matching_tlbs()` now returns `(matches, confidence, reason)`. Originally
     planned as a score-threshold "high vs. low" split, but scanning 9 real vehicles
     (Pz4h, Pz4E, TigerL, PantherG, Psw232, SPW250MG, M4A1, StuG3G, and others)
     against both the current, reduced Texture folder and the original, fuller
     98-library set showed **every single one** has another library scoring within 1-2
     unique ids of the top pick — including Psw232's own clean 96%-scoring guess,
     which was still wrong. No score-based threshold survived contact with real
     content, so the classifier was recalibrated to be honest instead: auto-detect is
     now **always** `"low"` confidence, whatever the score looks like; only a real
     `.RRI` (`"rri"`) or an explicit manual `tlb_filepath` (`"manual"`) earns trust.
     Added `cross_check_tlb_across_variants()` to report how consistently the top
     auto-detect guess resolves across sibling theatre-variant copies, as extra
     context alongside (not a substitute for) the confidence label. Low-confidence
     imports now escalate the operator report to a `{"WARNING"}` with explicit wording,
     and stamp `pe_tlb_confidence` onto the atlas Image alongside the existing
     `pe_tlb_filepath`, so it's inspectable later, not just a message that scrolled by
     at import time.

  Documented, rather than "fixed," two related but genuinely out-of-scope failure
  modes (per-unit `.scn Modification` skin overrides; mod-dependent model identifiers)
  in the new [KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md) — no file-level fix is
  possible for either, since the correct answer lives in mission/mod-state data this
  plugin was never designed to read.

  See [TEXTURE_ID_RESOLUTION.md](docs/TEXTURE_ID_RESOLUTION.md) for the full
  confidence-level writeup and [RRI_FORMAT.md](docs/RRI_FORMAT.md) for the RRI
  location fix.

- [x] **Export writer switched from `_24.BMP` to `_8.BMP` (palette-quantized) — fixed
  2026-07-05, same session as the failed-test entry below.** Direct fix for the
  conclusion two entries down: since the real game ignores `_24.BMP` and reads `_8.BMP`
  regardless, `EXPORT_OT_rrf_atlas` now writes that format instead. Added
  `find_source_bmp8()` (locates the model's real, currently-live `_8.BMP` via the
  Image's `pe_tlb_filepath` custom property - deliberately distinct from
  `find_atlas_image()`, which prefers `_24` and is still correct for *importing*),
  `read_bmp8_palette()` (reads the 256-entry BGRA palette straight off that real file),
  `quantize_to_palette()` (chunked nearest-Euclidean-RGB-distance match, no dithering),
  and `write_bmp8()` (writes a byte-correct 8-bit indexed BMP).

  Verified byte-level (not just via Blender's own reimport, which could mask a subtle
  bug): an untouched pixel round-trips to the exact palette index its original color
  already had (zero drift), and two deliberately-painted marks at opposite ends of the
  atlas (near the bottom and near the top) land at the correct rows with the right
  colors - confirming Blender's `Image.pixels` buffer (bottom-up, index 0 = v=0) and a
  positive-height BMP's on-disk row order (also bottom-up) already agree, so no row
  reversal is needed when writing - reversing would have silently flipped every
  exported atlas upside down, which this two-mark test would have caught immediately.

  **Not yet done**: loading an actual exported `_8.BMP` in the real game to see a
  repainted vehicle in the flesh - the verification above is thorough at the byte
  level, but nobody has done the equivalent real-game check that falsified the old
  `_24.BMP` approach, for this new writer specifically.

- [x] **Import hangs indefinitely on models with degenerate faces — fixed 2026-07-05.**
  `Psw232.RRF`'s "turretL" part has 8 of 104 faces with a repeated vertex index within
  the same face (e.g. one quad using vertex 46 twice) - real content in a real shipped
  file, not corruption introduced here. `bmesh.ops.recalc_face_normals()` in
  `_recalculate_normals()` hangs indefinitely if any input face is degenerate this way -
  confirmed reproducible every time on this exact part, while `turretR` (identical
  face/vertex count, no degenerate faces) completed instantly. Confirmed
  `mesh.from_pydata()`+`mesh.update()` keep all faces (including degenerate ones) with
  their original count and order intact - only `mesh.validate()` (which the plugin never
  called) would drop them, and doing that would break `face_texture_id`/
  `face_uv_corners`/detach-face's face-index alignment with the original file. Fixed by
  filtering degenerate faces out of just the `recalc_face_normals()` call's input
  (`valid_faces = [f for f in bm.faces if len({v.index for v in f.verts}) == len(f.verts)]`),
  leaving `mesh.polygons`'s count/order completely untouched. Verified: full import of
  `Psw232.RRF` now completes in 0.136s (previously hung indefinitely).

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

- [x] **Give a whole vehicle its own private, freely-paintable skin — scoped 2026-07-05,
  BUILT 2026-07-07 (see the entry at the top of this file for what actually shipped;
  this entry is kept as-is below for the original scoping/design-tradeoff discussion,
  which is still accurate context for how the two candidate approaches were weighed).**
  User's goal: import a model with *every* library it actually uses
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

- [x] **Repaint export path tested against the real game - and it fails. Tested
  2026-07-05.** Previously only checked via an automated pixel-comparison test inside
  Blender (painted regions match, untouched regions match, correct format/size) - never
  against the real game or ObjEdit. Now tested against both, on a real, ground-truth-
  confirmed install:

  - **ObjEdit**: loading a model with our exported `_24.BMP` present made the entire
    model invisible/black in OE's own 3D view (wireframe needed to see it at all), while
    the Image Lib texture-library preview showed the file's content just fine. Isolated
    with a clean before/after: removing our `_24.BMP` and reloading with only the
    original `_8.BMP` present rendered correctly again - confirming the break was
    specifically about our added file, not an unrelated OE quirk. This may be OE's own
    hardware-rendering path not supporting a `_24.BMP` sibling at all, separate from the
    question below.
  - **The real game**: tested twice, independently, both negative. (1) `Pz4.TLB`
    already ships with a real, pre-existing `Pz4_24.bmp` in active use - painted an
    unmissable mark into it, loaded the exact vehicle/mission using it, no trace of the
    mark. (2) A second vehicle (`PantherG`/`CustomA9.TLB`, confirmed via a genuine
    pre-existing `.RRI` and a 79% id-resolution rate, not a guess) got the same result:
    mark painted, exported as `CustomA9_24.bmp`, loaded the real mission the vehicle
    spawns in at point-blank range - no trace of the mark, no crash, normal rendering
    otherwise.

  Matches a historical PEDG account that vanilla PE's renderer never read `_24.bmp` at
  all - only a separate code-modded engine build ("PEx") does. This install is very
  likely running without that code mod's texture-loading behavior active.
  **Conclusion: Scenario A's export mechanism is sound but targets a file the real game
  doesn't read here - it needs to write into `_8.BMP` instead (quantized against the
  `.TLB`'s own palette) to actually reach the game.** See
  [PAINT_AND_EXPORT_SCOPING.md](docs/PAINT_AND_EXPORT_SCOPING.md) for the full writeup,
  including two auto-detect false starts hit while setting this test up (wrong-library
  guesses, and a vehicle with an undetected per-unit skin override) worth remembering
  for next time.

- [ ] **Some texture placement issues still being tracked down.** Reported after the
  geometry/pivot fixes landed — "model is now accurate" but "still some odd texture
  issues." Not yet reproduced with a specific screenshot/model to diagnose.
