# Scoping: a full `.RRF` geometry writer

**Status: scoped 2026-07-08. Phase 1 (reposition existing vertices, same topology) BUILT,
verified, AND real-tool-confirmed the same day** - `read_vertex_position()`/
`patch_vertex_position()` in `io_import_rrf.py`, wired into
`MESH_OT_pe_write_vertex_positions` ("PE: Write Vertex Positions", Edit Mode mesh context
menu, v0.9.0). The one open item from the build report below (a real visual check in
ObjEdit) is now done - see "Real-tool confirmation" at the end of the Phase 1 section.

**Phase 2 re-scoped the same day, from real engine source, not built yet.** Both of the
format regions that were blocking it (`sortList`, `attribVList`) are now substantially
understood - see "What's genuinely unknown or risky" below for the real `Rrdraw.c`/
`Rrdwire.c` source that resolved most of this. Phase 2 is no longer "blocked on two total
unknowns" - it's now a real, buildable (if involved) piece of work with one concrete
remaining gate: a real in-game/ObjEdit visual test of an actual add/remove-faces edit.
Phase 3 remains not started/not re-scoped.

This is the last major piece needed before
"replace ObjEdit" (see [[project_pe_blender_plugin_goal]]) is true for geometry, not just
texturing — everything built so far (`patch_face_texture_id()`, `patch_face_corners()`,
the `.TLB` writer, the private-skin pipeline) either writes a small fixed-size structure
(`.TLB`) or surgically patches one known field in an exact copy of an existing `.RRF`,
deliberately avoiding touching anything not fully understood. A geometry writer — moving
vertices, adding/removing faces, adding/removing whole parts — can't take that shortcut,
because those changes ripple through fields this project has never had to write before.

## Why this is a bigger step than every previous `.RRF` change

Per [RRF_FORMAT.md](RRF_FORMAT.md), `.RRF` is a direct memory dump with absolute
in-file byte offsets standing in for what were originally in-memory pointers — there's no
chunk/tag structure to lean on. Every previous writer avoided this problem entirely:

- `.TLB` is a flat, fixed-size array (`write_tlb_library()`) — no offsets to recompute.
- `patch_face_texture_id()`/`patch_face_corners()` overwrite one 24-byte face record's own
  fields *in place*, in an exact byte-for-byte copy of the original file. Nothing before or
  after that record moves, so no offset anywhere else in the file needs to change.

The moment a change **resizes** anything — a part gains or loses a vertex, a face is
added or removed, a part is added or removed from the hierarchy — every absolute offset
that comes after that point in the file (every subsequent part's mesh-record offsets, and
every offset *within* every subsequent mesh record) has to be recomputed and rewritten
consistently. That's the real new work here, not the byte-level field encoding, which is
already fully documented.

## What's already understood well enough to write (low risk)

- **Part record** (512 bytes, fixed): name, pivot, `objAttribut`, `parentNo`,
  `childCount`/`childArray` are all already read (`read_rrf()`) and their semantics are
  documented. Writing new values for these is no harder than writing new bytes at a known
  fixed offset.
- **Pivot semantics**: root-vs-child summing rule is confirmed (RRF_FORMAT.md) — needed to
  correctly place a new or moved part.
- **Face record** (24 bytes): vertex-index packing, `MAT_QUAD`, texture-ID/UV-corner
  encoding are all confirmed and already have working readers *and* one working writer
  each (`patch_face_texture_id`, `patch_face_corners`).
- **Vertex record**: plain 16.16 fixed-point XYZ, no ambiguity (`fixed_to_float`/its
  inverse is trivial to write).
- **Normals**: confirmed *not* required to be authored — the engine recalculates its own
  backface-culling from 2D screen-space winding, and this project's own importer already
  ignores stored normals and recalculates on import. A geometry writer doesn't need to
  solve normals at all, beyond keeping face winding roughly sane for Blender's own display.
- **Embedded placeholder texture block** (`textureStart`/`textureLen`): confirmed fixed
  256-byte 16×16 checkerboard placeholder in every real file checked — safe to copy
  verbatim from a known-good file rather than needing to understand its content.

## What's genuinely unknown or risky (the real scoping problem)

