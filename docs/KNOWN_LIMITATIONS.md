# Known limitations this plugin cannot fix from `.RRF`/`.TLB` files alone

Two real failure modes surfaced during "which BMP is actually on this model" debugging
that look, at first glance, like texture-resolution bugs but aren't — the correct
answer for both lives in files this plugin was never designed to read, and no amount of
`.RRF`/`.TLB` analysis can recover it. Documented here so this doesn't get
re-discovered the hard way again next time; see
[TEXTURE_ID_RESOLUTION.md](TEXTURE_ID_RESOLUTION.md) for the resolution logic that *is*
fixable and was fixed.

## Per-unit `Modification` skin overrides in `.scn` files

A specific in-game unit instance can carry a `Modification` field in its mission's
`.scn` file that silently overrides which texture library it actually renders with —
independent of whatever the model's own generic `.RRI` says.

Confirmed on the PantherG "II01" unit (Normandy_Obj, `brit44 crossroads` mission): its
own `.RRI` correctly names `CustomA1.TLB` as the model's generic library, but the
scenario's `Modification0:5` field made this specific unit instance render with
`CustomA9.TLB` instead. Nothing in the `.RRF`, `.TLB`, or `.RRI` files hints at this —
the override lives entirely in mission data this plugin never opens.

**Why this is out of scope**: fixing it would mean parsing `.scn` mission files and
cross-referencing a specific unit ID to a specific `Modification` value — a
fundamentally different, much larger input than "here's a model file," and one that
only matters if you already know which mission and which specific unit instance you're
trying to match. Flag as a candidate for a genuinely separate feature only if there's
ever a concrete, recurring need to match a specific in-mission unit's exact skin rather
than the model's generic one.

**Workaround**: if what you actually need is "what does unit X look like in mission Y,"
check the mission's `.scn` for that unit's `Modification` value by hand, not just the
model's own `.RRI`/auto-detect result.

## The same model identifier can mean a different real vehicle depending on active mod

Confirmed on "Pz4E" (Desert_Obj): under MichaelY's own code mod, the identifiers
"Pz4E"/"Pz4F2" displayed in-game as "Panzer IV F"/"Panzer IV G1" and rendered as
long-barreled tanks — not the short-barrel Ausf E that the actual `Pz4E.RRF` file on
disk depicts. Auto-detect's texture-library guess for that file was, in isolation,
completely correct (a clean, consistent 100% match) — the file-level answer was right
and the real-world answer was still wrong, because the question "which vehicle is
`Pz4E` right now" depends on which mod's `units.csv` is currently active, a fact no
`.RRF`/`.TLB`/`.RRI` file can express.

**Why this is out of scope**: which mod is enabled is install/environment state (see
the JSGME-style Mod Enabler pattern — enabling a mod overwrites live files, backing up
the previous version under a `.<ModName>` suffix), not something a file path alone can
introspect. The plugin has no reliable way to know, from `Pz4E.RRF`'s bytes, which
mod's `units.csv` was active when that file was last the "live" one, or whether it
still is.

**Workaround — process, not code**: always re-derive ground truth fresh (ideally via a
genuine `.RRI`, or by checking in-game) whenever the mod state might have changed,
rather than trusting a result carried over from an earlier session under a different
mod. Don't assume a Data_Name identifier means the same real vehicle it meant last
time you checked.

## Private-skin texture stretches on non-rectangular faces, independent of writer correctness

Giving a part a private skin (`MESH_OT_pe_give_private_skin`) and painting a labeled
checkerboard onto it to audit UV quality showed severe banding/stretching in real
ObjEdit on `88Pak43.RRF` (Normandy_Obj) - clean in Blender's own preview, badly warped
in OE, especially on the barrel and other tapered/non-rectangular panels.

Investigated three different corner-writing approaches in `apply_private_skin()`
(2026-07-08), tested against the user's real `PEx_105_ObjEdit.exe` at each step -
collapsing each face to a bounding box under a fixed corner-role convention, forcing
each face into its own small grid-tiled rectangle under that same convention, and
finally writing each vertex's own independent (x,y) position directly (the version
currently shipped, believed correct - see `TODO.md` for the full reasoning chain). None
resolved the checkerboard stretching.

