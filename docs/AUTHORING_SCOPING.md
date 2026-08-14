# Scoping: modelling and painting a new vehicle entirely in Blender

**Status: scoped 2026-08-12, not built.** Goal stated by the user: model in Blender,
paint in Blender, and get out a working `.RRF` **plus the `.TLB` that goes with it** -
no ObjEdit in the loop.

This is deliberately separate from
[ADD_FACES_SCOPING.md](ADD_FACES_SCOPING.md), which covered *editing* an existing model
(now built, see `MESH_OT_pe_write_mesh`). Authoring from nothing is a different problem:
every writer in this plugin currently rewrites part of a file that already exists and
that the mesh was imported from.

## What already exists and is tested

More than half the job is done, which is worth being clear about before listing gaps.

| Piece | State |
|---|---|
| `.TLB` writer (`new_tlb_library`, `append_tlb_entry`, `write_tlb_library`) | byte-exact against all 98 real libraries |
| `_8.BMP` writer + palette quantiser | real 8-bit paletted output, the format the game actually reads |
| UV island detection, sizing, atlas packing | `detect_uv_islands` / `size_islands_to_tiles` / `pack_islands_shelf` |
| Whole-part skin generation | `MESH_OT_pe_give_private_skin` already builds a **new dedicated `.TLB` + `.BMP`** for a part and writes real per-face crops |
| Mesh region writer | `rebuild_part_mesh_region`, now lossless; adding geometry confirmed in ObjEdit |
| Paint round-trip | Blender Texture Paint -> `EXPORT_OT_rrf_atlas` -> `_8.BMP` |

So "paint in Blender and produce a `.TLB`" is **substantially already built** - it just
currently requires an existing `.RRF` to attach to.

## The four real gaps, in dependency order

### 1. Creating a `.RRF` from nothing (the blocker)

Everything else waits on this. There is no `create_rrf()`; the header and `partArray`
are only ever read or patched. What has to be produced:

- Header: `maxLOD`, `transInfo`, `objCount`, `maxAllVertex`, `textureStart`, `textureLen`.
- Per part (512 bytes): `name[12]`, pivot, the collision box fields
  (`boxRangeX/Y/Z`, `boxPosX[4]`, `boxPosY[4]`), `objAttribut`, `maxVertex`, `parentNo`,
  `childCount`, `childArray[32]`, and `meshArray[8]` (all 8 LOD slots, which every real
  file fills identically to LOD0).
- The mesh region per part, which `rebuild_part_mesh_region()` can already lay out.

Mapping Blender's own object hierarchy to `parentNo`/`childArray` is straightforward -
Blender parenting is the natural source. `objAttribut` carries the gameplay type tags
(`Rrattrib.h`: HAUS/TREE/TANK/TURM/KANNONE/MUZZLE...) and would need to be author-set,
most naturally as a per-object enum property on export.

**The collision box is a real requirement, not cosmetic.** Confirmed from the PEDG
archive, a modder stating it plainly: *"when you add an object to an rrf, you must also
adjust the bounding box... There are speed keys for match hull, turret, gun and parent."*
ObjEdit exposes it under Edit > Bounding Box. A new model needs these filled in, and the
exact semantics of `boxPos*` ("4 Detail Kollision Rect") are **not yet decoded** - see
open questions.

### 2. A palette for a brand-new `.TLB`

`new_tlb_library()` currently sets `library.palette = bytes(2048)` - all zeros, i.e.
black. `MESH_OT_pe_give_private_skin` sidesteps this by borrowing the palette from the
part's existing `_8.BMP`, and falls back to greyscale when there is no source. Neither
is acceptable for a model authored from scratch and painted in colour.

Options, cheapest first:
1. **Copy a real theatre palette** from an existing `.TLB`/`_8.BMP` of the theatre the
   model belongs to. Safest, keeps the model looking native to its set.
2. **Generate one** (median-cut) from the painted texture. Better colour fidelity for
   unusual paint, but risks looking off next to stock vehicles.

Recommend (1) as the default with (2) as an option. Note the `.TLB` palette block is
**2048 bytes** while a BMP palette is 256x4=1024 - the relationship between the two has
not been verified and must be before writing either.

### 3. An `.RRI` writer

