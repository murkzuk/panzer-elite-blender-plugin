# TODO / Backlog

Running list of things flagged during work sessions, not yet done. Newest first.

---

- [ ] **"Detach face from shared texture cell" operator — 2 of 4 building blocks done.**
  Real models routinely reuse the exact same `.TLB` atlas rectangle across more than one
  face (the original artist's own space-saving choice — confirmed on a Panzer IV test
  model). Since painting acts on the shared atlas image, painting one face necessarily
  repaints every other face pointing at that same cell too. Confirmed this isn't a plugin
  bug: it's the same thing as "overlapping UVs" in modern DCC tools (Blender, Substance
  Painter, etc. all have this exact gotcha), and the real PE/ObjEdit tool would behave
  identically, since it's genuinely the same underlying pixels either way.

  Ruled out Blender's built-in Smart UV Project as a fix on its own — it has no awareness
  of which atlas regions are already used by other real `.TLB` entries (this model's own
  other faces, or unrelated vehicles sharing the same atlas), so it could just as easily
  relocate a UV onto someone else's texture.

  A real fix needs:
  1. Find genuinely free space in the atlas's tile-packing grid. **Not started.**
  2. Copy the current cell's pixels there as a starting point (so nothing changes
     visually until repainted). **Not started** (needs #1 first).
  3. Allocate a new `.TLB` entry (id/pos/size) for it. **Done** — `append_tlb_entry()`,
     verified byte-exact against all 98 real `.TLB` files.
  4. Rewrite that face's `textureOfset` to point at the new region (UV corner bytes stay
     valid unchanged, since they're offsets *within* the entry, not absolute atlas
     coordinates). **Done** — `patch_face_texture_id()`, verified against every real model
     in the asset set (byte-exact except the one intentionally-changed field; re-parses
     identically through the normal importer otherwise).

  This is the same underlying work as "Scenario B" in
  [`docs/PAINT_AND_EXPORT_SCOPING.md`](docs/PAINT_AND_EXPORT_SCOPING.md) (new texture
  regions), but scoped narrowly to "clone one face off its current shared cell" rather
  than general new-content painting. Remaining work is the atlas free-space search (#1/#2)
  plus wiring #3/#4 together into an actual Blender operator - none of this is exposed in
  the UI yet, it's all callable-from-Python building blocks so far.

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
