# Gap analysis: this plugin vs. the real ObjEdit

**Compiled 2026-08-12** by enumerating the real tool's whole feature surface from source
- all 68 `rrobjx5.dll` exports in `rrobjpex.c`, and every dialog unit in ObjEdit's Delphi
project - and checking each against what this add-on actually does. Not a wishlist: each
entry names the real function or unit behind it.

## Where the plugin already stands

| Area | State |
|---|---|
| Import geometry / hierarchy / pivots / attributes / UVs / textures | done |
| Texture library resolution (`.RRI`, auto-detect with honest confidence) | done |
| Per-face texture edits (detach, crop, flip) | done |
| Whole-part private skin (`.TLB` + `.BMP` with a real palette) | done |
| Paint round-trip to `_8.BMP` | done |
| Geometry write-back incl. added/deleted faces, in place or File > Export | done |
| Collision box regeneration (preserving custom boxes) | done |
| `.RRI` writer | done |

## Real gaps, highest value first

### 1. Gameplay attributes are read-only

`objAttribut` is stamped onto each object as a custom property at import and **never
written back** - the plugin writes exactly one field in the 512-byte part record
(`maxVertex` at +84) plus the collision box. So the part type tags
(`TANK`/`TURM`/`KANNONE`/`MUZZLE`/`HAUS`/`TREE`... from `Rrattrib.h`) and the
`OBJ_ATTRIB_HIDE` flag cannot be set from Blender at all.

Real tool: `rrSetAttributSelection`, `rrAttributeUsing`, `rrSetHideSelection`,
`rrUnhideAll`; `ObjAttributUnit` / `AttributDefUnit` / `AttributUsedUnit`.

This matters for any model that must actually function in game - a turret that isn't
tagged `TURM` is just geometry. **Highest-value gap for a small amount of work**: it's a
single `uint32` per part plus a per-vertex `attribVList` tag that is already carried
through the writer.

### 2. Part and hierarchy editing

No way to add, remove, rename or re-parent a part, or move a pivot. Pivots are read
(`pe_pivot`) and used correctly on write, but never written.

Real tool: `rrAddObject`, `rrRemoveObject`, `rrSetObjName`, `rrSetObjPivot`,
`rrMoveObjPivot`, `rrMoveChildPivot`, `rrMirrorObj`; `AddChildUnit`, `DeleteChildUnit`,
`MovePivotUnit`, `MirrorObjectUnit`, `ObjNameUnit`.

This is the `create_rrf()` / Phase 3 territory already scoped in
[AUTHORING_SCOPING.md](AUTHORING_SCOPING.md).

### 3. Face draw order

Now known to be **hand-authored** per octant (see AUTHORING_SCOPING gap 4) - which makes
a Blender-side equivalent genuinely useful rather than redundant, because there is no
algorithm that can do it for you.

Real tool: `rrSetSortSelection` -> `rrBspTreeEdit`, which moves selected faces one
position earlier or later in the block for the current view direction.

A "PE: Move Face Earlier / Later in Draw Order" pair of operators would be a close
match, and `derive_sort_list()` already handles carrying the result through edits.

### 4. Texture operations still missing

- `rrRotateTextureSelection` - rotate a face's texture in 90-degree steps. The plugin has
  flip but not rotate.
- `rrReNumTLB` (`RenumUnit`) - remap a model's texture references from one library to
  another wholesale. Directly useful for retheatring a vehicle.
- `rrSetTextureSelection` - assign an existing library rectangle to selected faces. The
  plugin can detach and re-crop, but cannot point a face at a chosen cell.

### 5. LOD levels

Only LOD0 is ever read or written; the writer duplicates LOD0 across all 8 slots. That
matches every real file checked, so nothing is currently broken - but the format supports
8 genuine levels and no PE model uses them. Real tool: `rrSetEditLOD`.

### 6. Groups

`groupNameList` / `selGroupArray` in the `.RRI` are written as defaults and otherwise
ignored; ObjEdit uses them as named face-selection sets (`rrSetGroupName`,
`GroupRenameUnit`). Low value - Blender's own vertex groups and selection sets are better
- but round-tripping someone else's groups instead of resetting them would be polite.

### 7. Palette editing

The plugin can read, write and borrow palettes, but cannot edit one. `PalEditUnit` is a
real palette editor. Matters because painting is quantised to a fixed 256-colour palette:
a repaint in colours the borrowed palette lacks will band or shift.

## Not gaps (deliberately)

- **Selection-level geometry ops** - `rrDivideSelection`, `rrMergeSelection`,
  `rrSplitSelection`, `MoveVertexUnit`. Blender's own modelling tools cover these, and the
  write-back path now carries the results into the `.RRF`.
- **3DS import** - `Import3DSUnit` calls `_mcMake` in `meshconv.dll`, a separate converter
  whose source is *not* in the archives. The DLL itself is present next to the working
  ObjEdit, so **a 3DS -> RRF path exists today**: model in Blender, export 3DS, import in
  ObjEdit. Worth knowing as a stopgap for authoring from scratch until `create_rrf()`
  exists.
- **`AnimationUnit` / `BallisticUnit`** - both call no DLL functions at all. Stubs or
  abandoned UI; nothing to match.
- **Render/viewport plumbing** - `rrInitRender`, `rrDisplayRender`, `rrSetRenderSize` and
  friends exist only to drive ObjEdit's own 3D view. Blender is the viewport.

## Worth adding beyond ObjEdit parity

Things the original tool cannot do, roughly in order of value for the work actually
happening on this project:

1. **A validation / lint operator.** Check a model and report: unresolved faces, sortList
   blocks that aren't valid permutations, `maxVertex`/`maxAllVertex` disagreeing with real
   counts, a collision box that no longer contains its mesh, n-gons, vertex counts near
   the 16-bit limit. Every one of those has been a real bug or a real trap during this
   project's own development, and each is a few lines to check.
2. **Whole-vehicle private skin** - one atlas shared across all parts of a model, instead
   of one library per part. Fewer libraries, less atlas waste, and much closer to how real
   vehicles are textured.
3. **LOD generation** - decimate in Blender and write genuine LOD1-7 levels. The format
   has supported this since 1999 and no shipped model uses it; it would be new capability,
   not parity.
4. **Symmetry / mirror with texture awareness** - `rrMirrorObj` exists but mirroring
   geometry without handling the texture corner conventions produces inside-out faces.
5. **RRF-to-RRF diff** - report what actually changed between two models. This project has
   repeatedly needed exactly that and has been writing throwaway scripts for it.
6. **Import/export of the whole vehicle set** - the batch tool already does bulk import;
   the reverse (write a folder of edited models back) is the natural pair.
