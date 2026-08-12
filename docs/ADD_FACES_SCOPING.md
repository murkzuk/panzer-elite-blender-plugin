# Scoping: adding faces/vertices to a `.RRF` (the last gap before a real exporter)

**Status: scoped 2026-08-12, not built.** Everything below was checked against real
files and the real shipped code during the scoping pass itself - the measured results
are quoted inline rather than left as assumptions to re-derive later.

Deleting faces has shipped since 2026-07-08 (`MESH_OT_pe_delete_faces`). Adding them is
the mirror operation and the last real blocker between "surgical editor" and "you can
author a model in Blender and export it". This document is what that would actually
take.

## What already exists, and is already proven

`rebuild_part_mesh_region()` is **not** delete-specific. It takes arbitrary
`new_vertices`/`new_faces` lists, recomputes the region from scratch, and shifts every
later part's offsets by the size delta - it never looks at whether the counts went down
or up. The delete operator is simply its first caller. Supporting pieces all exist too:

| Piece | State |
|---|---|
| `rebuild_part_mesh_region()` | count-agnostic resize + offset shift, verified via delete |
| `compute_sort_list()` | rebuilds all 8 octant blocks for any face set |
| `_pack_face_record()` | packs a new 24-byte textured face record |
| `patch_face_corners_per_vertex()` | writes real per-vertex UV corners |
| `append_tlb_entry()` / `find_free_atlas_space()` | allocates new atlas rectangles |
| `plan_private_skin()` / `apply_private_skin()` | full island -> atlas allocation pipeline |

So the *structural* half of "add faces" is done. What follows is what genuinely isn't.

## Blocker 1 - the two vertex-capacity fields are never maintained (measured)

`rebuild_part_mesh_region()` writes back the header's `maxAllVertex` **unchanged**, and
never touches the per-part `maxVertex` at all. Both were measured during this scoping
pass rather than assumed:

- **`maxAllVertex` is a total vertex budget**, not a per-part figure. Across all
  **7,418** real `.RRF` files under a full install: **7,260 (97.9%)** have
  `maxAllVertex` exactly equal to the *sum* of every part's LOD0 `vertexCount`; **0**
  match the largest single part; the remaining 158 (Tiger/Pz6E variants) sit *slightly
  above* the sum (e.g. 9528 vs 9514), **never below**. The consistent
  "greater-than-or-equal to the sum" shape is what an allocation bound looks like.
- **Per-part `maxVertex` duplicates that part's `vertexCount`** - the earlier survey of
  33,023 parts found zero mismatches.
- **Both go stale after a rebuild.** Exercising the real shipped function on a real
  `PantherG.RRF` part, shrinking 453 -> 450 vertices: afterwards `maxVertex` still read
  453 against a `vertexCount` of 450, and `maxAllVertex` still read 5488 against a real
  sum of 5485.

For **deletion** this is harmless in practice - both fields end up larger than needed,
which is the safe direction, and matches the 158 real files that already ship that way.
For **addition** it inverts: both fields would be *smaller* than the real counts. If the
engine sizes any per-vertex working buffer from them (which is what a "max" field of
this shape normally exists for), that is an overflow, not a cosmetic inconsistency.

**Required work**: update both fields inside `rebuild_part_mesh_region()` - set the
part's `maxVertex` to its new `vertexCount`, and the header's `maxAllVertex` to the new
sum across all parts. This also quietly fixes the existing staleness left behind by
every delete performed to date. Cheap, and it should land regardless of whether the
rest of this is built.

## Blocker 2 - a new face has no texture assignment (the real design question)

This is the actual reason "add faces" was deferred, and it is a design gap rather than a
format unknown. A newly created face carries no `textureOfset`, and this plugin's
materials each represent a **whole `.TLB` library**, not one atlas rectangle - so
"material + UV" alone cannot say which crop a new face should sample. Three viable
shapes, in increasing order of effort:

