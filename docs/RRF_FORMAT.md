# `.RRF` model geometry format

Panzer Elite's `.RRF` files are a direct memory dump of the game's in-memory object
representation, including raw offsets in place of pointers. There is no chunk/tag
structure — the layout below **is** the format.

## Numeric convention

All multi-byte fields are little-endian (x86). Coordinates and angles are **32-bit
signed 16.16 fixed point**, never plain IEEE float:

- `rrCoord` (position/size): `real_value = raw_int32 / 65536.0`
- `rrAngle` (rotation): `real_radians = raw_int32 / 20860.7567` (full circle = 131072 raw
  units; half circle/π = 65536)

## File layout

```
offset 0   Header (20 bytes)
offset 20  partArray[objCount], 512 bytes each
```

### Header (20 bytes)

| offset | field | type |
|---|---|---|
| 0 | maxLOD | u16 |
| 2 | transInfo | u16 |
| 4 | objCount | u32 |
| 8 | maxAllVertex | u32 |
| 12 | textureStart | u32 |
| 16 | textureLen | u32 |

`textureStart + textureLen` always equals the exact file size — a reliable sanity check
for "is this really an `.RRF` file". `textureStart..textureStart+textureLen` is an
embedded per-object texture block, but in every real shipped asset checked it's a fixed
256-byte 16×16 checkerboard placeholder, not meaningful art (see
[TEXTURE_ID_RESOLUTION.md](TEXTURE_ID_RESOLUTION.md)).

### Part record (512 bytes each)

Each "part" is one named node in the model's hierarchy (hull, turret, wheel, gun barrel,
etc.) and owns its own mesh data for up to 8 LOD levels.

| offset | field | type |
|---|---|---|
| 0 | name | char[12], null-terminated |
| 12/16/20 | pivotX/Y/Z | rrCoord × 3 |
| 24 | boxRangeX[2] | rrCoord × 2 |
| 32 | boxRangeY[2] | rrCoord × 2 |
| 40 | boxRangeZ[2] | rrCoord × 2 |
| 48 | boxPosX[4] | rrCoord × 4 |
| 64 | boxPosY[4] | rrCoord × 4 |
| 80 | objAttribut | u32 — low byte = part type (turret/gun/track/crew position/etc.), bit 31 = hidden flag |
| 84 | maxVertex | u32 |
| 88 | parentNo | u32 (`0xFFFFFFFF` = no parent — root part) |
| 92 | childCount | u32 |
| 96 | childArray[32] | u32 × 32 |
| 224 | meshArray[8] | rrMesh × 8 (36 bytes each), one per LOD level |

**Pivots are parent-relative deltas everywhere except directly under the root part.**
The root's own pivot is the model's coordinate-frame anchor, not a translation to
compound into its children - cancel it once for the root's direct children, then sum
every pivot below that normally (child position = parent position + child's own pivot),
all the way down the hierarchy.

This corrects an earlier reading that treated every pivot as root-absolute and cancelled
all of them uniformly. That seemed to fix a gun barrel (`Kanone` → `Blende` → `turm` →
`Tiger1`) flying out under naive full summing, but that specific chain's `turm` (turret)
pivot happens to be within a fraction of a unit of `(0,0,0)`, so cancelling every level
and cancelling only the root's render identically there - not real evidence either way.
A different vehicle's main gun (parent `turret`, whose pivot is a substantial
`(0, 1.45, 7.6)`) exposed the actual bug: cancelling every level placed the gun at
hull-deck height, completely disconnected from the turret it's mounted in. Cancelling
only the root and letting every level past it sum naturally puts the gun exactly at
turret height, correctly protruding from the mantlet — and the original 4-level chain
that motivated "cancel everything" holds up fine under this corrected rule too, since it
never actually depended on cancelling past the root in the first place.

**Every non-root part's raw mesh vertices are local to that part: world position = raw
vertex + pivot, unconditionally**, for every real vehicle and prop checked (Tiger1,
Pz4H_3, Pz4H, Pz4H2, PantherG2, ISU-152, a horse-drawn cart). This corrects two earlier,
wrong readings of this format:

