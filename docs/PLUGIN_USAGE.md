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

Texture resolution is automatic and needs no extra arguments if a `.RRI` sits next to
the model (see [RRI_FORMAT.md](RRI_FORMAT.md)) — the importer finds and uses it. To
override:

```python
# Force a specific .TLB (skips .RRI/auto-detect)
bpy.ops.import_scene.pe_rrf(filepath=r"...\Model.RRF", tlb_filepath=r"...\Some.TLB")

# No .RRI available - scan a folder and auto-pick the best-matching .TLB
bpy.ops.import_scene.pe_rrf(filepath=r"...\Model.RRF", tlb_search_folder=r"...\Texture")
```

Priority order: `tlb_filepath` (manual) > `.RRI` (if present and `use_rri` is on,
default) > `tlb_search_folder` (fallback) > geometry only.

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

## Known limitations

- Only the highest-detail LOD level is imported (appropriate for editing/painting; not a
  full multi-LOD round trip).
- No export/re-save to `.RRF`/`.TLB` yet — see
  [PAINT_AND_EXPORT_SCOPING.md](PAINT_AND_EXPORT_SCOPING.md).
- Texture resolution for models without a `.RRI` and without a hand-supplied `.TLB` is
  best-effort; some faces on older content may never resolve (this is a property of the
  saved data, not the importer — see [TEXTURE_ID_RESOLUTION.md](TEXTURE_ID_RESOLUTION.md)).
