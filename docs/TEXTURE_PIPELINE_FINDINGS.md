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