- The first assumed vehicles instead use "world = raw vertex" unmodified, based on early
  testing that never actually exercised the difference — several parts checked at the
  time (e.g. Tiger1's Turret, and both tracks) happen to have a pivot within a fraction
  of a unit of `(0,0,0)`, so adding it or not renders identically either way. The
  screenshots that seemed to confirm "no add" were coincidental, not real evidence.
- The second tried to detect the convention per part, comparing how far each candidate
  placement oversoots the root part's own bounding box, after noticing a Panzer IV's
  (`Pz4H.RRF`) 16 road wheels rendering stacked at the model's centre under a single
  file-wide "no add" choice. That per-part heuristic was *also* wrong: it flagged that
  same model's turret, and Tiger1's hatch/radio/gun/coax MG, as "no add" too, on the
  theory that a part cleanly nesting inside the root part's bbox without its pivot must
  already be in world-space. Rendered and inspected visually (not just checked against
  bbox math) — with "no add", the turret is a flat slab fused into the hull roof; with
  "add pivot", it's an unmistakable, correctly elevated turret with mantlet and cupola.
  Bounding-box overshoot against the root part is simply an unreliable signal here: a
  part sitting correctly above the hull roof, below the hull belly, or spread along the
  hull sides routinely and legitimately falls outside the hull mesh's own narrow bounds,
  which is exactly what an overshoot test penalizes.

Every non-root part in every real file checked has a substantial, non-trivial pivot —
consistent with an ordinary rigged-parts-hierarchy design (mesh authored local to its
own pivot, placed by translating to that pivot), not a coincidence specific to one asset.

### Mesh record (36 bytes, one per LOD level)

| offset | field |
|---|---|
| 0 | meshType (u32) |
| 4 | faceCount (u32) |
| 8 | faceList offset (u32, absolute file offset) |
| 12 | faceNormList offset (u32) |
| 16 | vertexCount (u32) |
| 20 | vertexList offset (u32) |
| 24 | vertexNormList offset (u32) |
| 28 | sortList offset (u32) |
| 32 | attribVList offset (u32) |

All "offset" fields are byte offsets from the start of the file (equivalent to the
in-memory object base address the original loader added them to).

- `sortList`: `faceCount × 8` `uint16` entries — a precomputed face-draw-order
  permutation per one of 8 coarse view directions (avoids a per-frame sort).
- `attribVList`: `vertexCount` (rounded up to even) `uint16` entries, indexed by vertex —
  a per-vertex attribute tag used when the original editor splits faces.

### Face record (24 bytes)

| offset | field |
|---|---|
| 0 | v1 (u32) |
| 4 | v2 (u32) |
| 8 | v3 (u32) |
| 12 | textureOfset (u32) |
| 16 | textureHalf (u32) |
| 20 | materialInfo (u32) |

`v1`/`v2`/`v3` pack a real vertex index in their **low 16 bits**; the upper 16 bits carry
UV pixel-offset data (see below) rather than a 4th vertex index. Mask with `& 0xFFFF` to
get the real index.

`materialInfo` bit flags relevant to import:
- bit 4 (`0x10`, `MAT_QUAD`): this is a quad, not a triangle. The 4th vertex index is
  `textureHalf & 0xFFFF` — **not** packed into `v1`/`v2`/`v3`.
- bits 0-1 (`0x3`): shading mode. Value `3` (`MAT_SHADING_DEEP`) means the face is a flat
  solid color, and `textureOfset` is repurposed to hold a packed RGB value instead of a
  texture reference — don't treat these as textured faces.
- bits 2-3 (`0xC`, `MAT_TEXTRUE_MASK`): non-zero means the face is texture-mapped.

**Vertex/geometry normals should be recalculated on import, not trusted from the file.**
The original renderer only required consistent winding for single-sided faces (it
backface-culls based on 2D screen-space winding at render time); two-sided faces were
never required to wind consistently since the game doesn't cull their backfaces either
way. That leaves no single reliable "outward" direction to carry over — a mesh imported
with stored winding shows a visibly inconsistent mix of correctly- and incorrectly-
oriented faces. Recalculating normals from the actual mesh shape (any standard
"recalculate outside" algorithm) resolves this.

### UV coordinates (packed into face vertex fields)

Confirmed directly against a live paint-and-save test in the original ObjEdit tool: when
a face is textured, each of `v1`/`v2`/`v3`/(`textureHalf` for quads) carries a pixel
offset **within the assigned texture library entry** in its upper 16 bits — one byte
for X, one for Y (`x = (field >> 16) & 0xFF`, `y = (field >> 24) & 0xFF`). Corner roles:

| field | corner |
|---|---|
| v1 | top-right |
| v2 | top-left |
| v3 | bottom-left |
| textureHalf (quads only) | bottom-right |

Combined with the resolved library entry's atlas position (see
[TLB_FORMAT.md](TLB_FORMAT.md)), this gives a full UV mapping into the shared texture
atlas with no cropping or per-part image needed.

Each corner byte being single-precision (0–255) means any one face's own crop can never
exceed 256×256 pixels — this is a hard structural consequence of the field width, not a
choice. Independently confirmed via historical PEDG community discussion as the real,
intentional design limit (minimum crop size 16×16, maximum 256×256) — the format
reverse-engineering here and the original design constraint agree exactly.

### Writing: surgical patches, not a full reconstruction

Unlike `.TLB` (a simple fixed-size array - see `write_tlb_library()` in
[TLB_FORMAT.md](TLB_FORMAT.md)), `.RRF`'s mesh/LOD data is a web of absolute in-file
offsets, and several pieces of it (`sortList`, `attribVList`, LOD levels above 0, the
embedded placeholder texture block) aren't understood well enough yet to safely
reconstruct a whole file from scratch - the risk of silently corrupting something not
fully understood is real. `patch_face_texture_id()` takes the safer, narrower approach
instead: patch one known field (a face's `textureOfset`) directly in an exact copy of the
original file, so everything else is *guaranteed* byte-identical without needing to
understand or rebuild the rest of the format first.

Verified against every real vehicle/prop model in the asset set: a plain "read raw, write
raw" round trip is byte-identical, and patching one face's texture reference changes only
that field's own bytes (confirmed via full byte diff) while every other part, vertex,
face, and UV corner re-parses identically through the normal importer.

A full "rebuild an arbitrary model from scratch" `.RRF` writer (needed for genuinely new
geometry, not just repointing an existing face at a different texture entry) would be a
separate, materially bigger undertaking - see `TODO.md`.

**A face whose corner bytes are all literally `(0,0)` was never individually cropped in
the original tool at all — it's not a genuine "crop to a single pixel" choice.**
Confirmed on real content: an entire building model had every single one of its
resolved faces at exactly `(0,0,0,0)`, too systematic to be a one-off. Treating this
literally (sampling one atlas pixel for the whole face) produces a flat, blocky,
stretched-looking result instead of the real texture. The correct fallback is to use the
assigned entry's **full rectangle** instead, with the same corner-role order above:
`v1=(sizeX-1,0)`, `v2=(0,0)`, `v3=(0,sizeY-1)`, `textureHalf=(sizeX-1,sizeY-1)`.
