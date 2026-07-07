# Scoping: a full `.RRF` geometry writer

**Status: scoped 2026-07-08. Phase 1 (reposition existing vertices, same topology) BUILT,
verified, AND real-tool-confirmed the same day** - `read_vertex_position()`/
`patch_vertex_position()` in `io_import_rrf.py`, wired into
`MESH_OT_pe_write_vertex_positions` ("PE: Write Vertex Positions", Edit Mode mesh context
menu, v0.9.0). The one open item from the build report below (a real visual check in
ObjEdit) is now done - see "Real-tool confirmation" at the end of the Phase 1 section.
Phases 2 and 3 remain not started.

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

- **`sortList`** (`faceCount × 8` uint16 entries): a precomputed face-draw-order
  permutation per one of 8 coarse view directions. Never reconstructed or even read by
  this project — only its *existence and size* are documented. If the engine actually
  relies on this for correct draw order (plausible, since it exists specifically to avoid
  a per-frame sort), a writer that adds/removes faces without correctly regenerating all 8
  permutations risks visibly wrong z-ordering (overlapping semi-transparent or two-sided
  faces drawing in the wrong order) rather than an outright crash — the kind of subtle bug
  that's easy to ship without noticing. **Needs real investigation before any writer that
  changes face count is attempted**: does the game visibly misbehave with a naive/trivial
  sortList (e.g. front-to-back index order duplicated 8×), or does it only matter for
  specific material types? Untested.
- **`attribVList`** (`vertexCount` uint16 entries, rounded up to even): "a per-vertex
  attribute tag used when the original editor splits faces" per RRF_FORMAT.md — the
  precise semantics were never pinned down beyond that description. Unknown whether a
  writer can safely zero-fill this or whether specific values are load-bearing.
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

Requires resizing one part's `vertexList`/`faceList`, which shifts every offset in that
part's own mesh record and (if any part after it in the file references absolute
offsets past this point — need to confirm whether offsets are part-relative or truly
file-absolute in a way that requires rewriting *every subsequent part*, not just this
one) potentially every part after it too. This is the phase that makes `sortList`/
`attribVList` unavoidable — can't be skipped once face count changes. **Blocked on the
two open investigations above** (does a naive/regenerated `sortList` render correctly;
what does `attribVList` actually need to contain).

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
is the next candidate, but it's the phase that first has to actually confront `sortList`/
`attribVList` rather than sidestep them - expect it to need its own investigation into
what a naive/regenerated `sortList` does to real rendering (this Phase 1 test only showed
that `sortList` tolerates unchanged face order with moved vertices, not that it tolerates
being rebuilt or approximated after a face-count change) before writing any code that
depends on getting it right.
