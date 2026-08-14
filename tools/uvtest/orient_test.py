"""Draw an orientation test into every entry of a .TLB atlas.

A checkerboard tells you a mapping is wrong but not HOW. An "F" tells you immediately:

    F      correct
    Ꟊ      mirrored horizontally
    ᖴ      mirrored vertically
    rotated F   rotated, and by how much

Each entry gets:
  - a dark background with a 1px border, so the entry's extent is visible;
  - a large asymmetric F filling most of it;
  - a RED square in the TOP-LEFT corner - the crop origin. If the red block is anywhere
    else, the rectangle is flipped or rotated, no squinting at the glyph required;
  - a GREEN bar along the TOP edge, so a 180 degree rotation is obvious even if the F is
    hard to read at small sizes.

Nothing above 220 per channel: ObjEdit keys light pixels as transparent.
"""
import os
import struct
import sys

sys.path.insert(0, r"C:/Users/Jeff/AppData/Local/Temp/claude/M--T34vsTiger---REDUX0-001-Scripts/4ca4b93c-80f4-4e8c-953e-69e2095e4ed4/scratchpad/pe-repo-check/tools/headless_oracle/bpystub")
import importlib.util
_s = importlib.util.spec_from_file_location(
    "io_import_rrf", r"L:/2025/PE/PE SOURCE/BlenderRRFPlugin/io_import_rrf.py")
rrf = importlib.util.module_from_spec(_s)
_s.loader.exec_module(rrf)

TLB = sys.argv[1]
BMP = sys.argv[2]
W, H = 256, 4096

# PALETTE INDEX 0 IS THE TRANSPARENT KEY - never use it for visible pixels.
# Confirmed against stock content: every shipped atlas has index 0 = (255,255,255) and
# fills 10-84% of the sheet with it, the empty space between packed entries. It is the
# INDEX that is keyed, not the colour: filling a background with index 0 made the model
# see-through in ObjEdit regardless of what colour index 0 held.
TRANSPARENT = 0
BG, FG, RED, GREEN, BORDER = 1, 2, 3, 4, 5
pal = [(45, 48, 50)] * 256
pal[TRANSPARENT] = (255, 255, 255)   # match stock convention
pal[BG] = (45, 48, 50)
pal[FG] = (200, 200, 190)      # the F - light but under the key threshold
pal[RED] = (200, 40, 40)       # top-left origin marker
pal[GREEN] = (60, 180, 60)     # top edge
pal[BORDER] = (20, 20, 20)
for i in range(6, 256):        # a usable ramp for anything painted later
    v = int((i - 6) * 220 / 249.0)
    pal[i] = (v, v, v)

px = bytearray([TRANSPARENT]) * (W * H)   # empty atlas space, as stock does


def put(x, y, c):
    if 0 <= x < W and 0 <= y < H:
        px[y * W + x] = c


def fill(x0, y0, x1, y1, c):
    for y in range(max(0, y0), min(H, y1)):
        for x in range(max(0, x0), min(W, x1)):
            px[y * W + x] = c


lib = rrf.read_tlb(TLB)
for eid, (ptx, pty, sx, sy) in sorted(lib.items()):
    ox, oy = ptx * 16, pty * 16
    if sx < 8 or sy < 8:
        continue
    fill(ox, oy, ox + sx, oy + sy, BG)
    # border
    for x in range(ox, ox + sx):
        put(x, oy, BORDER); put(x, oy + sy - 1, BORDER)
    for y in range(oy, oy + sy):
        put(ox, y, BORDER); put(ox + sx - 1, y, BORDER)
    # green bar along the TOP edge
    fill(ox + 1, oy + 1, ox + sx - 1, oy + 1 + max(2, sy // 16), GREEN)
    # red square in the TOP-LEFT corner = the crop origin
    m = max(3, min(sx, sy) // 6)
    fill(ox + 1, oy + 1, ox + 1 + m, oy + 1 + m, RED)
    # a big F: vertical stem, top arm, middle arm
    pad_x = max(2, sx // 6)
    pad_y = max(2, sy // 5)
    fx0, fy0 = ox + pad_x, oy + pad_y
    fx1, fy1 = ox + sx - pad_x, oy + sy - max(2, sy // 8)
    stem = max(2, (fx1 - fx0) // 5)
    arm = max(2, (fy1 - fy0) // 6)
    fill(fx0, fy0, fx0 + stem, fy1, FG)                       # stem
    fill(fx0, fy0, fx1, fy0 + arm, FG)                        # top arm (full width)
    my = fy0 + (fy1 - fy0) // 2
    fill(fx0, my, fx0 + int((fx1 - fx0) * 0.66), my + arm, FG)  # middle arm (shorter)

row_pad = (4 - (W % 4)) % 4
pixel_bytes = (W + row_pad) * H
offset = 14 + 40 + 1024
bmp = bytearray()
bmp += b"BM" + struct.pack("<IHHI", offset + pixel_bytes, 0, 0, offset)
bmp += struct.pack("<IiiHHIIiiII", 40, W, H, 1, 8, 0, pixel_bytes, 2835, 2835, 256, 256)
for r, g, b in pal:
    bmp += bytes((b, g, r, 0))
for y in range(H - 1, -1, -1):
    bmp += px[y * W:(y + 1) * W] + bytes(row_pad)
open(BMP, "wb").write(bytes(bmp))

lib_raw = bytearray(open(TLB, "rb").read())
for i, (r, g, b) in enumerate(pal):
    struct.pack_into("<4B", lib_raw, 8 + i * 4, r, g, b, 0)
open(TLB, "wb").write(bytes(lib_raw))

print("orientation test drawn into %d entries of %s" % (len(lib), os.path.basename(TLB)))
print("read it as: F upright + RED block top-left + GREEN bar along the top = correct")
