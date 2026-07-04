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

**Pivots are absolute (root-relative), not parent-relative deltas.** When reconstructing
the hierarchy in another tool, treat each part's pivot as its absolute world-space
anchor; don't sum ancestor pivots when composing the hierarchy transform, or deeply
nested parts (e.g. gun barrel → mantlet → turret → hull) will fly out to wildly wrong
positions.

**Two different vertex conventions exist, and nothing in the format flags which one a
given file uses.** On the vehicles checked (tanks), a part's raw mesh vertices are
already in one shared/assembled coordinate frame — world position = raw vertex,
unmodified; the pivot is only a rotation-origin marker. On at least some static
props/scenery (confirmed on a horse-drawn cart model), raw vertices are **part-local**
and need the part's own pivot added back: world position = raw vertex + pivot. Assuming
the vehicle convention on this kind of content produces parts flung tens of units
outside the model's own bounding box.

There's no reliable way to know in advance which convention a given file uses — detect
it empirically per model: try both conventions on every non-root part and see which one
keeps parts nested inside (or close to) the root part's own bounding box. The wrong
convention overshoots it dramatically; the right one doesn't.

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

**A face whose corner bytes are all literally `(0,0)` was never individually cropped in
the original tool at all — it's not a genuine "crop to a single pixel" choice.**
Confirmed on real content: an entire building model had every single one of its
resolved faces at exactly `(0,0,0,0)`, too systematic to be a one-off. Treating this
literally (sampling one atlas pixel for the whole face) produces a flat, blocky,
stretched-looking result instead of the real texture. The correct fallback is to use the
assigned entry's **full rectangle** instead, with the same corner-role order above:
`v1=(sizeX-1,0)`, `v2=(0,0)`, `v3=(0,sizeY-1)`, `textureHalf=(sizeX-1,sizeY-1)`.