Read-only today (`read_rri`, `find_rri_path`). It matters more than it looks: ObjEdit
derives the RRI path by swapping the last character of the `.RRF` name
(`convNameRRF_To_RRI` in `OEMainUnit.pas`) and, when it finds none, warns *"No RRI file
found, No auto load of textures!"* and loads the model untextured. ObjEdit writes one on
every save; a Blender-authored model should too, or it will not round-trip into the tool
cleanly. Format is already documented in [RRI_FORMAT.md](RRI_FORMAT.md) (16 slots of
128-byte null-padded paths) - this is the smallest item on the list.

### 4. `sortList` for a model with no original to derive from - RESOLVED 2026-08-12

**The measurement programme this section originally proposed was based on a false
premise and should not be run.** It assumed a generating algorithm existed that could be
fitted against the 7,418-file corpus. Reading the real source shows there is none.

Nothing in the engine or in ObjEdit ever generates a mesh sortList. The only writes
anywhere are offset<->pointer conversion at load (`object.c`) and `rrBspTreeEdit()`
(`Rrdwire.c`) - which, despite the name, builds no tree: it swaps one selected face one
position earlier (`sortFlag==1`) or later (`sortFlag==2`) in the block for the *current
view direction*. That is ObjEdit's manual "move this face forward/back in the draw order"
tool, driven by `rrSetSortInfo`. `rrAddObject()` imports a part from another file and
carries that file's ordering along with it.

So a real file's ordering is **hand-authored by the artist, one nudge at a time**, per
octant. No algorithm reproduces it, which is exactly why `compute_sort_list()`'s
closed-form recipe matched only 7-11 of 328 positions per block - it was never modelling
a real process.

**What a new part should get instead: plain identity order (0..n-1) in all 8 blocks.**
That is not a guess either. Measured across 3,259 real parts (26,072 blocks): 6.1% of
blocks ship as exactly identity, and 5.1% of parts use one identical ordering across all
8 octants. It is a shape real content genuinely takes, the engine accepts it, and the
artist can then tune draw order in ObjEdit's own Sort tool - the same way every real
model got its ordering in the first place.

Implemented as `identity_sort_list()`, now the fallback in `rebuild_part_mesh_region()`
whenever there is no authored ordering to derive from. `derive_sort_list()` remains
correct and preferred wherever an original exists; `compute_sort_list()` is kept only for
callers who explicitly want a depth-ordered guess.

## Open questions to settle before building

- ~~**`boxPos[4]` semantics**~~ - **RESOLVED 2026-08-12** from `rrDoGenBounding()`
  (`Rrdwire.c`), which is what ObjEdit's Bounding Box > Gen button calls. It scans the
  part's LOD0 vertices for min/max per axis and writes an axis-aligned range per axis
  plus the four corners of the XY rectangle:

      boxRangeX = [minX, maxX]     boxPosX[0..3] = maxX, minX, maxX, minX
      boxRangeY = [minY, maxY]     boxPosY[0..3] = maxY, maxY, minY, minY
      boxRangeZ = [minZ, maxZ]

  `boxPos` is four points rather than an extent so the box can be rotated independently
  of the mesh (`rrRotateObjectBounding`), with `boxRange` remaining the axis-aligned
  bound. Implemented as `compute_part_bounding()` / `patch_part_bounding()`.

  **Most real boxes are not auto-generated and must not be overwritten.** Measured across
  1,656 real parts: only 32.9% match their own mesh extent; 0% match the whole-vehicle
  extent and 0% match own-plus-children. The remaining 67% were set deliberately through
  ObjEdit's Gen/Rotate/MatchParent/MatchMain/MatchTurret tools (`rrDoMatchBounding` copies
  a box from another part with an offset), and `object.c` says as much - a box that does
  not match the model's extents means "the maker has a specific size in mind".

  So the writer regenerates a box only when it provably still matches what
  `rrDoGenBounding()` would produce for the old geometry, and otherwise preserves it and
  warns that it no longer contains the mesh, pointing at ObjEdit's own tool. That matches
  the requirement a PEDG modder stated plainly - *"when you add an object to an rrf, you
  must also adjust the bounding box"* - without silently discarding authoring decisions.
