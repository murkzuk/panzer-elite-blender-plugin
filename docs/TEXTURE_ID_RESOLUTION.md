# How a face's texture reference resolves to actual pixels

## The `textureOfset` field

On a textured face (see [RRF_FORMAT.md](RRF_FORMAT.md)), `textureOfset`'s top bit
(`0x80000000`) being set means the remaining 31 bits are an **index**, not a byte offset
into the file's own embedded texture block (that embedded block is a constant, unused
256-byte placeholder in every real asset checked — see below).

### The index formula

Every real `.TLB` library holds at most 4096 entries (`TLB_MAX_PARTS`), indexed 0-4095.
A face's index resolves as:

```
part_id = texture_id % 4096
```

That's it — no slot number, no session state, no guessing which of up to 32 possible
library slots a face's paint session happened to use. Because subtracting any multiple
of 4096 from an integer never changes its remainder mod 4096, and every real entry lives
in `[0, 4095]` by construction, the remainder **always** identifies the correct entry in
whichever `.TLB` the face actually references — no matter how large `texture_id` itself
is, or how many times the tool's internal "slot" counter had wrapped around when the face
was painted.

**This corrects an earlier version of this doc and an earlier version of the importer**,
which capped the search at slots 0-31 (`texture_id - slot*4096` for `slot` in `0..31`)
and treated anything larger as an unrecoverable runtime value. That cap was simply wrong
— real assets exist with an *implied* slot far beyond 31. Confirmed on a Tiger1 model:
face IDs around `1181712` for the turret's "kill rings" band imply slot 288, yet resolve
cleanly to entry 2591 (`1181712 % 4096 == 2591`) in `CustomB1.TLB` — matching the exact
library ObjEdit itself reported when opening a fresh (non-live) session on the same file.
Re-running the corrected formula against real content immediately took several
previously-magenta faces (an entire Tiger1 turret, 119/119 faces) to fully resolved.

### Practical resolution strategies, in order of preference

1. **Read a companion `.RRI` file** (see [RRI_FORMAT.md](RRI_FORMAT.md)) if one exists —
   it records the exact set of libraries the model actually uses, no guessing.
2. **Direct lookup against a specific candidate `.TLB`** using the modulo formula above.
3. **Best-match auto-detect**: with no other information, score every `.TLB` in a folder
   by how many of a model's unique texture IDs resolve against it. Unrelated libraries
   share a handful of common low IDs (generic materials like flat black/green shared
   across every vehicle), so real matches need to score well above that noise floor to be
   trusted — in practice, well over half a model's unique IDs vs. single digits for an
   unrelated library.

## Known remaining limitation: auto-detect only tries one library

Auto-detect (strategy 3 above) picks the single best-scoring `.TLB` in the search folder.
That's fine for models that only ever draw from one library, but some models genuinely
spread their faces across several libraries at once. A Tiger1 model with a `.RRI` listing
9 separate libraries resolved 94% of its faces when all 9 were used (via the `.RRI`), but
only 21% when auto-detect was left to guess a single best library — not because the
extra faces are unrecoverable, but because their real library was never even tried.

**Takeaway: prefer a `.RRI` file whenever one is present, especially for larger/older
vehicle models.** Auto-detect is a reasonable fallback for simpler content (props,
scenery, single-library vehicles), where it reliably reaches 100%.

## Genuinely unrecoverable faces: much rarer than previously believed

After the modulo fix, real test content resolves in the 88-100% range per model, with
only an occasional single stray texture ID (not a broad category) failing outright —
e.g. one distinct ID out of an entire Tiger1 model's several hundred unique IDs. That
residual is far more likely to be a stale/removed `.TLB` entry (the id was valid when the
face was painted, but the library was later re-saved without that entry) than a genuine
runtime-only hardware handle. There's no evidence left for the older theory that a
meaningful fraction of content is permanently unrecoverable by design — that conclusion
was an artifact of the slot-cap bug, not a real property of the saved data.

Practical takeaway: import will texture whatever resolves and flag the rest for manual
re-texturing (see [PLUGIN_USAGE.md](PLUGIN_USAGE.md)) — for a `.RRI`-backed import this
should now be a small handful of faces at most, not a systematic gap.
