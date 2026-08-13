# Texture pipeline: everything established (2026-08-12/13)

Consolidated so none of it has to be re-derived. Split into **settled** (measured or
taken from real engine source) and **open**. Where a fact came from source, the function
and file are named; where it came from measurement, the sample size is given.

---

## SETTLED — texture id encoding

`textureOfset` (per face, 32-bit). From `rrReNumTLB()` and `rrUsedSelection()`
(`rrobjpex.c` / `Rrdwire.c`), and ObjEdit's `ImageLibUnit.pas`:

| Bits | Meaning |
|---|---|
| 0-11 | part id within the library |
| 12-15 | library slot |
| 16-19 | one crop-origin nibble (16px units) |
| 20-23 | the other crop-origin nibble |
| 31 | "is textured" flag |

**32-library extension**: a part id above 2047 means the real slot is `slot+16` and the
real id is `id-2048`. ObjEdit writes the runtime id as `part_id + slot*MAX_PARTS`
(`MAX_PARTS=4096`, `MAX_LIB_PARTS=2048`, `MAX_LIBS=31`).

Verified by round-tripping **115,613 real textured faces with zero mismatches**. Real
content uses slots up to 23, with **28,772 faces in slots >= 16**.

**Impact**: decoding this took TigerE_1 from 35% to 100% resolved and TigerL from 71% to
100%. Shipped.

## SETTLED — which library a face uses

A face names its own slot; resolution must use it. Scanning all loaded libraries and
taking the first with a matching id gives *wrong-but-plausible* textures, because many
libraries share ids. Shipped: own-slot first, fallback to searching.

Auto-detect must also assign libraries to the slots faces actually name (not number
score-ranked matches 0,1,2...). Slots with very few distinct ids cannot identify a
library at all - on the Italy Tiger 89% of faces sit in one slot referencing **two**
entries, so every library ties. Confident slots are settled first; sparse slots break
ties toward the family already chosen.

An `.RRI` can be structurally unable to name a slot: the 8- and 16-slot variants cannot
express slots 16-31. A real Tiger1 has 289 faces in slot 16 against a 16-slot RRI.

## SETTLED — atlas geometry

- Atlas bitmap is always **256 x 4096**.
- An entry occupies `(posX*16, posY*16)` with size `(sizeX, sizeY)`, **top-down**.
  From `ImageLibUnit.pas`: `startX := posX*16; startY := posY*16;` then reading
  `Picture.Bitmap.canvas.pixels[startX+x, startY+y]`.
- Each entry is uploaded to the renderer as its **own texture** (`rrSendTexturePart` ->
  `halSendTextureBMP`), decomposed into 16x16 blocks. So per-face UVs are relative to the
  entry, not the whole atlas.
- `.TLB` files come in three sizes: **461,064** (table only), **1,510,718** (+ an embedded
  256x4096 8-bit BMP), **3,606,846** (+ an embedded 24-bit BMP). The entry table is
  identical in all three, so reading the first 461,064 bytes is correct. 64 of 580+ real
  libraries embed a bitmap.
- An embedded bitmap can differ from its sibling `_8.BMP` in 100% of bytes and still be
  **the same artwork with a different palette** - do not treat byte-difference as
  evidence of different content.
- `.TLB` palette: 256 entries of `[R,G,B,0]` in the first 1024 bytes of the 2048-byte
  block, rest zero. **Reverse of BMP's `[B,G,R,0]`.** From `rrSendTexturePal()`.

## OPEN — how a face's texture coordinates are derived

**This is the unsolved problem and the cause of "jumbled textures".**

Measured on `Italy_Obj/Tiger1.RRF` (4,785 textured faces):

- **Every face has all-zero corner bytes.** No explicit per-face UVs.
- **`attribVList` is 96.6% zero** - not carrying UVs either.
- 4,256 faces reference **two** entries with near-identical size and origin.
- `materialInfo` bits 8-15 give a crop size (`((nibble)+1)*16`), per `rrSetTexture()`:
  `inf |= (((sx>>4)-1)<<8) | (((sy>>4)-1)<<12)`.
- `rrUsedSelection()`'s all-zero branch reads:
  `SizeX=((TexInfo2>>4)+1)*16; StartX=((TexInfo>>20)&0xf)*16; StartY=((TexInfo>>16)&0xf)*16`.

