# `.RRI` library-list sidecar format

A later build of ObjEdit writes an optional `.RRI` file next to a `.RRF` (same base
name), recording which texture library was loaded into each of the tool's 16 library
slots when the model was last painted and saved. This is the authoritative answer to
"which `.TLB`(s) does this model use" — no guessing needed, when the file exists.

Not every model has one — it depends on which build/version of ObjEdit last saved it.
Older or long-unedited assets typically don't have a companion `.RRI`.

## Layout

```
offset 0     16 × 128-byte null-padded ASCII strings, one per library slot (0-15)
offset 2048  part-name table, 80 bytes per entry (part names, likely UI/reference only)
...          (large binary section, not yet decoded)
near EOF     material/shading-mode label strings ("No Shading", "No Single Texture", etc.)
```

### Library slot table (bytes 0-2047)

16 fixed-size 128-byte slots. Each holds a null-terminated relative path like
`texture\CustomB1.TLB`, or is entirely blank if that slot wasn't used for this model.

```python
for slot in range(16):
    raw = data[slot*128 : slot*128+128].split(b"\x00", 1)[0]
    text = raw.decode("latin-1").strip()
    # non-empty -> this slot's library
```

Confirmed against a live paint-and-save test in the real tool: painting a face from a
library titled "8202" wrote `textureOfset` (see
[TEXTURE_ID_RESOLUTION.md](TEXTURE_ID_RESOLUTION.md)) with low bits = 8202, and this
model's `.RRI` correctly listed the corresponding library in slot 2
(`8202 - 2×4096 = 10`, a real entry in that library).

### Resolving a listed path to a real file

The paths are relative to the pack's install root (e.g. `<root>\Texture\CustomB1.TLB`),
while the `.RRF`/`.RRI` themselves live at `<root>\<PackFolder>\Model.RRF`. The natural
resolution root is the `.RRF`'s own parent directory; falling back to the `.RRF`'s own
directory covers installs with a different layout.

### Everything past the library table

Not fully decoded — appears to be a part-name reference table (matching the `.RRF`'s own
part names, presumably for an editor UI part list) followed by a large binary section
and a small table of human-readable shading-mode labels near the end of the file. None
of this was needed to solve texture resolution, so it wasn't pursued further.
