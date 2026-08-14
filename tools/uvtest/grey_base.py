"""Fill a .TLB/_8.BMP pair with a flat panzer-grey base and a palette worth painting into.

The labelled test grid was the right tool for finding mapping bugs and the wrong one for
judging paint - too busy to tell a broken line from a noisy background. This replaces it
with plain Dunkelgrau so a stroke is unmistakable.

Palette design:
  - a 6x6x6 colour cube plus a grey ramp, so quantize_to_palette() has real choices when
    painted colours are matched back;
  - nothing above 220 per channel. ObjEdit keys light pixels as transparent, and a pale
    entry in the palette punches holes through the model.
"""
import os
import struct
import sys

TLB = sys.argv[1]
BMP = sys.argv[2]
W, H = 256, 4096
PANZER_GREY = (58, 62, 64)          # RAL 7021 Dunkelgrau, near enough
MAXC = 220                          # never light enough to be keyed transparent

levels = [0, 44, 88, 132, 176, MAXC]
pal = []
for r in levels:
    for g in levels:
        for b in levels:
            pal.append((r, g, b))               # 216 entries
while len(pal) < 256:                            # top up with a fine grey ramp
    i = len(pal) - 216
    v = int(i * MAXC / 39.0)
    pal.append((v, v, v))
pal = pal[:256]

# put the base colour in a known slot so the fill is exact
BASE_IDX = 255
pal[BASE_IDX] = PANZER_GREY

px = bytes([BASE_IDX]) * (W * H)

row_pad = (4 - (W % 4)) % 4
pixel_bytes = (W + row_pad) * H
offset = 14 + 40 + 1024
bmp = bytearray()
bmp += b"BM" + struct.pack("<IHHI", offset + pixel_bytes, 0, 0, offset)
bmp += struct.pack("<IiiHHIIiiII", 40, W, H, 1, 8, 0, pixel_bytes, 2835, 2835, 256, 256)
for r, g, b in pal:
    bmp += bytes((b, g, r, 0))                   # BMP palette is BGR0
for y in range(H - 1, -1, -1):
    bmp += px[y * W:(y + 1) * W] + bytes(row_pad)
open(BMP, "wb").write(bytes(bmp))

lib = bytearray(open(TLB, "rb").read())
for i, (r, g, b) in enumerate(pal):
    struct.pack_into("<4B", lib, 8 + i * 4, r, g, b, 0)   # .TLB palette is RGB0
open(TLB, "wb").write(bytes(lib))

print("panzer grey %s written into %s / %s" % (PANZER_GREY, os.path.basename(TLB),
                                               os.path.basename(BMP)))
print("palette: 216-colour cube + grey ramp, nothing above %d per channel" % MAXC)