**Why the obvious reading is wrong**: entry 74 of `Italy1` is 32x128 at atlas (128,768),
yet faces claim a 64x48 crop at origin (64,0) - predicting atlas x 192-240, entirely
outside the entry. Scoring all four nibble-order combinations by `origin+size <= entry
size` gives fit rates of only **13-22%**; a correct reading would fit ~always.

Note the fit test is **biased toward libraries with large entries** and must not be used
alone to pick a library (`CustomA3`-family scored 94% partly because its entries are
64x192).

The renderer consumes per-vertex float `tu/tv` (`WVERTEX`, `WingsHAL.h`) and
`halRenderFaces()` receives only the sort list, so **the HAL computes the coordinates
itself**. `OBJHALX5.dll` has **no source in any available archive** - `rrobjpex`,
`ObjEdit`, `Select`, `VisualAI`, `mod_enabler2` and `Particle_Editor` were all checked.

### The checkerboard experiment (set up, not yet read)

`Italy_Obj/Tiger1_ChkTest.RRF` + `.RRI`, with `Texture/ChkTest.TLB` + `ChkTest_8.BMP`:
a real entry table whose atlas pixels are a labelled 32px grid (`A0`..`H127`). Loading it
in ObjEdit and reading the labels off a face gives the mapping directly, with no reliance
on the HAL source.

**Gotcha found on the first attempt**: ObjEdit does `chdir(maindir)` before resolving the
`.RRI`, so relative paths like `texture\ChkTest.TLB` resolve against **ObjEdit's own
folder**, not the model's. The test model loaded untextured (flat grey) for that reason.
Put the library where ObjEdit will look, or use an absolute path in the `.RRI` - real
files contain both styles (`texture\customa1.tlb` and
`D:\Games\Panzer Elite\Texture\CustomA14.TLB`).

## Process notes worth keeping

- **Run the unmodified baseline first** when a real tool rejects a generated file. Hours
  went into a PantherG crash hunt whose crashes were environmental - that model would not
  load in that ObjEdit setup even as a byte-identical copy of an untouched file.
