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

## Theatre picker — narrowing auto-detect the way ObjEdit actually does it

The real ObjEdit doesn't score anything when it opens a model — it just asks the user a
direct question via a "Select Theatre" dialog (Desert / Italy / Normandy / Custom A /
Custom B / Custom C / None) and filters its own search to that theatre's libraries. This
plugin's own auto-detect, by contrast, was scoring *every* `.TLB` in the search folder
against each other regardless of theatre — real content confirms this genuinely causes
cross-theatre false competitors (e.g. `PantherG.RRF`'s correct answer, `CustomA1.TLB`,
previously had `CustomC1.TLB` — an unrelated theatre — sitting right behind it at the
same 100% score, purely coincidental generic-material overlap).

`IMPORT_OT_rrf.theatre` (v0.11.0) adds the same question as an import option — `AUTO` (no
filter, the original behavior) or one of the six theatres — and `find_matching_tlbs()`'s
new `name_prefix` parameter filters candidates to just that prefix (`Desert*`, `Italy*`,
`Normandy*`, `CustomA*`, `CustomB*`, `CustomC*`) before scoring. **The model's own folder
location is not a reliable default for this** — `PantherG.RRF` sits in `Normandy_Obj` but
its real answer is Custom A — so the picker doesn't try to guess a starting value, it asks
plainly, the same way ObjEdit does.

Tested against the same two real historical problem cases from the section above, not
synthetically:

- **PantherG.RRF**: `AUTO` already found the correct `CustomA1.TLB` at 100%, but flagged
  `CustomC1.TLB` (also 100%, unrelated theatre) as its closest competitor. Filtering to
  `CUSTOM_A` kept the same correct winner but replaced that irrelevant cross-theatre
  competitor with a real same-theatre one (`CustomA10.TLB`, 93%) — a more honest signal,
  even though the winning pick didn't change here.
- **Psw232.RRF**: `AUTO` guessed `CustomA8.TLB` (30%, wrong — matches the historical
  wrong-guess pattern already documented above). Filtering to `DESERT` found *no* match
  at all, rather than another plausible-looking wrong guess. This isn't the filter
  failing — a real `.RRI` cross-check confirms this model's true answer needs three
  separate libraries together (`Desert1`/`Desert11`/`Desert13`), one of which
  (`Desert13`) is missing from disk entirely, and even the one that *is* present
  resolves 0% of this model's ids on its own. No filter can find a file that isn't
  there — this is the same known limitation as before, not a new one. An honest "nothing
  found" is arguably the better outcome here than a plausible-looking wrong guess,
  though it doesn't fully solve this specific model.

**Bottom line**: the theatre picker measurably reduces cross-theatre false-positive
noise and avoids some wrong guesses outright, but it's not a cure for cases needing
multiple partial libraries or missing files — those remain genuinely hard, same as
documented in the confidence section above.

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


## The 32-library extension (found 2026-08-12 - a major resolution fix)

`rrReNumTLB()` in `rrobjpex.c` shows how the engine really decodes a face's
`textureOfset`:

```c
ActTLB = (textureOfset >> 12) & 0xf;    // library slot, bits 12-15
TexNum =  textureOfset & 0xfff;         // part id within that library, bits 0-11
if (TexNum > 2047) { ActTLB += 16; TexNum -= 2048; }   // slots 16-31
```

That last line is a **32-library extension**: when the 12-bit part number exceeds 2047,
the real slot is `slot + 16` and the real part id is `TexNum - 2048`. Resolving with
`texture_id % 4096` alone returns the un-adjusted number and finds nothing.

This is not a rare corner. **22.8% of textured faces across a real install (82,109 of
359,735) use it**, concentrated in the Tiger and IS-2 families.

`resolve_texture_id()` now tries **both** candidates rather than switching on the >2047
test, because which is correct depends on what is loaded in the slot - models exist whose
ids above 2047 are genuine part numbers, and forcing the subtraction makes those worse.
Trying both can only add a resolution, never remove one.

Measured on real models, resolved against every library in the Texture folder:

