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

**Important**: each atlas is shared across every vehicle/object that uses any part of
that library, not just the one model you imported (see
[PAINT_AND_EXPORT_SCOPING.md](PAINT_AND_EXPORT_SCOPING.md)). Painting on the image
in Blender only affects Blender's copy — nothing changes on disk until you export — but
when you do export, think about whether you want to replace the shared atlas everywhere
it's used, or save to a new filename instead.

To export: File > Export > Panzer Elite Texture Atlas (.bmp), pick the atlas Image you
painted (there's a search dropdown), and choose where to save. From the Python console:

```python
bpy.ops.export_scene.pe_rrf_atlas(filepath=r"...\Texture\CustomB1_24.bmp", image_name="CustomB1_8.BMP")
```

This only covers **repainting within the model's existing texture assignments** — no
`.RRF`/`.TLB` changes, just a replacement 24-bit `.BMP` the game's loader will prefer
over the paletted `_8.BMP` fallback. Save it as `<name>_24.BMP` next to the `.TLB` for
the game/ObjEdit to pick it up.

**Not yet tested against the real game or ObjEdit.** What's been checked so far is
limited to an automated pixel comparison of the round-tripped file (painted regions match
what was painted, untouched regions match the source, output is a standard 24-bit BMP at
the expected 256×4096 size) — nobody has actually loaded an exported file in ObjEdit or
the game yet to confirm it's accepted and displays correctly.

Adding brand new texture regions (new UV layout, new `.TLB` entries) isn't supported —
see Scenario B in [PAINT_AND_EXPORT_SCOPING.md](PAINT_AND_EXPORT_SCOPING.md).

## Known limitations

- Only the highest-detail LOD level is imported (appropriate for editing/painting; not a
  full multi-LOD round trip).
- Export only covers repainting existing texture assignments (Scenario A) — no new
  `.TLB` entries or `.RRF` changes; see
  [PAINT_AND_EXPORT_SCOPING.md](PAINT_AND_EXPORT_SCOPING.md).
- Auto-detect (used when there's no `.RRI` and no hand-supplied `.TLB`) only tries the
  single best-scoring library in the search folder. Models that genuinely draw from
  several libraries at once (common on larger/older vehicles) will resolve far fewer
  faces under auto-detect than they would with their real `.RRI` present — this is a
  real gap in the auto-detect strategy, not a property of the saved data (see
  [TEXTURE_ID_RESOLUTION.md](TEXTURE_ID_RESOLUTION.md)). Prefer keeping/restoring a
  model's `.RRI` file whenever one exists.
- A small number of individual texture IDs may still fail to resolve even with the
  correct library — in practice a handful of faces at most, likely stale/removed `.TLB`
  entries rather than a systematic limitation.
