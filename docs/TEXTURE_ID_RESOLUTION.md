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

The "32 possible library slots" figure isn't a guess either — independently confirmed via
historical PEDG community discussion as a real, hard runtime limit on how many `.TLB`
libraries the original editor could have loaded simultaneously (an earlier tool version
was limited to 8). That same discussion is what originally motivated packing extra
addressing into `textureOfset`'s otherwise-unused upper bits in the first place — this
repo's modulo-based resolution is a from-scratch reverse-engineered match for that same
mechanism, not a coincidence.

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
   unrelated library. `find_matching_tlbs()` scores every library in the folder this way,
   then greedily keeps adding qualifying libraries (best-scoring first) as long as each
   one still resolves at least one id nothing already added covers - not just the single
   best-scoring library (see below).

## Auto-detect now tries every library that helps, not just the best one

Originally auto-detect only picked the single best-scoring `.TLB` - fine for models that
only ever draw from one library, but a real problem for models that genuinely spread
their faces across several at once. A Tiger1 model with a `.RRI` listing 9 separate
libraries resolved 94% of its faces when all 9 were used (via the `.RRI`), but only 21%
when auto-detect was left to guess a single best library - not because the extra faces
were unrecoverable, but because their real library was never even tried.

Fixed by having auto-detect keep adding libraries (in score order) as long as each one
newly resolves something nothing already-selected covers - confirmed real improvement on
several models with no `.RRI` present: `Pz4H_3.RRF` and `PantherG2.RRF` both went from
already-good (91.0%/99.8%) to fully resolved (100%/100%) once auto-detect picked up a
second/third library it wasn't using before, with zero regression on every model that
only ever needed one library to begin with.

**A `.RRI` file is still the better answer when one exists** - it's the authoritative,
exact list, not a scored guess. Auto-detect on the same Tiger1 model above (no `.RRI`
involved) still only reaches 27% even with multi-library support, well short of the
`.RRI`'s 94% - it correctly picked up a second real library, but 7 of the 9 libraries
that model's `.RRI` lists never scored high enough above the noise floor to be trusted
as a genuine match on their own. Auto-detect remains a best-effort fallback, not a
substitute for a real `.RRI` when one is available.

## Confidence: how much to trust an auto-detect guess

A single session hit three real cases where trusting a plausible-looking auto-detect
result turned out wrong, and it was only caught by real in-game testing afterward:

- **Psw232** (Desert_Obj): auto-detect's scoring guessed `Desert5.TLB`, then
  `CustomB14.TLB` — both wrong. A genuine `.RRI` was needed to reveal the real answer
  (`Desert1`/`Desert11`).
- **PantherG** "II01" (Normandy_Obj): the real answer came from a genuine `.RRI` that
  existed on disk but wasn't found (see the folder-location fix above) — auto-detect
  never got the chance to be wrong or right here, but would have been asked to guess
  had the RRI stayed missed.
- **Pz4E** (Desert_Obj): auto-detect found a clean, unambiguous 100% single-library
  match, consistent across every theatre copy of the RRF — and it was still the wrong
  *vehicle*, because the active mod's `units.csv` pointed the "Pz4E" identifier at a
  different real tank than the file on disk depicts (see
  [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) — this specific failure mode is a
  different problem than texture-library scoring and isn't fixable from this file).

This led to checking whether a score-gap heuristic (top candidate clearly ahead of the
runner-up) could reliably tell a good auto-detect guess from a bad one. It can't:
scanning 9 real playable vehicles (Pz4h, Pz4E, TigerL, PantherG, Psw232, SPW250MG,
M4A1, StuG3G, and others) against both this project's current, reduced Texture folder
and the original, fuller 98-library set showed **every single one** has another library
scoring within 1-2 unique ids of the top pick. Psw232's own auto-detect guess scored a
clean-looking 96% with a real gap behind it — and was still wrong once checked against
its real `.RRI`. This asset library's generic base materials (flat colors, common
metal/rubber tones) overlap too pervasively across the whole set for a score gap to be
a reliable signal — not a case where it's sometimes missing, but one where it
structurally isn't there to find.

Because of this, `find_matching_tlbs()` returns `(matches, confidence, reason)` where
`confidence` is one of:

- **`"rri"`** — resolved via a real `.RRI` file (same-directory or texture-folder
  fallback). The authoritative answer; not a scored guess at all.
- **`"manual"`** — an explicit `tlb_filepath` was supplied by the caller, skipping
  detection entirely.
- **`"low"`** — auto-detect's best guess. **Always** `"low"`, regardless of how clean
  the score looks, per the finding above — `_classify_tlb_confidence()` never returns
  `"high"` for a pure scoring-based guess, because real testing found no score-based
  threshold that reliably separates a good guess from a wrong one in this asset
  library. `reason` still reports the top candidate's resolved percentage and nearest
  runner-up for context, since that's useful information even though it isn't grounds
  to call the guess trustworthy.

`IMPORT_OT_rrf.execute()` escalates the operator report to `{"WARNING"}` whenever an
import went through the `"low"` path, with explicit wording to check a real `.RRI` or
in-game before trusting the result — instead of the same plain informational message
whether the match was rock-solid or a coin flip. It also cross-checks the top
auto-detect candidate against same-named sibling copies in the other theatre
`PackFolder`s (`CustomA`/`CustomB`/`CustomC`/`Desert_Obj`/`Italy_Obj`/`Normandy_Obj`) via
`cross_check_tlb_across_variants()`, reporting how consistently it resolves across all
of them — extra context alongside the confidence label, not a separate trust signal
(confirmed on Pz4E: cross-check came back a consistent 100%/100% across variants, while
the real reason for low confidence was five *other* libraries scoring 98% right behind
the top pick within that one folder — a close-runner-up problem, not a cross-copy
inconsistency problem, and the two don't imply each other).

The resolution method and confidence are also stamped onto the imported data itself
(`pe_tlb_confidence` custom property on the atlas Image, alongside the existing
`pe_tlb_filepath`), so "how sure are we about this texture" stays inspectable later in
Blender's own UI, not just something that scrolled by in the operator report at import
time.

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