| Model | Before | After |
|---|---|---|
| TigerE_1.RRF | 35.0% | **100%** |
| TigerL.RRF | 71.1% | **100%** |
| Is2-0.rrf | 99.2% | **100%** |
| PantherG.RRF | 100% | 100% |
| 88Pak43.RRF | 100% | 100% |

Confirmed through the real import operator: TigerL, TigerE_1 and Is2-0 now come in with
**zero** unresolved faces, where TigerL was previously documented in this file as
resolving inconsistently (19-95%). PantherG's remaining 8 faces (0.2%) are the genuinely
unrecoverable live-HAL-handle case described above, not this bug.

---

## 2026-08-13: libraries resolve by THEATRE SET, not by id-overlap scoring

The user's observation cracked this: **the game renders FMMYDX12 correctly while ObjEdit
renders the same models broken.** The model data is therefore fine - the game gets its
library list from somewhere ObjEdit does not look, and neither did this importer.

A real install's `Texture/` folder is numbered *per theatre*: `Normandy1..6.TLB`,
`Italy1..6.TLB`, `Desert1..8.TLB`. The game loads the theatre's set **in order**, so a
face's library slot is simply an index into it:

```
slot N  ->  <Theatre>(N+1).TLB
```

### Proof

`K:\Panzer Elite\Normandy_Obj\M4a3.RRF` uses slot 1.

| | library | result |
|---|---|---|
| auto-detect (id-overlap) | `Italy5.TLB` - scored **100%** | brown/white garbage |
| theatre rule | `Normandy2.tlb` - scores **98%** (47/48 ids) | **a correct olive-drab Sherman** |

The *lower-scoring* library is the right one. Id-overlap scoring is actively misleading
here: many libraries share ids, so a perfect score is not evidence. Written as an `.RRI`
naming `Normandy2`, the model renders correctly - hull, bogies, sprocket, hatches all
right, with 38 faces (12%) still unresolved, presumably needing a second library.

### Consequences

- **Prefer the theatre rule over scoring** when a model sits in a `*_Obj` theatre folder
  and no real `.RRI` exists. Scoring should be the last resort, not the default.
- Applied blind across all theatre folders the rule resolves 62.6% of faces, but that
  figure is dragged down by **buildings** (`NHaus*` etc.), whose libraries are not in
  `Texture/` at all - they are presumably scenario-local. Vehicles do far better.
- This also explains why ObjEdit looks broken on models that play fine: ObjEdit needs an
  `.RRI`, and almost none exist. **Generating `.RRI` files from the theatre rule would fix
  those models in ObjEdit too**, not just in Blender.

### Implemented (v0.47.0)

- `theatre_prefix_from_path()` reads the theatre from the model's own folder
  (`Normandy_Obj` -> `Normandy`), so the rule applies with no user input.
- `slots_used_by()` recovers the slots a model's faces actually name.
- `theatre_set_libraries()` resolves `slot N -> <Theatre>(N+1).TLB`.
- The importer applies the rule **after** scoring and lets it override, keeping the scored
  matches as fallback for slots the rule cannot fill (buildings reference libraries that
  are not in `Texture/` at all). On the Normandy M4a3 this gives a correct Sherman with
  **0 unresolved faces** - better than a hand-written `.RRI` naming only Normandy2, which
  left 38 unresolved, because the fallback still covers the ids Normandy2 lacks.
- The import report now names the library actually **used**, not merely what scoring
  shortlisted. It previously reported "Italy5.TLB" on a model it had correctly painted
  from Normandy2.tlb.

Gotcha worth keeping: real installs mix case (`Normandy2.tlb`, `Normandy3.TLB`), so the
on-disk lookup must be case-insensitive. Getting that wrong made the rule silently do
nothing while still reporting success.

### tools/write_rri_batch.py

Writes `.RRI` files across a folder from the same rule, so **ObjEdit** stops showing
untextured models that the game renders perfectly. Dry run by default; never overwrites an
existing `.RRI` without `--force` (a shipped `.RRI` is better evidence than any rule);
skips models whose slots have no library on disk unless `--partial`.