1. **Inherit from an adjacent face** *(recommended for v1)*. A face added in Blender is
   almost always adjacent to existing geometry (extrude, subdivide, fill). Take the
   `textureOfset` of a face sharing an edge, then compute this face's own corners from
   its real UVs via the existing `patch_face_corners_per_vertex()`. No new allocation, no
   new atlas space, reuses only proven code. Refuse with a clear message when no
   textured neighbour exists, rather than inventing an assignment.
2. **Allocate a new `.TLB` entry per new face**, via `find_free_atlas_space()` +
   `append_tlb_entry()` - the same path `MESH_OT_pe_detach_face_texture` already uses.
   Correct but wasteful, and it fragments the atlas exactly the way the private-skin work
   was built to avoid.
3. **Re-run the private-skin pipeline for the whole part** after editing, so the new
   geometry is unwrapped and packed together with everything else. The cleanest result,
   and the machinery exists - but it repaints the entire part, which is a much larger,
   more destructive operation than "I added one face".

Recommended: build (1), and point users at the existing private-skin operator when they
want (3). (2) is not worth building as its own path.

## Other real constraints (none blocking, all already handled somewhere)

- **`sortList`** is rebuilt for the new face set by `compute_sort_list()`. Its
  closed-form recipe is empirically strong (Spearman rho 0.85-0.99 across every real
  part tested) but **never proven byte-exact** against the original tool - so any
  add-faces result deserves a real ObjEdit load before bulk use, same caveat the delete
  path already carries.
- **`attribVList`**: zero-fill for new vertices, carrying real values forward for
  existing ones - the documented safe baseline (see RRF_WRITER_SCOPING.md), and what
  delete already does.
- **`materialInfo`**: `_pack_face_record()` writes `0x9` (`0x19` for quads), the most
  common real textured-face value. New faces are therefore always textured; genuinely
  untextured/solid-shaded new content is out of scope.
- **Per-face crop cap**: 256x256 pixels per face, a hard format limit - already enforced
  by `size_islands_to_tiles()`.
- **Coordinates**: vertex positions are written in the file's own raw 16.16 convention.
  The import-time real-world scale (0.15625), the 180-degree +Y flip and the ground-snap
  are all applied to the *object* transform, not to mesh data, so `vert.co` is already
  raw and needs no inverse. Confirmed during this scoping pass: importing a real
  `PantherG.RRF` with all three options on and immediately writing all 31 parts'
  positions straight back produced a **byte-identical file**.

## Suggested phasing

- **Phase 2b-i** - fix the capacity fields (Blocker 1). Small, self-contained, also
  repairs existing delete output. No new UI.
- **Phase 2b-ii** - `MESH_OT_pe_add_faces`, using neighbour-inheritance for texturing
  (Blocker 2, option 1). Handles the real cases: extrude, subdivide, fill, knife.
- **Phase 2b-iii** - relax the "same part" restriction / mixed add+delete in one
  operation, if it proves needed in practice.
- **Phase 3** (still unscoped) - adding or removing whole parts and editing the
  hierarchy. Genuinely separate; touches the part array and child lists, not just one
  part's mesh region.

## Verification plan (mirroring how delete was proven)

1. **Byte-level**: add one face to a scratch copy, confirm the file grew by exactly one
   face record plus its `faceNormList`/`sortList` share, that the part before it is
   byte-identical, and that the part after it is identical at its new shifted offset.
2. **Invariants**: all 8 new `sortList` blocks are valid permutations of `0..n-1`;
   `maxVertex == vertexCount`; `maxAllVertex == sum(vertexCount)`.
3. **Re-import**: every face resolves to a real texture, zero unresolved.
4. **Real tool**: load the result in `PEx_105_ObjEdit.exe` and confirm the new geometry
   renders correctly with no crash - the step that has caught what byte checks could not
   throughout this project.

## Explicitly out of scope

Adding/removing whole parts (Phase 3), untextured/solid-shaded new faces, editing LODs
above 0 (all 8 slots are exact duplicates of LOD0 in every real file checked, and the
writer keeps them that way), and authoring a `.RRF` from nothing - every operation here
still edits a file that already exists and that the mesh was imported from.
