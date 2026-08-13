# Headless oracle: ask the real engine instead of guessing

This calls the shipped `rrobjx5.dll` directly, with no GUI, so the engine answers
questions about its own file format numerically. It replaced "load it in ObjEdit and
squint" as this project's ground-truth method.

**It works.** `rrInitRender()` initialises headless and loads `OBJHALX5.DLL` on its own;
`rrLoadGameMesh()` populates `actTank[0]`; `rrGetAllObjects()` returns real part names
and face counts; `rrGetMaterialSelection()` returns per-face texture fields.

## Requirements

- **A 32-bit Python.** Every DLL here is 32-bit x86 (PE machine `0x14c`) and the system
  Python is 64-bit; ctypes cannot cross that. The embeddable build is enough (stdlib
  `ctypes` only) - installed at `K:\Python32`.
- Run with the working directory set to the ObjEdit folder holding the DLLs, so the
  siblings resolve as they do for the real tool.

## Scripts

| script | what it does |
|---|---|
| `dump_exports.py` | Parses a DLL's PE export table. Exports carry a leading `_` and `@N` stdcall decoration, which is why naive name searches miss them. |
| `step2_init.py` | Brings the engine up: `rrInitRender` -> `rrSetRenderSize` -> `rrLoadGameMesh`, printing before/after each call so a crash localises. |
| `step3_readback.py` | Proves the mesh really loaded, by reading part names and face counts back via `rrGetAllObjects`. |
| `step4_oracle.py` | Per-face `rrGetMaterialSelection`, diffed against the file bytes. |

`bpystub/` lets `io_import_rrf.py` be imported **outside Blender**, so the pure
binary-format functions can be regression-tested from a plain Python prompt.

## Everything here only reads

No script writes to a model. Always point them at a copy anyway.

## Gotcha that cost real time

The face record is **inline** in the part entry, at
`HEADER_SIZE + part*PART_SIZE + 224 + lod*MESH_SIZE` - it is not a pointer to be
dereferenced. Hand-rolling that offset produced identical bytes for every part and a
convincing-looking wall of false mismatches. Use `io_import_rrf._mesh_record_offset()`.