Dry run over `K:\Panzer Elite\Normandy_Obj`: **264 of 267 models would get one**, 2 already
had one and were left alone, 1 unresolved.

#### The .RRI must list the WHOLE theatre set

First run of the batch writer named only the slots each model used - and made things
*worse*. An `.RRI` is authoritative, so it disables the importer's scored fallback: the
Normandy M4a3 went from **0 unresolved faces to 38**. All 38 wanted a single id (23) that
`Normandy2` lacks but the rest of the Normandy set has.

The game loads the theatre's libraries as a **set**, so any of them is available to a face
at runtime. The writer now lists all of `<Theatre>1..8` that exist. Re-verified: M4a3 and
Pz4H both back to **0 unresolved, 6/6 libraries found**.

Applied to `K:\Panzer Elite\Normandy_Obj`: **259 written, 1 unresolved**, and the two
genuine 1999-dated `.RRI` files (`MTank.RRI`, `Spw250MG.RRI`) left untouched.

**Case-sensitivity, twice.** Both this tool and `theatre_set_libraries()` first failed by
comparing a capitalised name (`"Normandy2.tlb"`) against lowercased directory entries.
Both times the failure was silent - the code reported success while doing nothing. When
matching filenames on Windows, lowercase BOTH sides.

## Applied across K:\Panzer Elite (2026-08-13)

| folder | written | pre-existing (untouched) | skipped |
|---|---|---|---|
| Normandy_Obj | 259 | 2 | 1 |
| Italy_Obj | 370 | 1 | 0 |
| Desert_Obj | 262 | 1 | 56 |

**Confirmed in ObjEdit by the user**: `Normandy_Obj/Pz3h.RRF`, which had no `.RRI` before,
now opens fully textured. The rule is validated in the real tool, not only in Blender.

## NEXT BUG, with evidence: the +2048 slot hack is wrong

The 56 skipped Desert models are blocked by `decode_texture_offset()`'s "part id above
2047 means slot+16, id-2048" rule. It yields slots that **cannot exist** - 33 models want
slot 17, 17 want slot 16, 6 want slot 23, i.e. `Desert18/17/24`, when only Desert1-6 and 8
are on disk. Decoding the raw fields instead (`slot = bits 12-15`, `id = bits 0-11`) gives
slots that exist AND cover their ids:

| model | hack | raw | raw coverage |
|---|---|---|---|
| `Grant.RRF` | slot 17 (no such library) | slot 1 -> Desert2 | **98%** |
| `ATGun37.RRF` | slots 17, 29 (neither exists) | slot 1 -> Desert2 | **100%** |
| `88Pak43.RRF` | slot 16 (no such library) | slot 0 -> Desert1 | **100%** |

This is consistent with the earlier warning that the hack's "115,613 faces round-tripped
with zero mismatches" evidence only ever proved decode/encode are inverses -
reversibility, not correctness.

**Do not simply delete the hack** - it was added for real 32-library content and removing
it blind risks regressing the models it was meant to fix (TigerE_1, TigerL went 35%/71% ->
100% resolved when it went in). The right fix is conditional: prefer the raw decode when
the hacked slot names a library that does not exist. Re-run
`tools/write_rri_batch.py "K:\Panzer Elite\Desert_Obj" --write` afterwards to pick up the
remaining 56.

## FIXED (v0.48.0): the +2048 slot hack is now conditional

`texture_slot_candidates()` offers **both** readings of a `textureOfset`, best guess
first: the hacked one (`id > 2047` -> slot+16, id-2048) and the raw one
(`slot = bits 12-15`, `id = bits 0-11`). `slots_used_by()` takes an optional
`available_slots` set and picks the first candidate naming a library that actually exists,
falling back to the hacked reading when nothing is known.

This is deliberately evidence-driven rather than a choice between the two rules. Neither
is correct in isolation - the hack is right for genuine 32-library content and wrong for
Desert - and "which library exists on disk" is evidence neither reading can supply itself.

**Regression check (the models the hack exists for):** `TigerE_1.RRF` against a texture
folder that really does have CustomA17/18 - slots unchanged `[0, 13, 16, 17]`, coverage
unchanged at **96.9%**. The conditional only fires when the hacked slot names nothing.