- **A missing `.RRI` makes ObjEdit load a model with no textures at all** ("No RRI file
  found, No auto load of textures!"), and the renderer then dereferences texture pointers
  that were never populated.
- Test artifacts written into `Texture/` pollute auto-detect for **every** model. Clean up.


---

## UPDATE 2026-08-13: explicit corners are origin+size, not four positions

Two corrections to what is written above.

### 1. Explicit corners are the MAJORITY case, not a rarity

An earlier note in this project claimed "0/1490 faces have non-zero corners" from a
five-vehicle sample. Scanning the whole install instead: **5,198,380 of 7,437,702
textured faces (69.9%) carry explicit corners, across 928 models.** The all-zero fallback
is the minority path. The Italy Tiger, which drove the earlier investigation, happens to
be one of the all-zero models - which is why it looked like the normal case.

### 2. The four corner fields are a mix of ORIGIN and SIZE

`rrUsedSelection()` reads them as:

```c
StartX = uvFace->v3 & 0xFF;            SizeX = uvFace->v1 & 0xFF;
StartY = (uvFace->v1 & 0xFF00) >> 8;   SizeY = (uvFace->v3 & 0xFF00) >> 8;
// each incremented when non-zero
```

matching how `rrSetTexture()` writes them (`xStart = X-1`, `xSize = sx-1`). So `v1.x` is a
**size**, not a right-edge coordinate. This importer treated all four fields as literal
corner positions, which puts the crop in the wrong place on every face carrying real
corner data - i.e. on the 69.9% majority.

Fixed in v0.42.0. On a real Sherman (`M4a376HV.RRF`, 94% explicit-corner faces) the
turret went from largely magenta to properly camouflaged and the running gear gained real
detail.

### Still imperfect

Some hull faces on that Sherman still land on magenta. Magenta is the atlas's own filler
between packed entries (20.9% of `CustomA13`'s atlas), so those faces are still being
mapped outside their intended rectangle. The `+1`-when-non-zero convention is implemented;
what remains is likely a further detail of the same rect maths, or the interaction with
entries whose `posX/posY` place them elsewhere in the atlas.

The all-zero fallback (the Italy Tiger) is unchanged and still unexplained - see the
open section above.

---

## UPDATE 2026-08-13 (later): the crop origin was being read from the wrong bits

Settled by calling the real engine headlessly (`tools/headless_oracle`) rather than by
inference. This supersedes the "OPEN - how a face's texture coordinates are derived"
section above for the all-zero-corner case.

### The stored layout

A face's `textureOfset` is written by `rrSetTextureSelection()` as

```c
texture = rrTextLibPartIDHALStarts[i] | (orgY<<28) | (orgX<<24);
```

and read back by `rrGetSelection()` as

```c
xOfset = (textureOfset>>24)&0xf;
yOfset = (textureOfset>>28)&0xf;
```

so the crop origin is at **bits 24-27 (X) and 28-31 (Y)**, in 16px units - *not* bits
16-23. Bits 16-23 are high bits of the texture id.

**Bit 31 is the "is textured" flag**, so only bits 28-30 actually carry Y. The engine's
own `>>28 & 0xf` swallows that flag and reports a bogus `yOfset` of 8 for every textured
face. Harmless inside ObjEdit; must not be copied.

### Where the old reading came from

`rrUsedSelection()`'s all-zero branch really does read `((TexInfo>>20)&0xf)*16`. But
`TexInfo` there is the **selection query word built by ObjEdit's UI**, not the face's
stored `textureOfset`. Two different packings; this project conflated them. The same
conflation made the v0.42.0 explicit-corner comment cite the wrong variant (the code was
right, because `_corner_xy()` already applies the `>>16`/`>>24` shift).

### Measured

On models with a real `.RRI` (so libraries resolve to the correct slot, not a merged
all-libraries dict), across 10,614 all-zero-corner faces:

| reading | crop lands inside its own entry | invents a non-zero origin |
|---|---|---|
| bits 24-27 X / 28-30 Y (correct) | **99.9%** | 0.0% |
| bits 16-23 (what the importer did) | 74.7% | **51.3%** |
| bits 24-31 raw, flag not masked | 28.1% | 100.0% |

Fixed in v0.43.0.

**Caveat, stated honestly:** on this sample the correct reading yields origin (0,0) for
every face, so it ties exactly with a hardcoded (0,0) and does **not** independently
prove the X nibble's position. What it does prove is that the old reading was fabricating
offsets on half of all faces. A model that genuinely uses a non-zero crop origin is still
wanted as a confirming case.

### Also corrected

The texture id decode was re-tested against the same ground truth. A 24-bit
`id = textureOfset & 0xFFFFFF` reading resolves only 13.6% of faces; the existing
bits 0-11 + slot bits 12-15 decode resolves 52.0%. **The existing decode stays.** Note
that its earlier "verified by round-tripping 115,613 faces with zero mismatches" evidence
showed only that decode/encode are inverses - reversibility, not correctness.

### Visually confirmed on the Italy Tiger

Rendered `Italy_Obj/Tiger1.RRF` from the same camera under both readings. **8.56% of
pixels differ, max channel delta 159** - and the difference is the whole point: the old
reading produced the smeared, vertically-streaked hull this project has been chasing,
while the new one resolves hatches, vision ports, weld seams, road-wheel detail and
proper camouflage mottling.

The mechanism is visible in the numbers. On this model the old reading invented a
non-zero origin on 1,263 of 4,785 faces (26%), with Y values of 32/64/160px. The
importer then clamps `start_y` to `sizeY - 1` and takes `crop_y = min(crop_h, sizeY -
start_y)`, so an origin past the entry's height collapses the crop to a **1-pixel-tall
strip** stretched over the whole face - exactly the streaking.

### Testing trap that produced a false negative first

Blender caches an addon as `scripts/addons/__pycache__/io_import_rrf.cpython-*.pyc`.
A/B-ing two variants by overwriting the `.py` between runs silently ran the **same stale
bytecode twice** and reported a perfect 0-pixel difference. Delete the `.pyc` between
runs, and treat "the change had literally zero effect" as a signal to check the cache
before believing it.

---

## UPDATE 2026-08-13: the colour key was failing on near-white art (v0.44.0)

Found by rendering `CustomA/Sdkfz184.RRF` side-on and comparing against ObjEdit's own
3D View of the same model. Every road wheel and drive sprocket sat on an opaque white
box that ObjEdit does not show.

**Cause: a colour-space mismatch, not a UV bug.** The key colour is compared against the
Image Texture node's `Color` output, which Blender delivers in **linear** space, but the
threshold was chosen for 0-255 sRGB artwork. PE key pixels are commonly *near*-white
rather than pure white - CustomA1's road-wheel entry 391 keys on `(250,250,250)`. In
sRGB that is 0.034 from white, comfortably inside the old 0.05 threshold; converted to
linear it is 0.9559, a distance of **0.0764 - outside it**. So the key silently did
nothing on exactly the art that needed it.

Threshold raised to **0.12**, which covers keys down to about sRGB 245 while staying far
from real paint (the lightest sand camo on this model is ~`(210,190,150)`, a linear
distance of ~0.93).

### What this model also settled

- **Sdkfz184 has 0 explicit-corner faces** - all 396 take the all-zero path, like the
  Italy Tiger. Explicit corners are common install-wide but absent from whole models.
- **All 396 faces sit in slot 0**, so only one library matters here.
- The **casemate roof really does tile**: 22 faces share texture id 9, every one a 16x16
  crop at origin (0,0) inside a 64x64 entry. They are *meant* to be identical, and
  ObjEdit's own render shows the same repeating grid. Not a bug - do not "fix" it.
- The only crop overflows (24 faces, 6.1%) are Track faces, whose 48x192 crop against a
  64x64 entry is deliberate tiling.
- Auto-detect flagged itself as **not confident** on `CustomA1.TLB` (CustomC1 scores 98%
  too). Worth remembering when judging any remaining colour difference on this model.

---

## SOLVED 2026-08-13: per-face texture ORIENTATION (v0.45.0)

User observation on `CustomA/Sdkfz184.RRF`, comparing Blender against ObjEdit's own
render: the casemate and the gun barrel are rotated 90 degrees. Rotating the all-zero
corner order by one step (v1 = top-left instead of top-right) makes **the casemate
perfect but breaks everything else** - the hull Balkenkreuz degrades into an unreadable
striped bar. So orientation is **per face**, not a global convention, and one piece is
still missing.

### What orientation is stored in

`rrRotateTexture()` (Rrdwire.c) rotates a face's texture purely by **permuting the vertex
indices** `v1 -> v2 -> v3 -> v4`, touching no UV value and no flag. So the face's vertex
order *is* the orientation.

### Ruled out

- **A global corner-order rotation.** All four were rendered; each fixes some faces and
  breaks others.
- **A materialInfo rotation flag.** `Object.h` defines only `MAT_SHADING_MASK` (bits
  0-1), `MAT_TEXTRUE_MASK` (bits 2-3, values NO=0 / NORMAL=8), `MAT_QUAD` (16),
  `MAT_TWOSIDE` (32). Bits 6-7 are **0 on all 396 faces**. `textureHalf` upper 16 is 0 on
  all 396.
- **textureOfset bits 16-31 as a linear tile index**, per rrSetTextureSelection()'s own
  commented-out original (`ofset/(sizeX>>4)`, `ofset%(sizeX>>4)`). Tried in both axis
  orders: **far worse** than origin (0,0) - road wheels and hull markings vanish. The
  engine comments it out as "error with chris tracks", so the shipped renderer does not
  use it either. The value is still parsed and carried in `face_crop_size[4]`, unused.

### The cause

`_recalculate_normals()` reverses the winding of any face whose normal disagreed with its
neighbours - **27 of 380 faces on Sdkfz184**, measured as reversals (0 were pure
rotations), concentrated in `Main_Gun`. The UV assignment then zipped the corner list onto
`poly.loop_indices` *positionally*, so a reversed loop received mirrored UVs. On striped
camo a mirror reads as a 90 degree rotation - precisely what was reported.

### The fix (v0.45.0)

Bind each corner to the **file's vertex index**, not to the loop's position:

```python
file_face = part.faces[poly.index]
corner_of_vertex = {vidx: xy for vidx, xy in zip(file_face, corners)}
for loop_index in poly.loop_indices:
    lx, ly = corner_of_vertex[mesh.loops[loop_index].vertex_index]
```

This is correct by construction rather than by luck: PE stores orientation *in* the vertex
order, so corner i belongs to file vertex i permanently, whatever Blender later does to
the winding. Faces that repeat a vertex index fall back to positional order.

Changed 6.05% of pixels on Sdkfz184: the gun barrel gained its camo banding instead of a
smooth lengthwise gradient, and the casemate stripes took on the organic wavy shape
ObjEdit shows. On the Italy Tiger the turret's red tactical number renders as a readable
**212** where it was previously a garbled smear - the signature of an un-mirrored texture.
No change in resolution rates (Tiger 4785/0 unresolved, Sherman 885/0).
