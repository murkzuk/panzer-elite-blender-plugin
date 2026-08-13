# TODO / Backlog

Running list of things flagged during work sessions, not yet done. Newest first.

---

- [ ] **NEXT: headless DLL harness for ground-truth face UVs - see
  [docs/HEADLESS_DLL_HARNESS_PLAN.md](docs/HEADLESS_DLL_HARNESS_PLAN.md).** User's idea
  (2026-08-13), feasibility checked and it is real.

  `rrobjx5.dll` in the OE_2 folder **exports the very function this project has been
  reverse-engineering**: `_rrGetUsedSelection@20`, plus `_rrLoadGameMesh@4`,
  `_rrSetupTextureLib@8`, `_rrSendTexturePart@32`, `_rrSendTexturePal@12`,
  `_rrInitRender@0`. (A naive name search says "not exported" - the names carry a leading
  underscore and `@N` stdcall decoration.) Calling it per face makes the engine state its
  own texture rectangle numerically, for thousands of faces in seconds, with no GUI.

  **Blocker**: every one of those DLLs is 32-bit x86 and every Python here is 64-bit, so
  it needs a 32-bit Python (install to K:, never C:). `rrnop.dll` (710KB, same folder) is
  almost certainly a no-op HAL for headless init.

  Worth it beyond this bug: it turns "load in ObjEdit and squint" into a repeatable
  numerical oracle, and takes ObjEdit - which is slow to drive - off the critical path.

  Cheaper fallback if that stalls: the checkerboard test is already built
  (`Italy_Obj/Tiger1_ChkTest.RRF`); it only failed because ObjEdit `chdir(maindir)`s
  before resolving the `.RRI`, so put the library in OE_2's Texture folder or use an
  absolute path.

- [ ] **START HERE on texture work: [docs/TEXTURE_PIPELINE_FINDINGS.md](docs/TEXTURE_PIPELINE_FINDINGS.md)**
  consolidates everything established about the texture pipeline as of 2026-08-13, split
  into settled (with the source function or sample size behind each fact) and open. Read
  it before re-deriving anything.

  Settled and shipped: the texture-id encoding including the 32-library extension
  (Tigers 35%->100% resolved), slot-correct library selection, atlas/entry geometry, the
  three .TLB size variants, and the .TLB palette layout.

  **Open: how a face's texture coordinates are derived.** Every face on the Italy Tiger
  has zero corner bytes and near-zero attribVList, 4,256 faces share two entries, and the
  obvious crop reading predicts atlas regions outside the entry itself (13-22% fit). The
  renderer computes tu/tv inside OBJHALX5.dll, whose source is in none of the archives.

  **Next action**: the checkerboard experiment is already built -
  `Italy_Obj/Tiger1_ChkTest.RRF` + `.RRI` with `Texture/ChkTest.TLB`/`ChkTest_8.BMP` (a
  real entry table over a labelled 32px grid). It loaded untextured on the first try
  because ObjEdit `chdir(maindir)`s before resolving the .RRI, so `texture\ChkTest.TLB`
  resolved against ObjEdit's own folder. Fix by placing the library where ObjEdit looks or
  using an absolute path in the .RRI, then read the grid labels off a face.

- [ ] **UNSOLVED: how face texture coordinates are actually derived.** The "jumbled
  textures" symptom is NOT fixed, and the crop-rectangle model previously recorded in
  RRF_FORMAT.md is now known to be wrong. Written up in full there; the short version:

  On a real Italy Tiger every one of 4,785 faces has all-zero corner bytes, `attribVList`
  is 96.6% zero, and 4,256 faces share just two entries with identical size and origin -
  so the file does not appear to contain per-face UVs at all. The decisive contradiction:
  `materialInfo` gives a 64x48 crop against a 32x128 entry, i.e. wider than the entry that
  should contain it, on 4,737 of 4,772 faces. All four nibble-order combinations fit
  inside their entry only 13-22% of the time.

  The renderer takes per-vertex float `tu/tv` (`WVERTEX` in WingsHAL.h) and
  `halRenderFaces()` receives only the sort list, so the HAL computes them itself.
  **OBJHALX5.dll has no source in any available archive** - rrobjpex, ObjEdit, Select,
  VisualAI, mod_enabler2 and Particle_Editor were all checked.

  **Next step that needs no further source**: paint the labelled checkerboard into a
  library atlas, load an affected model in the real ObjEdit, and read the mapping off the
  result directly. That technique is already proven in this project.

  What IS solid and stays: the 32-library texture-id decode (Tigers 35%->100% resolved),
  slot-correct library selection, and per-slot library assignment. Those are separate
  wins and are verified.

