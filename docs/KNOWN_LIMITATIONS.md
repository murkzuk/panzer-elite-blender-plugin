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