**Result:** the 56 blocked Desert models now write. `ATGun37` and `88Pak43` import with
**0 unresolved**; `Grant` gets 46 of 575 unresolved (its Desert2 coverage is 98%, not
100%). Desert_Obj now has **319** `.RRI` files.

Note the tool had to learn the same lesson as the importer: it computed `used` via
`slots_used_by(parts)` with no availability set, so it kept reporting the phantom slot 16
as missing and skipping models the resolver could already handle. Both sides must share
the availability knowledge.

## v0.49.0: narrow .RRI + a fallback for ids the named library lacks

User report: after the batch write, ObjEdit raised **"Texture ID Too High!"** on Normandy
models (Psw222 and PantherG still rendered; M3 did not).

The check is ObjEdit's own, in `ImageLibUnit.pas`, and it inspects a **loaded library's
entries**, not the `.RRI`:

```pascal
if libList[libCount].libParts[i].id and (MAX_PARTS-1) > MAX_LIB_PARTS - 1 then
   Application.MessageBox('Texture ID Too High!', 'ERROR', MB_OK );
```

i.e. it fires when any loaded library holds an entry with `(id mod 4096) > 2047`.

**Cause: writing the whole theatre set made ObjEdit load libraries the model does not
need.** No stock Normandy library trips the check - but ObjEdit resolves the `.RRI`
against **its own folder**, and the user's OE_2 `Texture/` holds an extended REDUX set
(Normandy1..14) in which `Normandy4.tlb` and `Normandy8.TLB` have **113 high-id entries
each**. Listing the full set pulled those in for no benefit.

### Fix, both halves

1. **`write_rri_batch.py` now writes only the slots a model uses** (`--full-set` opts back
   in). The four models checked each use exactly one slot, so their `.RRI` lists one
   library - nothing spurious for ObjEdit to load.
2. **The importer supplements a narrow `.RRI`** when a *named* slot's library lacks some
   of that slot's ids - the case that made the narrow version regress before (M4a3: one id
   shared by 38 faces). Extra libraries are parked at spare high keys, so the `.RRI` still
   wins wherever it can answer. Note this is distinct from the pre-existing
   "slot the .RRI cannot name" inference; that handles missing *slots*, this handles
   missing *ids* within a present slot.

Verified: M4a3, Psw222, PantherG and M3 all import with **0 unresolved**, each `.RRI`
listing a single library.

Rewritten across the install: Normandy 259, Italy 370, Desert 318. Originals (pre-2020
timestamps) untouched throughout - the genuine ones are dated 1999, which makes them
trivially separable from generated ones.

### Absolute paths: why a relative .RRI can load the WRONG library

After the narrow rewrite the "Texture ID Too High!" error was gone and Psw222 and PantherG
rendered perfectly - but **M3 still failed**. The cause is the `chdir` gotcha, now with
teeth:

**ObjEdit `chdir()`s to its OWN folder before resolving the .RRI**, so `texture\Normandy2.tlb`
loads the library sitting next to *ObjEdit*, not next to the model. The user's OE_2
`Texture/` holds an extended REDUX set under the same filenames, and the two editions
differ in content:

| model | slot -> library | coverage from the game install | coverage from OE_2's copy |
|---|---|---|---|
| **M3** | 1 -> Normandy2 | 95.2% | **71.4%** |
| Psw222 | 0 -> Normandy1 | 100% | 100% |
| PantherG | 0 -> Normandy1 | 95.7% | 100% |

Only M3's slot maps to a library whose OE_2 edition differs materially - which is exactly
why it alone failed while the other two looked perfect. Nothing was wrong with the model,
the rule, or the importer.

`--absolute` writes the full path (`K:\Panzer Elite\Texture\Normandy2.tlb`), which pins the
library regardless of where ObjEdit is launched from. Real shipped .RRI files use both
styles, so this is in-format. Applied across the install: Normandy 259, Italy 370,
Desert 318.

**Recommend `--absolute` whenever ObjEdit lives outside the game folder**, which is the
common case for a modding setup.