- [x] **"Jumbled textures" finally solved - the per-face crop ORIGIN was never read.
  2026-08-12.** The user's standing complaint ("this is our Achilles heel") turned out to
  be one missing field.

  A face's `textureOfset` carries more than an id: bits 0-11 are the part id, 12-15 the
  library slot, and **bits 16-23 are the crop origin within the entry, in 16px units**.
  `rrUsedSelection()` (Rrdwire.c) spells the all-zero-corner fallback out exactly:
  `StartX = ((TexInfo>>20)&0xf)*16; StartY = ((TexInfo>>16)&0xf)*16`, with the size from
  materialInfo. This importer read the size and assumed the origin was always (0,0).

  That is invisible while each face has its own entry and catastrophic once faces share
  one. On a real Italy Tiger **all 4,785 faces use the all-zero-corner path, and 4,256 of
  them reference just TWO entries** - large sheets each face crops a different window out
  of. Every one sampled the same top-left corner at a different size, producing the
  overlapping patchwork users kept reporting.

  Fixed, and the same Tiger now renders with coherent camo, clean panel boundaries and a
  correctly-placed Balkenkreuz. Full layout written up in RRF_FORMAT.md.

  Still open: the running gear renders dark red-brown and still looks wrong.

- [x] **Jumbled textures on multi-library models - real cause found and fixed 2026-08-12.**
  User report: "the TLB import still brings in jumbled textures... we are not right on
  reading the texture data from the RRF". Correct on both counts.

  **A face names its own library slot and resolution ignored it.** The old code scanned
  every loaded library and returned the first holding a matching part id; many libraries
  share ids, so faces were textured from the wrong libraries. Resolution now tries the
  face's own slot first.

  **Auto-detect numbered its score-ranked matches 0,1,2...**, which made a face's slot
  meaningless. It now assigns libraries to the slots faces actually name, keeping the
  score-ranked list as fallback - per-slot assignment alone covers fewer ids and regressed
  Is2-0 from 0 to 422 unresolved before the fallback was restored.

  **An .RRI can be structurally unable to name a slot.** The 8- and 16-slot variants
  cannot express slots 16-31; a real Tiger1 has 289 faces in slot 16 against a 16-slot
  RRI. Those slots are now inferred while the RRI wins wherever it speaks.

  **Slots with very few distinct ids cannot identify their library.** On Italy_Obj's
  Tiger1, 89% of faces sit in one slot referencing just TWO entries - they are large
  sheets and each face's corner crop picks a sub-area - so every library in the folder
  ties and the winner was arbitrary (an Italy Tiger picking up CustomA1). Confident slots
  are now settled first and low-id slots break ties toward the family already chosen, so
  AUTO reaches the same answer as setting the theatre by hand. Verified visually: the
  Italy Tiger now renders in proper theatre camo.

  Two dead ends worth not repeating: TLBs come in three sizes (461K/1.5M/3.6M) because 64
  of them embed their own bitmap, but the entry table is identical so reading the first
  461,064 bytes is correct; and an embedded bitmap differing from its sibling `_8.BMP` in
  100% of bytes turned out to be the same artwork with a different palette.

  Still open: the running gear on that Tiger renders dark red-brown and looks wrong.