**Why this is (probably) not fixable as a writer bug**: many real faces on this mesh
simply aren't rectangular/square in 3D - tapered armor plates, trapezoidal panels. Any
texture mapped across a non-rectangular polygon gets stretched toward its longest
vertex via ordinary UV interpolation, completely independent of which corner-encoding
convention sits underneath the data. A checkerboard is a uniquely harsh test pattern for
this (straight grid lines make interpolation skew glaringly visible) in a way a soft
continuous camo texture never would - real painted content might look fine even though
the checkerboard test doesn't.

**Not yet confirmed either way** - parked before testing with a more representative
texture. If revisited: (a) retest with the part's own real borrowed camo texture (or a
plain gradient) instead of a checkerboard, cheap and fast, would confirm or rule out
this reframing directly; (b) if genuinely still a problem with realistic textures too,
the real fix is re-seaming the worst non-rectangular faces in Blender so they're closer
to true rectangles - a mesh-topology change, not a writer change.

---

## SOLVED (diagnosis): the private-skin stretching is non-rectangular UVs

The long-parked "private-skin texture stretches on non-rectangular faces, possibly
unfixable" is now measured rather than guessed, using the labelled UV grid.

Same merged model, same library, rendered both ways: **Blender shows crisp labelled cells;
ObjEdit smears them into streaks.** Auditing the UV corners explains it exactly:

| model | textured faces | axis-aligned rectangles | non-rectangular |
|---|---|---|---|
| stock `Psw222.RRF` | 137 | **137 (100%)** | 0 |
| our private-skin merge | 141 | 46 (33%) | **95 (67%)** |

**Every face in real PE content is an axis-aligned rectangle - all 137, no exceptions.**
Smart UV Project produces islands whose faces are not, and the engine cannot represent
them: it reduces a face to origin+size and stretches the crop across it. Blender
interpolates the four corners properly, which is why it looks right there and only there.

So this was never an engine bug or an interpolation subtlety. **The format simply cannot
express a non-rectangular face mapping**, and the private-skin path has been feeding it
exactly that.

### The fix direction

`plan_private_skin`/`apply_private_skin` must give every face an **axis-aligned rectangle**,
not an island-relative UV polygon - i.e. do what stock content does. Two options:

1. **Snap to bounding box** - keep the island packing, but write each face's UV bbox
   corners rather than its true corners. Cheap; slightly distorts faces whose unwrapped
   shape is not a rectangle, but renders correctly in-engine.
2. **A rectangle per face** - allocate each face its own atlas rectangle sized to its real
   proportions, exactly as stock content is authored. More atlas pressure, no distortion,
   and no seams shared between faces.

Option 1 is a small change and testable immediately with the grid: re-audit should read
100% rectangular, and ObjEdit's render should go crisp. Option 2 is the faithful one.

### Test that proves it either way

The labelled grid (`tools/uvtest/make_uvtest.py`, or `tools/merge_private_skins.py` +
`grid_into.py`) makes this a one-look check: crisp readable cells in ObjEdit means the
mapping is expressible; streaks mean it is not.

### Option 1 implemented (v0.54.0): snap every face to its UV bounding box

`apply_private_skin()` now snaps each vertex to the nearer edge of its own face's UV
bounding box before writing the corners. That forces an axis-aligned rectangle while
keeping each vertex on the corner it already occupied, so orientation and winding survive.

Result, re-audited on the same five-part merge:

| model | textured faces | axis-aligned rectangles |
|---|---|---|
| stock `Psw222.RRF` | 137 | 137 (100%) |
| private-skin merge, v0.53.0 | 141 | 46 (33%) |
| **private-skin merge, v0.54.0** | 141 | **141 (100%)** |

Now structurally identical to stock content. Faces whose unwrapped shape was not
rectangular are mildly distorted - unavoidable, because the format cannot express them at
all; the alternative was smearing.

**Awaiting the ObjEdit check**: the labelled grid should now read as crisp cells rather
than streaks. If it does, option 2 (a rectangle per face, sized to real proportions, as a
PE artist would author it) becomes a refinement rather than a gamble.

### v0.55.0: the actual bug - corners were written as POSITIONS, not ORIGIN+SIZE

Snapping to rectangles (v0.54.0) was necessary but not sufficient: ObjEdit still smeared.
The source answered it. `rrSetTexture()` (Rrdwire.c) packs a face's crop as **origin and
size**:

```c
xStart = X - 1 (when X != 0);   xSize = sx - 1
v1 = idx | (yStart<<24) | (xSize <<16)
v2 = idx | (yStart<<24) | (xStart<<16)
v3 = idx | (ySize <<24) | (xStart<<16)
textureHalf = idx | (ySize<<24) | (xSize<<16)     [quads]
```

and `rrUsedSelection()` reads it straight back that way. **Both writers in this add-on
packed positions instead** - `patch_face_corners()` put `max_x`, the right edge, into v1
where the engine expects a width. A 50px-wide face sitting at x=200 therefore declared a
200px crop, and the texture stretched across it. The two only coincide when a face starts
at x=0, which is why some faces always looked fine.

Fixed in `patch_face_corners()`, and `apply_private_skin()` now routes its
already-rectangular faces through it rather than writing per-vertex positions.

Verified by reading every face back **the engine's own way** (rrUsedSelection's explicit
branch): **141 of 141 crops fit their entry, 0 do not.** User confirms the wheels are no
longer smeared in ObjEdit, and the Blender render went from streaks to crisp labelled
cells.

**Correction to an earlier claim in this document:** it said Blender never reads the corner
bytes back because `apply_private_skin` sets loop UVs directly. That is true only within
the session that writes them - a fresh *import* of the saved .RRF does read them, and did
show the same smearing. The renders were of re-imported files all along.

### Option 2 implemented (v0.56.0): a rectangle PER FACE

Option 1 rendered crisp but left faces treading on each other: sharing an island's entry
and then snapping each face to its bounding box makes adjacent triangles claim overlapping
atlas pixels (a triangle's box is much larger than the triangle). **98 overlapping
face-pairs** on a five-part Psw222 - so painting one face silently altered its neighbour.

`plan_private_skin(..., per_face=True)` now treats every face as a one-face island, reusing
the existing sizing, shelf-packing and writing paths unchanged.

| | islands (v0.55.0) | per face (v0.56.0) |
|---|---|---|
| library entries | 73 | 141 |
| axis-aligned rectangles | 141 / 141 | 141 / 141 |
| **overlapping face pairs** | **98** | **0** |
| atlas used | 39% | 42% |

Barely more atlas for complete isolation, and it matches how stock PE content is authored.

**The trade, stated plainly:** there are no continuous seams across a surface any more -
texture cannot flow from one face into the next within the atlas. Painting must be done in
the **3D viewport** (Texture Paint), where Blender projects the brush through each face's
own UVs and a stroke crossing an edge paints both faces correctly; Blender's Bleed setting
(~2px) covers the edges. Painting the flat atlas in the 2D image editor is not practical
with per-face rectangles - it is 141 disconnected patches.

`per_face` is an operator property, default **False**, so the island behaviour is
unchanged for anyone who wants it. `tools/auto_skin.py` passes `per_face=True`.

### v0.57.0: texel density - size by real surface area, and split the budget by it

User report: "some faces are large but have a small island and vice versa". Measured as
texels per unit of 3D area, our spread was **637x**. Two causes, and only the second
mattered much:

1. **`size_islands_to_tiles()` weighted islands by UV bbox area**, a poor proxy once faces
   are snapped to rectangles - a 6.75-area face and a 0.55-area face both got 297 texels.
   Now takes `area_weights` and `plan_private_skin()` passes each island's real
   `calc_area()`. On its own this only moved 637x -> 604x.

2. **The atlas budget was split EQUALLY between parts.** A 4-face machine gun received the
   same share as a 106-face hull, so its tiny faces each landed an enormous rectangle -
   27,989 texels per unit against the hull's ~150. `tools/auto_skin.py` now splits the
   budget by each part's surface area:

```
Psw222 (hull)  area 809.1  budget 0.4321
Wheel          area 160.3  budget 0.0856
Turret         area  53.6  budget 0.0286
Main_Gun       area   5.8  budget 0.0031
Turret_MG      area   1.0  budget 0.0005
```

**Result: 637x -> 52x**, which is more consistent than stock PE content (759x). Stock is
uneven by an artist's choice per face; ours had been uneven by accident.

Still 141 entries, 141 axis-aligned rectangles, 0 overlapping face pairs, 0 unresolved.