- **`.TLB` palette block layout** (2048 bytes vs BMP's 1024) - see gap 2.
- **`transInfo`** (bit 0 = Phong per `Object.h`) and sensible `objAttribut` defaults for
  a new vehicle part.
- **`meshType`** values for tri vs quad meshes on a newly authored part.

## Suggested phases

- **A - `.RRI` writer.** Smallest, self-contained, immediately useful (it fixes the
  "No RRI file found" path for everything this plugin already produces, not just new
  models).
- **B - palette sourcing** for `new_tlb_library()`, so a fresh `.TLB` is paintable in
  real colours. Unlocks "paint in Blender" fully on its own.
- **C - `sortList` accuracy programme.** Fit against the 7,418-file corpus until
  byte-exact. Do this *before* C-dependent work is trusted, not after.
- **D - `create_rrf()`** - header + part array + hierarchy from Blender objects, reusing
  `rebuild_part_mesh_region()` for each part's mesh region. The big one.
- **E - a single "Export vehicle" operator** tying D + `give_private_skin` + A together:
  Blender objects in, `.RRF` + `.TLB` + `_8.BMP` + `.RRI` out.

A and B are worth doing regardless of whether D ever happens - they improve the existing
edit-and-paint workflow on their own.

## Verification plan

Same ladder that has actually caught things on this project, in this order:

1. **Byte level** - a generated file re-parses through this plugin's own reader with the
   expected counts, offsets and capacity fields.
2. **Round-trip** - re-import reproduces the geometry and UVs that went in, with zero
   unresolved faces.
3. **Real tool** - loads in `PEx_105_ObjEdit.exe` with textures auto-loading from the
   generated `.RRI`. Use a model whose baseline is known to load in that setup; always
   load the unmodified control first (a whole session was lost in 2026-08-12 to skipping
   that step).
4. **Real game** - the only test that has ever falsified an assumption outright here
   (see the `_24.BMP` export finding in PAINT_AND_EXPORT_SCOPING.md).

## Scope note

Nothing above needs new format reverse-engineering except the collision-box and palette
questions. The binary layouts are known; this is mostly construction work plus one real
measurement programme (`sortList`).

---

## Working headless authoring loop (2026-08-14, v0.53.0)

`pe_give_private_skin` alone cannot produce a loadable model: each part gets its own .TLB
whose entry ids restart at 0, while every part's faces still name slot 0. Five parts =
five libraries fighting over the same addresses. Two things were needed.

### 1. `budget_fraction` on the private-skin operator

`plan_private_skin()` sized each part's islands to fill **60% of a whole atlas**, correct
for a lone part and hopeless when merging - five parts want 300% of one atlas and the
merge ran out of space partway through the fourth. Now a parameter (default 0.6
unchanged); pass roughly `0.55/N` when the parts will be merged.

### 2. `tools/merge_private_skins.py`

Repacks every per-part entry into one 256x4096 atlas, assigns unique ids, rewrites every
face to the merged library in slot 0, and writes the matching `.RRI`. The palettes already
match (each private skin borrows the same one), so no requantising - and the merge refuses
outright if they ever differ rather than silently recolouring half the model.

### Verified end to end, no GUI

```
tools/auto_skin.py            unwrap + private-skin all 5 parts   (headless Blender)
tools/merge_private_skins.py  73 entries -> one library, 141 faces repointed, 0 skipped
import the result             141 faces textured, 0 unresolved, no magenta
```

Settings used by `auto_skin.py`, all established by measurement: axis-aligned rotation,
**Correct Aspect OFF** (the repacker treats UV space as square and does its own
conversion - correcting twice gives 38 clamped islands against 13), 45 degree angle limit,
zero island margin (`apply_private_skin` repacks with its own `margin_px=2`).

### Gotchas worth keeping

- `write_bmp8()` wants a numpy array in **bottom-up** row order; `read_bmp8`-style code
  hands back top-down. Reversing is required or every merged atlas is upside down.
- `FloatProperty` was missing from the add-on's `bpy.props` import - adding an operator
  float property fails at module import until it is added.
- **Never use Blender's Image > Save for a PE atlas.** It writes the image datablock's own
  format regardless of the extension typed - a `.BMP` filename got a PNG inside it, 61 KB
  instead of 1,026 KB. Only `File > Export > Panzer Elite Texture Atlas` writes the 8-bit
  paletted BMP the game reads. A wrong-sized file in Explorer is the quickest tell.
