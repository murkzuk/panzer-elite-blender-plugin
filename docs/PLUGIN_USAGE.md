# Using the importer

## Install

Blender 3.6+. Edit > Preferences > Add-ons > Install from Disk, point at
`io_import_rrf.py`, enable it.

## Import

File > Import > Panzer Elite Model (.rrf), or from the Scripting tab's Python console
for full control over the optional texture arguments:

```python
bpy.ops.import_scene.pe_rrf(filepath=r"C:\path\to\Model.RRF")
```

Texture resolution needs no extra arguments at all for a typical
`<install root>\<PackFolder>\Model.RRF` layout with a sibling `Texture\` folder (the
common case) — the importer automatically:

1. Uses a companion `.RRI` if one sits next to the model (see
   [RRI_FORMAT.md](RRI_FORMAT.md)) — the precise answer, no searching needed.
2. Otherwise auto-derives the model's sibling `Texture\` folder and scans every `.TLB`
   in it, scoring each by how many of the model's texture IDs it resolves, and uses the
   best match.

To override when needed:

```python
# Force a specific .TLB (skips .RRI/auto-detect entirely)
bpy.ops.import_scene.pe_rrf(filepath=r"...\Model.RRF", tlb_filepath=r"...\Some.TLB")

# Point the auto-detect scan at a different folder than the auto-derived sibling one
bpy.ops.import_scene.pe_rrf(filepath=r"...\Model.RRF", tlb_search_folder=r"...\SomeOtherTexture")
```

Priority order: `tlb_filepath` (manual) > `.RRI` (if present and `use_rri` is on,
default) > `tlb_search_folder` (explicit override, if given) > auto-derived sibling
`Texture\` folder > geometry only.

## What you get

- One Blender object per model part, correctly parented and pivoted (rotating a parent
  correctly carries its children, matching the game's own hierarchy).
- Custom properties per object: `pe_part_index`, `pe_obj_attribut` (raw hex), `pe_type_id`
  and `pe_type_name` (turret/gun/track/crew-position/etc., decoded from the game's part
  attribute tags).
- Parts flagged hidden in the source file (e.g. alternate/interior geometry not meant to
  render normally) import correctly hidden.
- Where texture resolution succeeds: real UVs into the shared atlas bitmap, and a
  material per texture library the model uses (a model can use several at once).
- Where it doesn't (see [TEXTURE_ID_RESOLUTION.md](TEXTURE_ID_RESOLUTION.md)): the face
  is assigned a bright magenta `PE_UNRESOLVED_TEXTURE` material and a `pe_texture_unresolved`
  boolean face attribute, so it's easy to find and select for manual re-texturing (Select
  > Select All by Trait, or the Attribute panel).
- Normals are recalculated from mesh shape on import rather than trusted from the file
  (see [RRF_FORMAT.md](RRF_FORMAT.md) for why).

## Paint and export a repainted texture

Once a model is imported with textures resolved, its atlas image(s) are real Blender
`Image` datablocks tied to materials — switch to the Texture Paint workspace and paint
directly on the model as normal.

If painting seems to do nothing: check the Texture Paint properties panel's "Slots" list
shows the atlas image (not empty). This was a real bug in earlier versions — the
imported material's Image Texture node wasn't marked as the node tree's *active* node,
which is what Blender's Material-mode texture painting actually reads to find a canvas;
without it, `tool_settings.image_paint.canvas` comes back empty and a paint stroke lands
nowhere. Fixed by having the importer select and activate that node itself, so this
should no longer come up on models imported since the fix.

**Important**: each atlas is shared across every vehicle/object that uses any part of
that library, not just the one model you imported (see
[PAINT_AND_EXPORT_SCOPING.md](PAINT_AND_EXPORT_SCOPING.md)). Painting on the image
in Blender only affects Blender's copy — nothing changes on disk until you export — but
when you do export, think about whether you want to replace the shared atlas everywhere
it's used, or save to a new filename instead.

To export: File > Export > Panzer Elite Texture Atlas (.bmp), pick the atlas Image you
painted (there's a search dropdown), and choose where to save. From the Python console:

```python
bpy.ops.export_scene.pe_rrf_atlas(filepath=r"...\Texture\CustomB1_8.bmp", image_name="CustomB1_8.BMP")
```

This only covers **repainting within the model's existing texture assignments** — no
`.RRF`/`.TLB` changes, just a replacement `_8.BMP`. An earlier version of this operator
wrote a 24-bit `_24.BMP` instead, on the assumption the game's loader prefers it over
the paletted `_8.BMP` - confirmed wrong against a real running install (see
[PAINT_AND_EXPORT_SCOPING.md](PAINT_AND_EXPORT_SCOPING.md)), so it now writes `_8.BMP`
directly: it reads the model's real, currently-live `_8.BMP` to get its exact palette,
quantizes your repainted colors against it, and writes a proper 8-bit indexed BMP.
Repainted colors that don't already exist in the fixed 256-color palette land on their
closest available match - expected behavior for this format, not a bug. Save it as
`<name>_8.BMP` next to the `.TLB` for the game to pick it up.

**Verified byte-level against the real palette/row-order rules this format needs** -
not yet tested by loading an actual exported file in the real game. See
[PAINT_AND_EXPORT_SCOPING.md](PAINT_AND_EXPORT_SCOPING.md) for exactly what was checked
and what's still outstanding.

Adding brand new texture regions (new UV layout, new `.TLB` entries) isn't supported —
see Scenario B in [PAINT_AND_EXPORT_SCOPING.md](PAINT_AND_EXPORT_SCOPING.md). One specific
case *is* supported now, though - see below.

## Detach a face from a shared texture cell

Some models reuse the exact same `.TLB` atlas rectangle across more than one face (the
original artist's own space-saving choice) - painting one repaints every other face that
happens to share it, since they're all genuinely looking at the same pixels. To give a
face its own independent copy of the same content:

1. Enter Edit Mode on the model, switch to face select, and select the face(s) you want
   to detach.
2. Right-click for the face context menu (or a properties panel, if you've added one) and
   choose **PE: Detach Face From Shared Texture Cell** - operator id
   `mesh.pe_detach_face_texture`.

```python
bpy.ops.mesh.pe_detach_face_texture()
```

Each selected face gets its own newly-allocated `.TLB` entry (same size as whatever it was
using, populated with a copy of that same content) and its own UV, so it stops sharing a
cell with anything else. Everything else in the model - the untouched original entry, any
other face still using it, every other byte in the file - is left exactly as it was.

**This writes directly to the model's `.RRF` and whichever `.TLB` it resolved through** -
a real, hard-to-reverse-by-hand edit to the actual asset files, not just an in-memory
Blender change. A one-time `.bak` backup of each file is made automatically before the
first edit in a session (it won't overwrite an existing `.bak` on later edits, so it
always reflects the state from before any of this session's changes).

Only covers faces that already resolved a texture (see above) - a magenta/unresolved face
has no real crop rectangle to clone, so there's nothing to detach it onto. Doesn't touch
`.RRI` files.

## Reposition vertices (Phase 1 geometry writer)

Real mesh editing with a write-back to the model's own `.RRF` - reshape a panel line,
adjust a mudguard, tweak a barrel profile - without adding or removing any vertex, face,
or part. See [RRF_WRITER_SCOPING.md](RRF_WRITER_SCOPING.md) for why this is scoped as
"Phase 1" and what a fuller geometry writer (adding/removing faces or parts) still needs.

1. Enter Edit Mode on the model, move one or more vertices however you like - just don't
   add, delete, merge, or split any of them. Vertex *count* for the part must stay exactly
   what it was on import.
2. Right-click for the mesh context menu and choose **PE: Write Vertex Positions** -
   operator id `mesh.pe_write_vertex_positions`.

```python
bpy.ops.mesh.pe_write_vertex_positions()
```

Writes every vertex's current position back into the `.RRF`, in place, in an exact copy
of the file (the same `.bak`-backed-up, surgical-patch approach as every other writer
here) - face count, vertex count, and every other part's data are left completely
untouched. If Blender's own vertex count for the part no longer matches what's recorded
in the file (i.e. you added or removed a vertex), the operator refuses to run rather than
risk writing something the format's still-unconfirmed regions (`sortList`/`attribVList`)
can't actually support yet.

**Move the object itself (Object Mode translate/rotate/scale) separately, if at all** -
this operator only concerns itself with per-vertex positions inside the mesh, converted
back to the file's raw coordinate convention using the part's original pivot (stamped at
import time as the `pe_pivot` custom property, not read from the object's own
`obj.location`, so an accidental Object Mode move afterward can't silently corrupt what
gets written).

## Known limitations

- Only the highest-detail LOD level is imported (appropriate for editing/painting; not a
  full multi-LOD round trip).
- Export only covers repainting existing texture assignments (Scenario A) — no new
  `.TLB` entries; geometry writing is limited to repositioning existing vertices (see
  above) — adding/removing faces or parts isn't supported yet, see
  [RRF_WRITER_SCOPING.md](RRF_WRITER_SCOPING.md) and
  [PAINT_AND_EXPORT_SCOPING.md](PAINT_AND_EXPORT_SCOPING.md).
- Auto-detect (used when there's no `.RRI` and no hand-supplied `.TLB`) is **always**
  reported as low confidence, no matter how clean its score looks — real testing found
  no reliable way to tell a good auto-detect guess from a wrong one by score alone (see
  [TEXTURE_ID_RESOLUTION.md](TEXTURE_ID_RESOLUTION.md#confidence-how-much-to-trust-an-auto-detect-guess)).
  A low-confidence import escalates the operator report to a warning and stamps
  `pe_tlb_confidence = "low"` on the atlas image. Prefer keeping/restoring a model's
  real `.RRI` file whenever one exists — it's stamped `"rri"` and is the authoritative
  answer, not a scored guess.
- A small number of individual texture IDs may still fail to resolve even with the
  correct library — in practice a handful of faces at most, likely stale/removed `.TLB`
  entries rather than a systematic limitation.
- Even a fully-resolved, high-confidence texture match can still be the wrong *vehicle*
  or the wrong *specific unit's skin* — see [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md)
  for two real cases (per-unit `.scn Modification` overrides, and mod-dependent model
  identifiers) that no `.RRF`/`.TLB` analysis can catch.
