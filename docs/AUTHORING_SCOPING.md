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

### 4. `sortList` for a model with no original to derive from

This is the one genuine risk. `derive_sort_list()` works by carrying the part's own
authored ordering forward - a brand-new part has none, leaving only
`compute_sort_list()`, which reproduces a real ordering in **7-11 of 328 positions per
block**. It has never been shown to cause a real failure, but it has also never been
shown to be correct.

**This is solvable by measurement rather than guesswork, and should be, before relying
on it**: there are 7,418 real `.RRF` files on disk, each containing 8 known-good blocks.
That is a large labelled dataset. The task is to find the rule that reproduces them
byte-exactly - iterate on the candidate depth metric, axis/sign mapping and tie-breaking
until exact-match rate approaches 100%, rather than settling for a correlation. Until
that is done, treat any from-scratch model as needing a real in-tool check.

## Open questions to settle before building

- **`boxPos[4]` semantics.** "4 Detail Kollision Rect" per `Object.h`. `object.c`'s
  landscape-object path compares `boxPosX[1]`/`boxPosX[0]` against the mesh extents and
  pads `boxRange` by `0x21800`, but that branch does not run for vehicles. What a vehicle
  needs here is unconfirmed - and ObjEdit's "match hull/turret/gun/parent" speed keys
  suggest a convention worth reading out of `OEMainUnit.pas` rather than inventing.
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
