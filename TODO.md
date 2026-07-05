# TODO / Backlog

Running list of things flagged during work sessions, not yet done. Newest first.

---

- [x] **"Detach face from shared texture cell" operator — done 2026-07-05.** Real models
  routinely reuse the exact same `.TLB` atlas rectangle across more than one face (the
  original artist's own space-saving choice — confirmed on a Panzer IV test model). Since
  painting acts on the shared atlas image, painting one face necessarily repaints every
  other face pointing at that same cell too. Confirmed this isn't a plugin bug: it's the
  same thing as "overlapping UVs" in modern DCC tools (Blender, Substance Painter, etc.
  all have this exact gotcha), and the real PE/ObjEdit tool would behave identically,
  since it's genuinely the same underlying pixels either way.

  Ruled out Blender's built-in Smart UV Project as a fix on its own — it has no awareness
  of which atlas regions are already used by other real `.TLB` entries (this model's own
  other faces, or unrelated vehicles sharing the same atlas), so it could just as easily
  relocate a UV onto someone else's texture.

  Shipped as `MESH_OT_pe_detach_face_texture` (Edit Mode face context menu, "PE: Detach
  Face From Shared Texture Cell") — select the face(s) sharing a cell with something else,
  run it, and each selected face gets its own private copy of the texture: finds free
  atlas space (`find_free_atlas_space()`), clones the current cell's pixels there via
  Blender's Image API, allocates a new `.TLB` entry (`append_tlb_entry()`), repoints the
  face's `textureOfset` (`patch_face_texture_id()`), and shifts that face's own UVs to the
  new cell. Writes directly to the model's `.RRF` and `.TLB`, with a one-time `.bak`
  backup of each made automatically before the first edit.

  Verified end-to-end on a real model (not a synthetic test) via the actual registered
  `bpy.ops` call, using an isolated scratch copy of the asset (never the live files):
  found two real faces on Pz4H's turret sharing one cell, selected only one, ran the
  operator, and confirmed all of - the selected face's texture id changed while the
  unselected sibling's didn't; a new `.TLB` entry appeared with the same size at a
  different, non-overlapping position while the original entry stayed byte-identical;
  the new cell's pixels exactly matched the old cell's pre-edit content while the old
  cell itself was untouched; the selected face's Blender UV shifted to the new cell while
  the sibling's UV didn't move; and both `.bak` backups were created correctly.

  This covered the same ground as "Scenario B" in
  [`docs/PAINT_AND_EXPORT_SCOPING.md`](docs/PAINT_AND_EXPORT_SCOPING.md) (new texture
  regions), scoped narrowly to "clone one face off its current shared cell" rather than
  general new-content painting - PAINT_AND_EXPORT_SCOPING.md still needs updating to
  reflect that this specific case is now built.

- [ ] **Auto-detect only tries the single best-scoring `.TLB`.** Models that genuinely
  draw from several libraries at once resolve far fewer faces without a `.RRI` present —
  confirmed on a Tiger1 with a `.RRI` listing 9 libraries: 94% resolved via the `.RRI`,
  only 21% via auto-detect alone (auto-detect found just 1 of the 9).

- [ ] **Repaint export path untested against the real game/ObjEdit.** Only checked so far
  via an automated pixel-comparison test inside Blender (painted regions match, untouched
  regions match, correct format/size). Nobody has loaded an actual exported `_24.BMP` in
  ObjEdit or the game yet to confirm it's accepted and displays correctly.

- [ ] **Some texture placement issues still being tracked down.** Reported after the
  geometry/pivot fixes landed — "model is now accurate" but "still some odd texture
  issues." Not yet reproduced with a specific screenshot/model to diagnose.