- [x] **Texture-library remap (ObjEdit's ReNumTLB) - built 2026-08-12.**
  `MESH_OT_pe_remap_texture_library` repoints a model's faces from one library slot to
  another, for moving a vehicle onto a different theatre's libraries without re-texturing
  it face by face. Reproduces the real function including its safety behaviour: faces
  whose part id exceeds the target library's maximum are left alone and reported rather
  than pointed at a rectangle that may not exist.

  Added `decode_texture_offset()` / `encode_texture_offset()` as the shared, correct
  encoding (slot bits 12-15, part id bits 0-11, plus the 32-library extension). Verified
  by round-tripping **115,613 real textured faces with zero mismatches**, which also
  measured slot use directly: real content uses slots up to 23, with **28,772 faces in
  slots 16+** - the extension that had been invisible to this plugin until today.

  A real remap on PantherG moved exactly 3,579 faces from slot 15 to 7, skipped none, and
  changed 3,579 bytes - one per face, since only the slot nibble moves - with the file
  size unchanged.

- [x] **Texture resolution: the 32-library extension - found and fixed 2026-08-12. Large
  real improvement.** Reading `rrReNumTLB()` while scoping a library-remap feature turned
  up how the engine actually decodes `textureOfset`: slot = bits 12-15, part = bits 0-11,
  **and when that part number exceeds 2047 the real slot is slot+16 and the real part is
  part-2048**. Resolving with `texture_id % 4096` alone misses every such face.

  **22.8% of textured faces on a real install use that encoding** (82,109 of 359,735),
  concentrated in the Tiger and IS-2 families. `resolve_texture_id()` now tries both
  candidates - not a switch on the >2047 test, since some models' ids above 2047 are
  genuine part numbers and forcing the subtraction makes those worse; trying both can
  only ever add a resolution.

  Through the real import operator: **TigerE_1 35% -> 100%, TigerL 71% -> 100%**, Is2-0
  99.2% -> 100%, all three now importing with zero unresolved faces. TigerL was
  previously documented in TEXTURE_ID_RESOLUTION.md as resolving inconsistently (19-95%).
  No regression on models that already resolved fully.

- [x] **Face draw-order nudge - built 2026-08-12.** `MESH_OT_pe_move_face_draw_order`
  ("PE: Move Face(s) in Draw Order"). Worth building precisely because the ordering is
  hand-authored: there is no algorithm to recalculate it with, so the one-step nudge is
  the only operation that exists on it, in the original tool or here.

  Reproduces `rrBspTreeEdit` (Rrdwire.c) step for step, including its group behaviour - a
  run of selected faces shifts together without overtaking each other (verified:
  `[0,1,2,3,4,5]` moving `{2,3}` later gives `[0,1,4,2,3,5]`). Writes straight to the
  `.RRF`; the sortList is fixed-size so nothing is rebuilt.

  Defaults to nudging all 8 octants together, which is predictable and almost always
  wanted. A single octant can be chosen (bit0 = X>=0, bit1 = Y>=0, bit2 = Z>=0 per
  `rrDirectionToSortListNo`) for ObjEdit's per-view behaviour. Blender's viewport is
  deliberately NOT auto-mapped onto that - PE's matrix convention has not been verified,
  and guessing would silently edit the wrong octant.

  Verified on a real model: the selected face moved position 3 -> 4, all 8 blocks stayed
  valid permutations, `.bak` written, and the validator reports no errors afterwards.

- [x] **Gameplay attributes are now writable, and there is a model validator - 2026-08-12.**
  The two items the gap analysis ranked highest, both built and verified.

  **Attributes.** `objAttribut` was imported and never written, so part type tags could be
  read but not set - a model edited in Blender could look right and still not function.
  Added `patch_part_attribute()`, wired into the write path, plus
  `MESH_OT_pe_set_part_attribute` ("PE: Set Part Type / Attributes") with the type as an
  enum and a hide checkbox. Only the named fields are replaced; every other bit of the
  word is preserved. `OBJ_TYPE_NAMES` was also completed from the real `Rrattrib.h` -
  **89 constants, where the hand-made table had 37**. Verified on a real PantherG part:
  type 0 -> 4 (TURM) reached the file, other bits untouched, hide flag round-tripped as
  `0x80000004`.

  **Validator** (`validate_rrf()` + "PE: Validate Model"). Every check is a bug this
  project actually hit: capacity fields too small for the geometry, sortList blocks
  indexing past `faceCount`, faces referencing vertices that do not exist, degenerate
  faces, and a collision box that no longer contains its mesh. Tested both directions -
  105 of 150 real shipped models come back completely clean, and all four deliberately
  damaged copies were caught.

  Two things worth keeping from building it. Real shipped content genuinely contains
  broken sortLists (2 of 150 index past `faceCount`, which is a real out-of-bounds read in
  the draw loop; more repeat or omit faces), so the check separates "reads out of bounds"
  from "draws some faces twice" rather than calling both an error. And the collision-box
  warning added earlier was firing on no-op writes, because many parts ship with a box
  that never contained their mesh - it now fires only when the edit itself breaks a box
  that was previously intact.

- [ ] **Full gap analysis against the real ObjEdit - see
  [docs/GAP_ANALYSIS.md](docs/GAP_ANALYSIS.md), compiled 2026-08-12** by enumerating all
  68 `rrobjx5.dll` exports and every ObjEdit dialog unit, then checking each against what
  this add-on does.

  Ranked gaps: (1) **gameplay attributes are read-only** - `objAttribut` is imported as a
  custom property and never written, so TANK/TURM/KANNONE/MUZZLE tags and the hide flag
  cannot be set from Blender; the plugin writes exactly one part-record field
  (`maxVertex`) plus the collision box. Highest value for least work, and a model needs
  these to function in game. (2) part/hierarchy editing - add/remove/rename/re-parent,
  pivot writing (`create_rrf()` territory). (3) face draw order - now known to be
  hand-authored, so a "move face earlier/later" pair would genuinely help. (4) texture ops
  still missing: `rrRotateTextureSelection`, `rrReNumTLB` (remap a model to another
  library), `rrSetTextureSelection`. (5) LOD levels. (6) groups. (7) palette editing.

  Explicitly NOT gaps: selection-level geometry ops (Blender does these better, and the
  write-back carries them through), render plumbing, and `AnimationUnit`/`BallisticUnit`
  (both call no DLL functions - stubs).

  **Useful discovery: a 3DS -> RRF path exists today.** ObjEdit's Import 3DS calls
  `_mcMake` in `meshconv.dll`, which is present next to the working ObjEdit even though
  its source is not in the archives. Model in Blender, export 3DS, import in ObjEdit - a
  real stopgap for authoring from scratch until `create_rrf()` is built.

  Worth adding beyond parity: a validation/lint operator (unresolved faces, invalid
  sortList blocks, capacity fields, a collision box that no longer contains its mesh,
  n-gons - every one has been a real bug here), whole-vehicle private skin on one atlas,
  genuine LOD generation, texture-aware mirroring, and an RRF-to-RRF diff.

- [x] **Collision box decoded and wired in - 2026-08-12.** `rrDoGenBounding()`
  (`Rrdwire.c`), the function behind ObjEdit's Bounding Box > Gen button, scans a part's
  LOD0 vertices and writes `boxRangeX/Y/Z` as per-axis min/max plus `boxPosX/Y[4]` as the
  four corners of the XY rectangle (`maxX,minX,maxX,minX` / `maxY,maxY,minY,minY`) - four
  points rather than an extent so the box can be rotated independently of the mesh.

  The important part is what NOT to do. Only **32.9%** of 1,656 real parts have a box
  matching their own mesh extent; whole-vehicle and own-plus-children both explain **0%**.
  The other 67% were set deliberately via Gen/Rotate/MatchParent/MatchMain/MatchTurret,
  and `object.c` treats a non-matching box as intentional ("the maker has a specific size
  in mind"). So the writer regenerates only when the stored box provably still matches the
  old geometry, and otherwise preserves it and warns that it no longer contains the mesh.
  Verified on a real extrude: 88Pak43 part 0 has a hand-set box, and the warning fired.

- [x] **`.RRI` writer, palette sourcing, and the sortList question - all resolved
  2026-08-12 by actually reading ObjEdit's and the engine's source rather than inferring
  from file data.** Three things came out of it, two of them corrections to this repo's
  own documentation:

  1. **`.RRI` is six blocks, not one, and the library slot count varies by build.** Three
     variants ship in real installs, identifiable by size alone: 214,144 (8 libs),
     267,040 (16) and 668,448 (32). The old fixed 16-slot read could return group names
     like "PantherGa" and "Name 16" as if they were library paths. `read_rri()` now
     detects the variant; `write_rri()` produces the current 32-slot form with ObjEdit's
     own defaults. Not byte-identical by design - real files carry stale `strcopy` buffer
     content after each name terminator, which is uninitialised memory, not data.
  2. **The `.TLB` palette is 256 entries of `[R,G,B,0]` in the first 1024 of its 2048-byte
     block**, the reverse of BMP's `[B,G,R,0]`, confirmed against the engine's own
     `rrSendTexturePal()`. This exposed a real bug: `new_tlb_library()` wrote 2048 zero
     bytes, so **every private-skin `.TLB` shipped with an all-black palette** disagreeing
     with its own `_8.BMP`. Now sourced from a real library in the model's texture folder.
  3. **The sortList has no generator to reverse-engineer.** The proposed "fit against
     7,418 files until byte-exact" programme was based on a false premise and has been
     withdrawn. Nothing in the engine or ObjEdit generates one - `rrBspTreeEdit()` builds
     no tree, it swaps a selected face one position up or down in the current view's
     block. Real orderings are hand-authored per octant. New parts now get identity order
     (`identity_sort_list()`), which 6.1% of real blocks and 5.1% of real parts already
     use.

- [ ] **Model AND paint a new vehicle entirely in Blender - scoped 2026-08-12, see
  [docs/AUTHORING_SCOPING.md](docs/AUTHORING_SCOPING.md).** User's stated direction: model
  in Blender, paint in Blender, get a working `.RRF` **and its `.TLB`** out, with ObjEdit
  no longer in the loop.

  Over half of this already exists - the `.TLB` writer is byte-exact, the `_8.BMP`
  writer + palette quantiser are real, and `MESH_OT_pe_give_private_skin` already builds
  a brand-new dedicated `.TLB`/`.BMP` for a part with real per-face crops. The catch is
  that every writer here still edits a file that already exists.

  Four gaps, in dependency order: (1) **`create_rrf()`** - header + 512-byte part array +
  hierarchy from nothing, the blocker; (2) a **real palette** for a new `.TLB`
  (`new_tlb_library()` currently writes 2048 zero bytes = black, and private-skin borrows
  from an existing BMP that a new model does not have); (3) an **`.RRI` writer** - ObjEdit
  warns "No RRI file found, No auto load of textures!" and loads untextured without one;
  (4) **`sortList` with no original to derive from** - `derive_sort_list()` needs an
  authored ordering, leaving only `compute_sort_list()`, which matches real data in just
  7-11 of 328 positions per block.

  (4) is the one real risk and it is measurable rather than speculative: there are 7,418
  real `.RRF` files on disk holding 8 known-good blocks each, so the rule can be fitted
  until it reproduces them byte-exactly instead of merely correlating. Worth doing before
  anything depends on it.

  Also unresolved and needed for (1): the collision-box fields. A PEDG post states
  outright that adding an object to an RRF requires adjusting the bounding box, and
  ObjEdit exposes "match hull/turret/gun/parent" speed keys for it - `boxPos[4]`
  semantics should be read out of ObjEdit's own source rather than invented.

  Recommended order: `.RRI` writer and palette sourcing first - both are small and improve
  the existing edit-and-paint workflow on their own, regardless of whether the
  from-scratch writer ever lands.

- [x] **Writer made lossless, and adding geometry CONFIRMED WORKING in the real tool -
  2026-08-12.** `rebuild_part_mesh_region()` repacks a part's whole mesh region, and
  byte-comparing a no-op rebuild (feeding it exactly the data already in the file)
  exposed that it was silently inventing four things. A no-op rebuild differed from the
  original by **8,614 bytes**; it now differs by **0**.

  | Lost | Detail |
  |---|---|
  | `maxVertex` / `maxAllVertex` | never updated. `Scene.c` shows these size the real per-actor vertex buffers (`vCount=obj->maxAllVertex`, carved per part by `maxVertex`), so growing a part wrote into the next part's slice |
  | `materialInfo` | hardcoded to `0x9`/`0x19` - 29 distinct real values on one hull collapsed to 2, and every per-face crop size to 16x16 |
  | face/vertex normals | zero-filled. Real entries are 16.16 unit vectors (measured \|v\|=1.0000) and the engine reads them |
  | `textureHalf` on triangles | forced to 0, while 10 real triangles store 1 |

  Fixed by carrying real data forward rather than reconstructing it: raw 24-byte face
  records are copied and only vertex indices remapped (`repack_existing_face_record()`),
  normals/attribVList/materialInfo are passed through, and the sortList is now **derived
  from the file's own** (`derive_sort_list()`) instead of regenerated - `compute_sort_list()`
  matches a real ordering in only 7-11 of 328 positions per block. All of this also
  repairs the shipped delete operator, which had been losing the same data since July.

  **Real-tool confirmation**: `88Pak43_AddFaceTest.RRF` - a real model with one triangle
  and three vertices genuinely ADDED - loads correctly in `PEx_105_ObjEdit.exe`, as does
  a byte-identical no-op control. This is the first time this project has added geometry
  to a `.RRF` at all; every previous writer only moved or removed things.

  **Process lesson worth keeping.** Most of the day went into a crash hunt against
  PantherG that produced four real findings by measurement but a wrong diagnosis: the
  crashes were environmental (that model would not load in that ObjEdit setup even as a
  byte-identical copy of an unmodified file), and no control was run until very late.
  Comments asserting the sortList "crashes the real engine" were written into the code on
  that basis and have since been corrected. Run the unmodified baseline FIRST when a real
  tool rejects a generated file.

- [ ] **"Add faces" scoped 2026-08-12 - see
  [docs/ADD_FACES_SCOPING.md](docs/ADD_FACES_SCOPING.md).** The last real gap between
  the current surgical editors and an actual `.RRF` exporter. Two findings from the
  scoping pass worth having here directly:

  1. **`maxAllVertex`/`maxVertex` are never maintained by
     `rebuild_part_mesh_region()`** - a real defect, not just a future one. Measured:
     `maxAllVertex` equals the *sum* of every part's LOD0 `vertexCount` in 7,260 of
     7,418 real files (0 match the largest single part; the other 158 sit slightly
     *above* the sum, never below), so it is an allocation bound. Exercising the real
     function on `PantherG.RRF` (453 -> 450 verts) left `maxVertex` reading 453 and
     `maxAllVertex` 5488 against a real sum of 5485. Harmless for deletion (both end up
     too large - the safe direction), but adding vertices without fixing this leaves
     both fields *below* the real counts. Worth fixing on its own even if nothing else
     here gets built.
  2. **The geometry writer is NOT broken by the 2026-08-07 import transforms.** Scale,
     the 180-degree flip and ground-snap are applied to the object transform, not mesh
     data, so `vert.co` stays raw and needs no inverse. Proven, not reasoned: importing
     `PantherG.RRF` with all three options on and writing all 31 parts' positions
     straight back produced a byte-identical file.

  Recommended first step is the capacity-field fix, then an `add faces` operator that
  inherits its texture assignment from an edge-adjacent face (reusing
  `patch_face_corners_per_vertex()`), refusing clearly when there is no textured
  neighbour rather than inventing one.

- [ ] **Wheel-cylinder replacement only reaches ~39% of the real roster - most
  vehicles' wheel objects use naming/structure this pass doesn't handle yet.**
  Logged 2026-08-07, right after shipping the wheel-cylinder + track-grey batch
  pass (`tools/_worker_convert.py`'s `replace_with_cylinder()`, 597 cylinders
  created across CustomB's 154 vehicles, real and working - see that commit for
  the full citation). User caught it live: "marder2 amoung many other 2d wheels
  still" - real, systematic gap, not a one-off.

  Surveyed all 154 converted `.blend` files directly (not assumed): only
  **60/154 (39%)** have any mesh object whose name matches the current filter
  (`wheel`/`roller`/`cog`, case-insensitive substring). The other **94/154
  (61%)** use naming/structure this pass never even considers:
  - **German naming** - `Raeder`/`Räder` ("wheels"), `Rad`/`RadL`/`RadR`/`RVRad`
    ("wheel") - confirmed on Marder2, 76net, ATGun57, ATRgun45/57/76, ATgun76,
    Ba20, Bt7-0, CharB (real sample list, not exhaustive - a full re-survey
    should catalogue every distinct naming convention actually present, not
    just what turned up in one 15-file sample).
  - **Whole-suspension blobs with nothing separable at all** - Churchill3 and
    Cromwell both use one `SUSPENSION` object (no individual wheel geometry
    to extract in the first place) - these may need a completely different
    approach (or may not be fixable this way at all).

  **Real reason this isn't just "add German words to the name filter"**: most
  `Raeder`-style objects are very likely ALSO compound multi-wheel meshes
  (same real problem already found and correctly left alone for `idler`
  (Tiger1) and `Boggies` (KV-2) - Marder2's own `Raeder` is 88 verts, plausible
  for several wheels merged into one object, not one disc). The circularity
  safety check (`replace_with_cylinder()`'s own real fix, see that commit)
  would likely just correctly skip most of these anyway even with wider name
  matching - the real blocker is building genuine connected-component
  splitting for a compound multi-wheel mesh, not just recognizing more names.
  That's a real, separate, bigger task - not attempted here, logged instead of
  chased further this session per explicit direction ("log it as a follow-up,
  don't chase it now").

  **Real next steps when this gets picked up**: (1) full re-survey of all 154
  files' real object names (not the 15-file sample above) to catalogue every
  distinct wheel-naming convention actually present, (2) design a real
  connected-component splitter (likely: cluster a compound wheel mesh's own
  vertices/faces by spatial proximity/normal-consistency into separate wheel-
  sized islands, THEN run the existing per-wheel circularity+cylinder logic on
  each island) rather than treating this as a simple name-matching gap.

---

- [x] **Batch RRF-import correctness: scale, ground-snap, +Y-forward - DONE
  2026-08-07.** Found while batch-converting CustomB's real vehicle roster (154
  files) for Cogs of War. Three real, permanent fixes now baked into
  `io_import_rrf.py` itself (all default-on import options, not just external
  post-processing):
  1. `apply_real_world_scale` - a raw import comes out ~6-9x too big on every
     axis. **`PE_TO_METERS_SCALE` corrected to 0.15625 on 2026-08-12** (was an
     empirically-fitted 0.14 - see the entry at the top of this file for the
     real engine-source citation that replaced it).
  2. `snap_to_ground` - a fresh import's pivot can sit well off the model's own
     ground contact point (confirmed: a real KV-2 came out 0.59m too high).
     Real Blender gotcha found along the way: `matrix_world` isn't recomputed
     synchronously right after setting `.location`/`.scale` - needs an
     explicit `context.view_layer.update()` before reading it for the
     ground-snap calculation, or the offset comes out from stale data.
  3. `flip_to_positive_y_forward` - a raw import faces -Y; every real working
     vehicle already in the project (KV-1, Pz4H) faces +Y. 180° around Z.
  All three: only root-level objects need touching (children inherit via
  Blender's transform hierarchy) - but scale/flip both also need `.location`
  itself scaled/rotated, not just `.scale`/`.rotation_euler`, since some real
  imports have many independent root objects (e.g. a real KV2-0 import has 12
  - AAMG/Comander/radio/Turret_MG1/etc, small detail parts each imported as
  their own unparented object at real raw-unit coordinates) that would
  otherwise render correctly-sized/oriented but scattered away from the hull.
  New `tools/batch_import_gui.pyw` - standalone Windows GUI wrapping the whole
  pipeline (import with all three fixes + optional Smart UV + faction-paint
  pass) for batch work on a whole folder at once, no Blender UI clicking
  needed. Runs each file as its own fresh Blender subprocess with a hard
  per-file timeout, not one long-running loop - real, load-bearing design:
  `Psw232.RRF` was found to hang `bmesh.ops.recalc_face_normals()`
  indefinitely on degenerate geometry in one part (8 of 104 faces on
  `turretL` have a repeated vertex index) during this same real batch work,
  costing 4+ hours before diagnosis; that specific bug is fixed (see
  `_recalculate_normals()`'s own header), but per-file isolation stays right
  for whatever the next unknown edge case turns out to be.
  **Result**: CustomB's real 154-vehicle roster (basically the whole Ostpak
  combat/logistics vehicle set) now converts 154/154 clean, real-world
  scaled, ground-snapped, correctly-facing, faction-painted, in one pass.
  **Known separate issue, not yet tackled**: many real wheel/cog/idler parts
  are genuinely flat 2D discs, not 3D cylinders (confirmed: Tiger1's own
  `wheel_0`-`wheel_15` measure 0.099m thick vs. 0.717m diameter, ~1:7 ratio) -
  a real, common 1999-era engine shortcut, not an import bug. Real follow-up
  candidate once picked back up.
  **Next real target**: the Desert theatre folder - same tool, `faction_map.json`
  will need new entries for whatever's in there (the GUI's own "Edit
  Unclassified" dialog handles this without hand-editing the JSON).

---

- [ ] **Private-skin checker test shows severe stretching on non-rectangular faces in
  real ObjEdit — investigated 2026-07-08, parked, likely not a writer bug at all.**
  Gave `88Pak43.RRF` a private skin, painted a labeled checkerboard onto it to audit UV
  quality, and tested the actual file in the user's real `PEx_105_ObjEdit.exe` (isolated
  test copy, never touching the live file). Result: looked clean in Blender's own
  preview, but severely banded/stretched in OE, especially on the barrel and other
  tapered/non-rectangular panels.

  Tried three approaches for `apply_private_skin()`'s corner-writing, each tested
  against the real tool:
  1. Collapse each face to its UV bounding box, write via the fixed
     v1=top-right/v2=top-left/v3=bottom-left/textureHalf=bottom-right convention
     (`patch_face_corners()`) — broke badly in OE.
  2. Force every face into its own small grid-tiled rectangle under the same fixed-role
     convention — measurably improved, not resolved; also logically can't ever let two
     triangles sharing one flat panel look seamless, since neither can supply the
     4th/BR corner under a *fixed*-role model.
  3. **Current, shipped approach**: write each vertex's own independent (x,y) position
     directly into whichever slot it occupies in the file (matched via `pe_vertex_index`
     against the file's real per-slot vertex-index bits), rather than collapsing to any
     shared box — new `patch_face_corners_per_vertex()`, used only inside
     `apply_private_skin()` (`patch_face_corners()` itself is unchanged, still used by
     `MESH_OT_pe_set_face_crop`, whose docstring already documents the bounding-box-only
     behavior as intentional for that operator's simpler manual-crop case). Reasoning:
     `_corner_xy()` (this project's own read side, sourced from the real engine's
     `Rrdwire.c`) documents the field only as "a UV pixel offset," not a named role —
     the fixed-role pattern was only ever confirmed from one community writer's own code
     for one specific case, not the engine itself.

  **Real-tool result after (3): still not resolved** — and the user correctly diagnosed
  why, independent of any corner-encoding question: many real faces in this mesh simply
  aren't rectangular/square in 3D (tapered plates, trapezoidal panels), and *any* texture
  mapped across a non-rectangular polygon gets stretched toward its longest vertex via
  ordinary UV interpolation, regardless of which corner-encoding convention sits
  underneath. A checkerboard is a uniquely harsh test pattern for this (straight grid
  lines make interpolation skew glaringly visible) in a way a soft continuous camo
  texture never would. This reframes the whole investigation: approaches (2) and (3)
  were likely chasing a red herring rather than a real data bug. (3) is believed correct
  and is the version left in place, but this specific "checkerboard renders unevenly on
  non-square faces" symptom was never actually confirmed fixed.

  **Parked 2026-07-08 per explicit user request** — user's own read: "i actually think
  it is unfixable." Two untried next steps if revisited: (a) retest with a smoother,
  more representative texture (the part's own real borrowed camo, or a plain gradient)
  instead of a checkerboard, to check whether the private-skin data is actually fine for
  realistic content; (b) actually re-seam the worst non-rectangular faces (barrel,
  tapered panels) in Blender so they're genuinely closer to rectangles - a mesh-topology
  change, bigger scope, previously deferred for the same reason during an earlier
  UV-island-splitting attempt the same session. See
  [KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md) for the write-up. Don't re-litigate
  the corner-encoding question again without first ruling out (a) - that's the cheap,
  fast check.

- [x] **Fixed a real crop-size bug in the all-zero-corners fallback — confirmed and
  built 2026-07-08.** User reported `88Pak43.RRF`'s gun shield/barrel rendering
  smeared/stretched. Traced to a genuine bug (not just an approximation): the importer's
  fallback for a face with no explicit crop (`v1`/`v2`/`v3`/`textureHalf` all `(0,0)` -
  confirmed the universal convention across every real file checked, 0/1490 faces with
  any explicit crop) was using the assigned `.TLB` entry's own *full allocated* size -
  but real content routinely uses only *part* of a larger shared entry (confirmed via a
  live ObjEdit comparison: entry 160 is allocated 32x32, but ObjEdit's own Image Lib tool
  showed the actual face using only a 32x16 crop). The real crop size is packed into
  `materialInfo` bits 8-11/12-15 (`((nibble)+1)*16` per axis) - confirmed against the
  real engine source (`Rrdwire.c rrUsedSelection()`) and empirically sensible across
  every distinct `materialInfo` value on the same part (always a clean 16px-multiple
  submultiple of the entry's own size, consistent with one entry commonly being shared
  by several faces, each using its own smaller sub-tile).

  `_read_mesh_lod0()` now computes this real crop size per face (`face_crop_size`, a new
  `RRFPart` field); `build_blender_objects()`'s all-zero-corner fallback uses it instead
  of the entry's full size. Verified end-to-end: a real ObjEdit-baked test file with the
  corrected values loaded and rendered "so much better" per direct user comparison
  against the original file.

  **One remaining edge case, likely bad source data, not pursued further**: one specific
  face (the front glacis plate, `materialInfo` giving crop 48x16 from a 48x48 entry)
  still looked visibly off after the fix. Checked the referenced library entry (123)
  directly in ObjEdit's own Image Lib tool per the user's own judgement - it "looks off"
  there too, independent of anything this plugin does - most likely a genuinely
  unusual/damaged entry in this specific `.TLB`, not a remaining formula bug.

- [x] **Found a real, on-demand way to generate an authoritative `.RRI` for any model —
  2026-07-08.** Previously, a model with no `.RRI` had no reliable texture-library
  answer at all - auto-detect is always low-confidence by design (see
  TEXTURE_ID_RESOLUTION.md), and no analysis of the `.RRF`/`.TLB` files alone can
  substitute for the genuine slot-assignment record only a real `.RRI` carries. Traced
  `TObjectEditForm.SaveObject1Click` in ObjEdit's own Delphi source
  (`ObjEdit\OEMainUnit.pas`): **every File > Save in ObjEdit automatically writes a real
  `.RRI` from whichever libraries are actually loaded in the editor at that moment** -
  not a guess, ObjEdit's own confirmed state. This means any model can get a genuine,
  authoritative `.RRI` on demand: open it in ObjEdit, load libraries until it renders
  correctly, File > Save (**to a copy - this also re-writes the `.RRF` itself, the one
  real caveat**), then use the resulting `.RRI` going forward. Documented in
  [RRI_FORMAT.md](docs/RRI_FORMAT.md#generating-a-real-rri-for-a-model-that-doesnt-have-one).
  Doesn't help with per-face crop questions (RRI only ever records library assignments),
  which remain a `.RRF`-internal, format-level question with no equivalent shortcut.

- [x] **Color-key transparency — built and real-render-verified 2026-07-08.** User
  reported `6pdr.RRF`'s wheel spoke gaps rendering solid white instead of see-through.
  Direct pixel sampling confirmed a genuine, deliberate pattern: the wheel part's
  spoke-gap faces sample exact pure white (1.0,1.0,1.0) while every other sampled face on
  the same part shows normal metal/paint tones - a real 1999-era engine convention
  (reserve one color as "don't draw this," instead of storing per-pixel alpha), not
  incidental. Traced supporting structure in the real engine source too:
  `libMatPal`/`screenWin16PalMatID` (`rrobjpex`/`RRF object hex`) feeds a per-texture-
  library "material type" into the hardware texture upload call, consistent with this
  being a per-library convention.

  `_build_material()` now wires a `Vector Math (Distance)` + `Math (Greater Than)` node
  chain into the material's Alpha input, comparing each sampled pixel against a
  configurable key color - on by default (`use_colorkey=True`, white). **The key color
  is not hardcoded** - user noted PP2-X-sourced content reportedly uses bright
  pink/magenta for the same convention instead, so `colorkey_color` is a real per-import
  override, not a fixed constant. The private-skin operator's own fresh blank canvas
  passes `use_colorkey=False` explicitly, since color-keying an unpainted canvas would
  just punch it full of holes.

  Verified on the real `6pdr.RRF`/`CustomA4.tlb` case: node-graph math confirmed white
  pixels correctly compute alpha=0 and normal wheel colors compute alpha=1, and a real
  EEVEE render of just the wheel part showed a clean, fully-detailed wheel (tread +
  spoke/hub pattern) with zero white patches, versus the same model's solid opaque white
  before this fix.

- [x] **Theatre picker for TLB auto-detect — built and tested 2026-07-08.** User pointed
  out the real ObjEdit doesn't guess at all - its "Select Theatre" dialog just asks
  Desert/Italy/Normandy/Custom A/Custom B/Custom C/None directly and searches only that
  theatre's libraries. Real `.TLB` filenames confirm the pattern (`Desert1-8.TLB`,
  `Italy1-6.TLB`, `Normandy1-6.TLB` shipped with the base game; `CustomA*`/`CustomB*`/
  `CustomC*` added by mods) - and the model's own containing folder is *not* a reliable
  stand-in for this (`PantherG.RRF` sits in `Normandy_Obj` but its real answer is Custom
  A), so ObjEdit's plain-question approach is the right one to mirror, not something to
  cleverly infer.

  Added `find_matching_tlbs(..., name_prefix=...)` (filters candidates by name prefix
  before scoring) and `IMPORT_OT_rrf.theatre` (v0.11.0, mirrors the six real ObjEdit
  options plus "Auto" for the original unfiltered behavior) - only affects the
  last-resort auto-detect step, never `.RRI`/manual override priority.

  Tested against the same two real historical wrong-guess cases documented in
  `TEXTURE_ID_RESOLUTION.md`: on `PantherG.RRF`, the theatre filter kept the same
  already-correct winner (`CustomA1.TLB`) but replaced an irrelevant cross-theatre
  runner-up (`CustomC1.TLB`, coincidental 100% overlap) with a real same-theatre one -
  more honest signal, not a different answer. On `Psw232.RRF`, filtering to `DESERT`
  returned no match at all rather than repeating the historical wrong guess
  (`CustomA8.TLB`, 30%) - confirmed via this model's own real `.RRI` that its true answer
  needs three separate Desert libraries together, one of which (`Desert13`) is missing
  from disk entirely, so no filter can conjure a correct answer here. **Genuine, tested
  improvement (removes cross-theatre false positives, avoids some wrong guesses
  outright) - not a fix for cases needing multiple partial libraries or missing files,
  which remain a documented, separate limitation.**

- [ ] **Priority note (2026-07-08): user is focused on this Blender plugin first.** A
  possible future Godot rewrite of Panzer Elite ("Cogs of War") came up the same day, but
  it's explicitly a separate, not-yet-started effort in its own project directory - see
  `L:\2025\PE\PE SOURCE\COGS_OF_WAR_GODOT_HANDOFF.md` for the starting-point index
  whenever that begins. Until then, treat this repo's own backlog below as the active
  priority, not something to pause for Godot exploration.

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
