# Plan: query the real engine headlessly for ground-truth face UVs

**Status: BUILT AND WORKING (2026-08-13).** See `tools/headless_oracle/`. Every risk
listed below turned out to be surmountable: `rrInitRender()` initialises headless and
pulls in `OBJHALX5.DLL` by itself, no window or HDC needed, and `rrnop.dll` was never
required. It has already settled one long-standing question - see the crop-origin update
in `TEXTURE_PIPELINE_FINDINGS.md`. Two corrections to the plan as written: the exported
`rrGetUsedSelection` takes `uvFace` as its **3rd** argument, not its 5th (the internal
`rrUsedSelection` differs), and `Setting.HAL` is absent from the OE_2 folder yet the HAL
still loads - the shipped `rrobjx5.dll` is not built from the `rrobjpex` source tree.

Original plan follows. User's idea, sharpened after checking
feasibility. Goal: stop guessing how a face's texture rectangle is derived, and stop
needing manual ObjEdit loads (slow to drive) to find out.

## The target is the DLL, not ObjEdit

`rrobjx5.dll` (in the user's working ObjEdit folder,
`M:\Users\jeff\Desktop\Old Desktop\OE_2\`) **exports the exact function this project has
been trying to reverse**. Verified by parsing the PE export table - note the leading
underscore and `@N` stdcall decoration, which is why a naive name search says "not
exported":

```
_rrGetUsedSelection@20     -> rrUsedSelection(): returns a face's real texture rect
_rrLoadGameMesh@4          _rrSetupTextureLib@8
_rrSendTexturePart@32      _rrSendTexturePal@12
_rrInitRender@0            _rrSetRenderSize@8      _rrTestGameMesh@8
```

131 exports in total. Calling `rrGetUsedSelection` per face makes the engine state its
own answer, numerically, for thousands of faces in seconds.

## The blocker

**Every one of these DLLs is 32-bit x86** (verified from the PE machine field:
`rrobjx5.dll`, `rrnop.dll`, `OBJHALX5.dll`, `meshconv.dll` all `0x14c`). Every Python on
this machine is **64-bit** (`Python313` confirmed). ctypes cannot cross that boundary, so
this needs a 32-bit host.

## Steps

1. **Install a 32-bit Python** (~30MB). Put it on `K:` - never `C:`, see
   [[feedback_c_drive_space_constraint]].
2. **Load `rrobjx5.dll` with the working directory set to the OE_2 folder**, so its
   sibling DLLs and `Setting.HAL` resolve exactly as they do for the real ObjEdit.
3. **Init**: `rrInitRender`. If it demands a real device, retry against **`rrnop.dll`**
   (710KB, sitting in the same folder - almost certainly the no-op HAL built for this).
4. **Load a model**: `rrLoadGameMesh(path)`.
5. **Populate the texture table**: `rrSetupTextureLib` + `rrSendTexturePal` +
   `rrSendTexturePart`. Required, not optional - `rrUsedSelection()` returns early if the
   face's id is not found in `rrTextLibPartIDs`, which only those calls fill.
6. **Query**: build an `rrSelInfo` array (layout known: `TSelectInfo` = 4 x Longint, from
   `ViewUnit.pas` - mode, objNo, idNo, textureId) and call `rrGetUsedSelection`, reading
   back the `rrUVFace` it fills. Signature is in `Rrdwire.c`:
   `rrUsedSelection(int32 *count, rrSelInfo *list, int32 TexInfo, int32 TexInfo2, rrUVFace *uvFace)`.
7. **Diff** the returned rectangles against what `io_import_rrf.py` computes for the same
   faces. Any disagreement is the bug, expressed as numbers rather than a visual guess.

## Risks, stated honestly

- `rrInitRender` may require a window/HDC. Mitigations: `rrnop.dll`, or create a hidden
  window via ctypes. The user's ObjEdit **does** initialise on this machine, so the HAL
  works here.
- Texture upload runs through the HAL (`rrSendTexturePart` -> `halSendTextureBMP`), same
  mitigation.
- If it will not init headless: an hour lost, nothing damaged - every call listed here
  only reads, and the model is loaded from a copy.

## Why it is worth it beyond this bug

It turns "load it in ObjEdit and squint" into a repeatable numerical oracle for any
model. That is permanently useful for validating the writer, the crop maths, and any
future authoring work - and it takes ObjEdit off the critical path, which matters because
driving it is slow.

## Cheaper fallback (~10 minutes)

The checkerboard experiment is already built (`Italy_Obj/Tiger1_ChkTest.RRF`). It loaded
untextured only because ObjEdit `chdir(maindir)`s before resolving the `.RRI`, so
`texture\ChkTest.TLB` resolved against ObjEdit's own folder. Fix by putting the library in
**OE_2's Texture folder** or using an absolute path in the `.RRI`, then read the grid
labels off a face. One manual load, visual reading.
