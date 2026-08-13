"""Build a UV TEST PATTERN skin for a real PE model, end to end.

Rather than re-unwrapping, this keeps the model's existing library ENTRY TABLE and
replaces only the atlas artwork with a labelled grid. Every face then shows exactly which
part of the atlas it samples, so the mapping can be read straight off the render - in
Blender and in ObjEdit alike.

Produces, in this folder:
   Psw222.RRF   copy of the model (untouched geometry)
   Psw222.RRI   points at the test library
   UvTest.tlb   copy of Normandy1's entry table
   UvTest_8.bmp 256x4096 8-bit labelled grid
"""
import importlib.util
import os
import struct
import sys

HERE = os.path.join("K:" + os.sep, "uvtest")   # short: write_rri truncates at 127 bytes
os.makedirs(HERE, exist_ok=True)
sys.path.insert(0, r"C:/Users/Jeff/AppData/Local/Temp/claude/M--T34vsTiger---REDUX0-001-Scripts/4ca4b93c-80f4-4e8c-953e-69e2095e4ed4/scratchpad/pe-repo-check/tools/headless_oracle/bpystub")
_s = importlib.util.spec_from_file_location(
    "io_import_rrf", r"L:/2025/PE/PE SOURCE/BlenderRRFPlugin/io_import_rrf.py")
rrf = importlib.util.module_from_spec(_s)
_s.loader.exec_module(rrf)

GAME = os.path.join("K:" + os.sep, "Panzer Elite")
GTEX = os.path.join(GAME, "Texture")
SRC_MODEL = os.path.join(GAME, "Normandy_Obj", "Psw222.RRF")
SRC_LIB = None
for n in os.listdir(GTEX):
    if n.lower() == "normandy1.tlb":
        SRC_LIB = os.path.join(GTEX, n)

W, H = 256, 4096
CELL = 32

# ---- palette: 16 distinct hues + white/black, rest greys -----------------------------
pal = []
hues = [(220, 60, 60), (240, 140, 40), (240, 220, 60), (150, 220, 60),
        (60, 200, 90), (60, 210, 200), (60, 150, 240), (90, 90, 230),
        (160, 80, 230), (230, 80, 200), (230, 120, 150), (150, 100, 60),
        (190, 190, 190), (120, 120, 120), (15, 15, 15), (70, 70, 70)]
# NOTE: nothing here may be near-white. The importer's colour key treats near-white as
# transparent (COLORKEY_DISTANCE_THRESHOLD, linear space), so labels drawn in 250,250,250
# were being keyed OUT - they rendered as holes rather than text. 14 = near-black label,
# 15 = dark grey border.
for i in range(256):
    if i < len(hues):
        pal.append(hues[i])
    else:
        g = (i * 7) % 200          # capped: never near-white (see colour-key note)
        pal.append((g, g, g))

# ---- 5x7 digit font so each cell can be labelled -------------------------------------
FONT = {
    "0": ["111", "101", "101", "101", "111"], "1": ["010", "110", "010", "010", "111"],
    "2": ["111", "001", "111", "100", "111"], "3": ["111", "001", "111", "001", "111"],
    "4": ["101", "101", "111", "001", "001"], "5": ["111", "100", "111", "001", "111"],
    "6": ["111", "100", "111", "101", "111"], "7": ["111", "001", "010", "010", "010"],
    "8": ["111", "101", "111", "101", "111"], "9": ["111", "101", "111", "001", "111"],
    ",": ["000", "000", "000", "010", "100"],
}

px = bytearray(W * H)          # index 0 default
cols, rows = W // CELL, H // CELL
for cy in range(rows):
    for cx in range(cols):
        base = (cx + cy) % 2
        colour = (cx % 8) if base == 0 else 12 + (cy % 4)
        for y in range(CELL):
            for x in range(CELL):
                px[(cy * CELL + y) * W + cx * CELL + x] = colour
        # 1px black border so cell edges are unmistakable
        for x in range(CELL):
            px[(cy * CELL) * W + cx * CELL + x] = 15
            px[(cy * CELL + CELL - 1) * W + cx * CELL + x] = 15
        for y in range(CELL):
            px[(cy * CELL + y) * W + cx * CELL] = 15
            px[(cy * CELL + y) * W + cx * CELL + CELL - 1] = 15
        # label "col,row"
        label = "%d,%d" % (cx, cy)
        ox, oy = cx * CELL + 3, cy * CELL + 12
        for ch in label:
            g = FONT.get(ch)
            if g:
                for gy, line in enumerate(g):
                    for gx, bit in enumerate(line):
                        if bit == "1":
                            X, Y = ox + gx, oy + gy
                            if cx * CELL <= X < cx * CELL + CELL and cy * CELL <= Y < cy * CELL + CELL:
                                px[Y * W + X] = 14
            ox += 4

# ---- write an 8-bit BMP (bottom-up rows, 1024-byte palette) --------------------------
row_pad = (4 - (W % 4)) % 4
pixel_bytes = (W + row_pad) * H
offset = 14 + 40 + 1024
bmp = bytearray()
bmp += b"BM" + struct.pack("<IHHI", offset + pixel_bytes, 0, 0, offset)
bmp += struct.pack("<IiiHHIIiiII", 40, W, H, 1, 8, 0, pixel_bytes, 2835, 2835, 256, 256)
for r, g, b in pal:
    bmp += bytes((b, g, r, 0))          # BMP palette is BGR0
for y in range(H - 1, -1, -1):          # bottom-up
    bmp += px[y * W:(y + 1) * W] + bytes(row_pad)

open(os.path.join(HERE, "UvTest_8.bmp"), "wb").write(bytes(bmp))

# ---- library: same entry table, our palette -----------------------------------------
lib = bytearray(open(SRC_LIB, "rb").read())
for i, (r, g, b) in enumerate(pal):
    struct.pack_into("<4B", lib, 8 + i * 4, r, g, b, 0)   # .TLB palette is RGB0
open(os.path.join(HERE, "UvTest.tlb"), "wb").write(bytes(lib))

# ---- model copy + .RRI ---------------------------------------------------------------
open(os.path.join(HERE, "Psw222.RRF"), "wb").write(open(SRC_MODEL, "rb").read())
rrf.write_rri(os.path.join(HERE, "Psw222.RRI"),
              {0: os.path.join(HERE, "UvTest.tlb")})

print("wrote:")
for f in sorted(os.listdir(HERE)):
    print("   %-16s %d bytes" % (f, os.path.getsize(os.path.join(HERE, f))))
print("\ngrid: %dx%d cells of %dpx, each labelled 'col,row'" % (cols, rows, CELL))