- **`sortList`** (`faceCount × 8` uint16 entries) — **substantially resolved, 2026-07-08,
  from real engine source, not just data analysis.** Confirmed against `rrobjpex\Rrdraw.c`
  (`rrDirectionToSortListNo()`, `rrCalcSortDirection()`, and the `SORT_XSMALL`/`SORT_YSMALL`/
  `SORT_ZSMALL`/`SORT_XBIG`/`SORT_YBIG`/`SORT_ZBIG` constants in `Headers\SCENE.H`, also
  independently defined in `rrobjpex\Tank.c`):

  ```c
  int32 rrDirectionToSortListNo(int32 dirFlag)
  {
     int32 listNo=0;
     if(dirFlag&SORT_XSMALL) listNo|=1;
     if(dirFlag&SORT_YSMALL) listNo|=2;
     if(dirFlag&SORT_ZSMALL) listNo|=4;
     return(listNo);
  }
  ```

  The 8 blocks are **the 8 octants of 3D space**: block index = a 3-bit code built from
  the sign of the camera/view direction's X, Y, and Z components *in the part's own local
  space* (bit 0 = X negative, bit 1 = Y negative, bit 2 = Z negative — derived from
  `rrCalcSortDirection()`'s `mat`-transformed axis projections). At render time,
  `rrDefineSortlist()` just picks `sortListBasis + sortInfo*maxFaces` — i.e. it selects
  which of the file's 8 *already-baked* orderings to use for the current camera direction;
  it does **not** compute the ordering itself at runtime. That per-octant baking is exactly
  what a writer has to reproduce.

  Confirmed empirically too (before finding the source above, and independently
  corroborating it): surveyed real `sortList` data on several real parts
  (`PantherG.RRF`/`Pz4H.RRF` hulls) and found (1) every one of the 8 blocks is a clean,
  valid permutation of `0..faceCount-1` (no stray/flagged values, e.g. no bit-15-tagged
  "skip this face" entries in any real file checked, even though `Rrdwire.c`'s render loop
  has a `faceOrderList[faceNo]&0x8000` skip check — that mechanism, if real, is either
  runtime-only or simply unused in every real shipped file this project has access to);
  and (2) sorting each part's own face centroids by depth along the corresponding octant's
  diagonal direction (the 8 `(±1,±1,±1)`-normalized vectors) correlates strongly with the
  stored block order — Spearman's ρ of **0.85–0.96** across every block tested, and
  every single best-fit direction landed on the octant-diagonal set, never on a
  compass-heading-around-one-axis set (several were tried and scored far worse) — a
  correlation this consistent, on a direction set that then turned out to be exactly what
  the real source uses, is real confirmation, not coincidence.

  **What's still not pinned down exactly**: the correlation is strong but not perfect
  (residual ~5–15% positional deviation from a plain average-vertex-centroid depth sort),
  meaning the *precise* metric the original tool used per octant (maybe a different
  reference point per face, e.g. nearest/farthest vertex instead of centroid, or specific
  tie-breaking) isn't fully nailed down. **Practical implication for Phase 2**: a writer
  that regenerates all 8 blocks as "sort this part's faces by centroid depth along the
  correct octant's diagonal, using the exact `SORT_XSMALL`/`SORT_YSMALL`/`SORT_ZSMALL`
  bit-to-block-index mapping confirmed above" would very likely be correct or very close
  to it — worth a real in-game visual test (z-fighting/wrong-draw-order artifacts would be
  the visible failure mode if the approximation isn't good enough, not a crash) before
  trusting it blindly, but this is no longer a black box needing invention from scratch.

- **`attribVList`** (`vertexCount` uint16 entries, rounded up to even) — **role
  confirmed from real source, 2026-07-08; exact semantic meaning of the value itself still
  open.** Confirmed in `Rrdwire.c` (both copies, `RRF object hex\` and `rrobjpex\`), inside
  the function that subdivides ("splits") one face into a finer `sx × sy` grid — the same
  routine RRF_FORMAT.md's own corner-encoding facts came from:

  ```c
  va1=obj->partArray[splitObjNo].meshArray[0].attribVList[v1];
  va2=obj->partArray[splitObjNo].meshArray[0].attribVList[v2];
  va3=obj->partArray[splitObjNo].meshArray[0].attribVList[v3];
  va4=obj->partArray[splitObjNo].meshArray[0].attribVList[v4];
  rrCalcAttribList(sx,sy,va1,va2,va3,va4,newAttribVList);
  ```

  This mirrors exactly how the same function interpolates vertex *positions*
  (`rrCalc3DArrays(... vList, newVertexList)`) and *normals*
  (`rrCalc3DArrays(... vNormList, newVertexNormList)`) across the new subdivision grid —
  i.e. `attribVList` is a genuine **interpolatable per-vertex numeric value**, smoothly
  blended across a face split exactly like position/normal, not a discrete flag/bitmask.
  This is consistent with the real value patterns already observed (e.g. one part's unique
  values were `[0, 264, 520, 776, 1032, 1288, ...]` — evenly-spaced-ish numbers, exactly the
  shape you'd expect from an interpolated quantity, not a small set of independent flag
  bits) and with RRF_FORMAT.md's original "used when the original editor splits faces"
  description, now with a real mechanism behind it instead of just a guess.

  **What this doesn't yet tell us**: what the interpolated *quantity itself* represents
  (lighting/shading weight? a texture-tile coordinate distinct from the face-corner UV
  bytes? something else?) — `rrCalcAttribList`'s own body wasn't found/inspected in this
  pass. **Practical implication for Phase 2**: many real parts checked have **all-zero**
  `attribVList` data (e.g. `PantherG`'s own hull, `Schuerzen`, `Turmblende`) — i.e. zero is
  a real, common, safe value for parts that were never put through this specific
  face-splitting/tessellation feature. For a Phase 2 writer that isn't itself implementing
  large-face texture-tile subdivision, the safe approach is: leave every **untouched**
  vertex's `attribVList` entry exactly as it already was, and default any genuinely **new**
  vertex's entry to `0` (matching the common real-file baseline) rather than inventing a
  value — this side-steps the open "what does the number mean" question entirely for the
  cases Phase 2 actually needs to handle.
- **Per-part `maxVertex` vs. the mesh record's own `vertexCount`** — **checked, 2026-07-08:
  they never differ.** Surveyed all 5,166 real `.RRF` files under
  `L:\Panzer Elite Ostpak3\` (33,023 real parts, zero parse errors) comparing each part's
  header `maxVertex` field against its own LOD0 mesh record's `vertexCount`: **zero
  mismatches**. `maxVertex` is simply a duplicate of `vertexCount`, not a separate
  pre-allocated capacity — there's no "free slack" to exploit for appending vertices
  without moving anything else. This removes one of this document's original two open
  unknowns entirely, and is also a useful data point for Phase 2/3: whenever face/vertex
  count does change, `maxVertex` should very likely just be set equal to the new
  `vertexCount`, matching every real file's own universal behavior.
- **LOD levels 1-7**: this project's own prior finding (confirmed twice, independently,
  from real PEDG community source) is that **the shipped engine has only ever read LOD
  0** — the other 7 mesh-record slots exist structurally but are dead weight for
  in-game correctness. This substantially de-risks a writer: it likely only needs to
  write a valid, non-crashing (e.g. zero-count) stub for LOD 1-7 rather than real
  decimated geometry, *for the shipped game*. Whether ObjEdit itself, or any other real
  tool, reads or depends on non-empty higher LODs is unconfirmed and worth a quick check
  before assuming they can be safely stubbed out entirely.
- **Hierarchy edits** (adding/removing a whole part): every part's `childArray`/
  `parentNo`/`childCount` would need renumbering if a part is inserted or removed
  mid-array (part indices are positional — the array index *is* the part number,
  referenced by other parts' `childArray` entries), and every mesh-record offset in every
  part *after* the insertion/removal point shifts by however many bytes were added or
  removed. This is the most invasive category of change and depends on getting the
  offset-recomputation right, which in turn depends on correctly handling `sortList`/
  `attribVList` for the new/resized part (see above) — not just growing/shrinking the
  file.

## Recommended phased approach

Rather than attempting "arbitrary geometry rebuild" as one undertaking, phase it by how
much of the unknown territory above each phase actually needs to touch:

### Phase 1 — reposition existing vertices, same topology (lowest risk, most immediately useful)

Move vertices without adding or removing any vertex, face, or part. Face count,
vertex count, and every absolute offset in the file stay **exactly** as they already are
— this is the same "exact copy, patch one known field in place" philosophy as
`patch_face_texture_id()`/`patch_face_corners()`, just applied to the vertex-position
field instead of a face field. Doesn't touch `sortList`, `attribVList`, LODs, or the
hierarchy at all, so none of the genuinely unknown territory above is at risk.

**What this unlocks on its own**: real sculpting/reshaping of an existing part's mesh in
Blender (fixing a bad panel line, reshaping a mudguard, adjusting a barrel profile) with a
real write-back — something no existing operator does today. A meaningfully useful,
low-risk capability in its own right, not just a stepping stone.

**BUILT and verified 2026-07-08.** `read_vertex_position()`/`patch_vertex_position()`
mirror the existing `patch_face_texture_id()`/`patch_face_corners()` pattern exactly (a
`_vertex_record_offset()` helper re-reads the mesh record's own `vertexCount`/
`vertexList` fields fresh from the buffer every call, so it stays correct across repeated
patches in the same session). `MESH_OT_pe_write_vertex_positions` ("PE: Write Vertex
Positions", Edit Mode mesh context menu, v0.9.0) wires this to a real operator: refuses to
run if Blender's own vertex count for the part no longer matches the file's, converts each
vertex from Blender's local mesh-space convention back to the file's raw value (root part:
`raw = local + pivot`, since the root's mesh is stored in Blender as raw-minus-pivot;
every other part: `raw = local` directly, unchanged — see `build_blender_objects()`), and
writes through the usual `.bak`-backed-up surgical patch. The pivot is read from a new
`pe_pivot` custom property stamped on every object at import time, not from the object's
own (possibly since-moved) `obj.location`.

Verified on a real file (`PantherG.RRF`, scratch copy), not synthetically:
- Byte-level: patched one vertex each on the root part and a non-root part (`Bow_MG`) in
  an in-memory buffer — read-back matched exactly what was written, and a full-file byte
  diff confirmed every changed byte fell inside one of the two patched vertices' own
  12-byte records, nothing else in the file moved.
- Real `bpy.ops` end-to-end: imported the file, moved vertex 0 of both the root and a
  non-root part in real Edit Mode, ran `bpy.ops.mesh.pe_write_vertex_positions()` on each,
  and confirmed the raw file value matches the expected pivot-aware conversion for each
  case (root got the pivot added back, non-root didn't).
- Re-import: a fresh import of the modified file placed the moved vertex at exactly the
  new local position in both cases, while every *other* vertex on both parts (229 on the
  root, 9 on the non-root) re-imported byte-for-byte identical to the pristine original.

**Real-tool confirmation, done 2026-07-08.** Loaded a deliberately exaggerated test case
in the user's real, working ObjEdit build (`PEx_105_ObjEdit.exe`) — moved vertex 68 on
`PantherG.RRF`'s hull (originally the mesh's own highest point, at the rear of the hull)
straight up by 3 units via the real operator, then opened the resulting file in ObjEdit's
own 3D view. Result: the file loaded with no error, the whole model (hull, turret, gun,
running gear) rendered as a normal, recognizable Panther silhouette, and the one
deliberately moved vertex showed up exactly as expected — an isolated, localized spike/
tent of stretched triangles at that single point, with nothing else on the model
distorted, missing, or corrupted.

Getting to a clean load took two real environment fixes along the way, worth remembering
for any future native-tool testing in this project: (1) launching the exe without an
explicit working directory made it inherit the launcher's own cwd rather than its own
install folder, so it failed to find its own `MTYPE.DAT` (shading/palette config) and then
crashed with a null-pointer-style access violation in its renderer DLL trying to use the
uninitialized config — fixed by explicitly setting the working directory to the exe's own
folder; (2) this particular ObjEdit build doesn't cleanly support a file path passed as a
command-line argument at all (causes a Delphi "Range check error" plus a second access
violation, even with the working directory now correct) — the real fix was launching it
with no arguments and using its own **File > Open** dialog instead.

This closes the one open item from this section: a real visual check that vertex
repositioning renders correctly, not just a clean byte-level/re-import round trip. It also
answers the `sortList` question this check was meant to inform: moving vertex
*positions* only (face-index order/count completely unchanged) rendered with no visible
z-ordering or draw-order artifacts, consistent with `sortList` being a pure face-index
permutation that isn't sensitive to where a vertex sits — real evidence (not just a
plausibility argument) that Phase 1 doesn't need to touch it, though this doesn't yet say
anything about whether `sortList` matters once face *count* changes (Phase 2's problem,
still open).

### Phase 2 — add/remove faces within an existing part (same vertex/part count elsewhere)

**Re-scoped 2026-07-08 with the sortList/attribVList findings above — no longer blocked
on two total unknowns, though real work and one more real-world test remain.**

Requires resizing one part's `vertexList`/`faceList`, which resizes that part's mesh
record's own variable-length regions (`faceList`, `vertexList`, `faceNormList`,
`vertexNormList`, `sortList`, `attribVList`). Per RRF_FORMAT.md, every "offset" field is
**already confirmed absolute from the start of the file**, not part-relative — so
resizing anything in one part's mesh data means every part *after* it in the file needs
its own mesh record's 6 offset fields (`faceList`/`faceNormList`/`vertexList`/
`vertexNormList`/`sortList`/`attribVList`, all in every LOD slot 0-7, not just LOD 0)
shifted by the same byte delta. Mechanically involved, but not ambiguous — this is a
plain, deterministic offset-rewrite once the size delta is known, not a new format
mystery.

What's now needed to actually build this, given the findings above:
1. **A real `sortList` builder**: for the resized part, recompute all 8 blocks using the
   confirmed octant-direction/`SORT_XSMALL`-style bit mapping and a centroid-depth sort per
   block (see above) — buildable now, though its output should be treated as "very likely
   correct, not proven exact" until tested in-game.
2. **`attribVList` carry-forward**: preserve existing vertices' values unchanged; default
   any genuinely new vertex to `0` (the common real-file baseline for parts that never used
   the face-splitting feature this field supports) — no invention of unknown values needed
   for Phase 2's actual scope.
3. **Offset rewriting for every part after the resized one** — the one piece of this phase
   with no remaining conceptual unknown, just careful implementation (and a good test: byte-
   diff a same-topology no-op "resize by zero" pass against the original file to catch any
   off-by-one in the shift logic before testing an actual add/remove-faces case).
4. **A real in-game/ObjEdit visual test of a Phase-2 edit specifically** (add or remove a
   face, not just move a vertex) — this is the one thing Phase 1's own real-tool test
   didn't cover, since it never changed face count/order. This is now the actual remaining
   gate before trusting Phase 2, not a total black box needing invention from scratch.

### Phase 3 — add/remove whole parts, hierarchy edits

The full case: new `objCount`, renumbered `parentNo`/`childArray` across the whole file,
and Phase 2's offset/sortList/attribVList problem for the new or resized part(s). This is
effectively "rebuild the file from a part list," the most general and highest-risk case,
and depends on Phase 2 being solved first.

### Explicitly out of scope for this document

- **`.RRI` writer** (registering which libraries a model uses) — a separate, much
  simpler fixed-format writer (16 slots × 128-byte path strings, see
  [RRI_FORMAT.md](RRI_FORMAT.md)), still not started, but not coupled to geometry writing
  at all.
- **Part attribute/flag editing UI** (rename a part, toggle the hidden bit in
  `objAttribut`, etc.) — trivial once Phase 1's "rewrite the part record" mechanics exist,
  but a UI/UX question, not a format-risk one; not worth scoping separately here.

## Recommendation

**Update 2026-07-08: both of this document's original prerequisite checks, and the Phase
1 build itself, are now fully done.** The empirical `maxVertex`-vs-`vertexCount` survey
(5,166 real files, 33,023 parts, zero mismatches) is resolved. Phase 1 is built, verified
at the byte/re-import level, and now also confirmed in the user's real ObjEdit build - a
deliberately exaggerated vertex move rendered as a clean, isolated, correctly-placed
distortion with the rest of the model completely intact (see "Real-tool confirmation" in
the Phase 1 section above). That test also gave real (not just plausibility-argument)
evidence that `sortList` isn't sensitive to vertex position alone, since face order/count
never changed and nothing rendered wrong.

Phase 1 can be considered genuinely closed. **Phase 2 (add/remove faces within a part)**
was re-scoped the same day: real engine source (`Rrdraw.c`'s `rrDirectionToSortListNo()`/
`rrCalcSortDirection()`/`SORT_XSMALL` family, `Rrdwire.c`'s `attribVList`-interpolation
call site) resolved both fields that were previously total unknowns - `sortList` is 8
octant-direction-selected face-draw-order permutations (mechanism confirmed from source,
exact per-face depth metric only ~85-96% correlated with a plain centroid sort, so
"very likely correct, not proven exact"), and `attribVList` is a genuinely interpolatable
per-vertex value tied to a face-splitting feature that Phase 2 doesn't need to implement
(safe default: preserve existing values, zero-fill new vertices, matching real files'
own common baseline).

**Recommended next step for Phase 2**: build the pieces now that the unknowns are
resolved (a `sortList` builder using the confirmed octant/centroid-depth recipe, the
offset-rewrite pass for every part after a resized one, `attribVList` carry-forward/
zero-fill) - but treat "add a face, resize the part, load it in ObjEdit or the real game
and look for z-fighting/wrong-draw-order artifacts" as the actual closing test, the same
way Phase 1's real-tool test was the final gate there. Don't consider Phase 2 done on
byte-level/re-import verification alone this time - the approximate nature of the
`sortList` recipe specifically means a visual check matters more here than it did for
Phase 1's exact vertex-position writer.
