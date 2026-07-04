# How a face's texture reference resolves to actual pixels

This was the hardest part of the format to pin down, and it has a real, permanent
limitation baked into some existing content — worth understanding before relying on
texture import for a specific model.

## The `textureOfset` field

On a textured face (see [RRF_FORMAT.md](RRF_FORMAT.md)), `textureOfset`'s top bit
(`0x80000000`) being set means the remaining 31 bits are an **index**, not a byte offset
into the file's own embedded texture block (that embedded block is a constant, unused
256-byte placeholder in every real asset checked — see below).

### The index formula

The tool can have up to 32 texture libraries loaded simultaneously (numbered slot
buttons in the editor UI). A face's index resolves as:

```
part_id = texture_id - (library_slot * 4096)
```

where `library_slot` (0-31) is whichever slot that specific `.TLB` happened to be loaded
into during the session that painted the face — **not a fixed property of the `.TLB`
file itself**. The same library could be loaded into slot 0 in one session and slot 5 in
another.

Confirmed exactly via a live paint-and-save test in the real tool: painting a face from
a library titled "8202" wrote `textureOfset` low bits = 8202. A `.TLB` entry with
`id=10` (matching the tool's own displayed size for that texture) resolves exactly when
tested at slot 2 (`8202 - 2×4096 = 10`).

### Practical resolution strategies, in order of preference

1. **Read a companion `.RRI` file** (see [RRI_FORMAT.md](RRI_FORMAT.md)) if one exists —
   it records the *exact* slot→library mapping used, no guessing.
2. **Brute-force search**: for a specific candidate `.TLB`, try every slot 0-31 against
   each face's `texture_id` and see which slot (if any) lands on a real entry.
3. **Best-match auto-detect**: with no other information, score every `.TLB` in a folder
   by how many of a model's unique texture IDs resolve against it (trying all 32 slots
   for each). Unrelated libraries share a handful of common low IDs (generic materials
   like flat black/green shared across every vehicle), so real matches need to score
   well above that noise floor to be trusted — in practice, tens of matches vs. single
   digits for an unrelated library.

## The permanent limitation: some faces are genuinely unrecoverable

Even with the correct `.TLB` (or the exact one named in a `.RRI`), **older or
heavily-edited models only resolve a fraction of their faces** — commonly somewhere
between 10% and 30% in real assets checked, occasionally much higher on models with a
current `.RRI`.

The reason: tracing the renderer's own texture-upload code path shows that in some
build(s) of the tool, `textureOfset` was populated from a live hardware texture handle
returned by the graphics API at paint time (`halSendTextureBMP`), not a stable ID.
That's fundamentally a runtime value — it has no relationship to any `.TLB`'s own
per-entry ID, and there is no way to reconstruct it from the saved files after the fact.

**How to tell**: a face's `texture_id` that doesn't resolve against *any* slot of the
correct library (confirmed via a `.RRI` or a high-confidence auto-detect score) almost
certainly falls into this category, rather than needing yet another library tried.

Practical takeaway: import will texture whatever is recoverable and flag the rest for
manual re-texturing (see [PLUGIN_USAGE.md](PLUGIN_USAGE.md)) — this is a real limitation
of the saved data itself, not a gap in the import logic.
