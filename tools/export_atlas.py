"""Export a painted atlas out of a .blend as the 8-bit paletted BMP the game reads.

Blender's own Image>Save cannot produce this - it writes the datablock's own format
regardless of extension, and 24-bit at best. This quantises to the .TLB's palette and
writes a real 8-bit BMP, the same as the add-on's atlas exporter.
"""
import bpy, sys, os
argv = sys.argv[sys.argv.index("--")+1:]
BLEND, TLB, OUT = argv[0], argv[1], argv[2]
bpy.ops.wm.open_mainfile(filepath=BLEND)
bpy.ops.preferences.addon_enable(module='io_import_rrf')
import io_import_rrf as rrf
import numpy as np

img = None
for i in bpy.data.images:
    print("R: candidate %-34s size=%dx%d has_data=%s pixels=%d"
          % (i.name, i.size[0], i.size[1], i.has_data,
             len(i.pixels) if i.has_data else 0))
    if i.name == "Render Result":
        continue
    if i.size[0] == 256 and i.size[1] == 4096:
        img = i
        break
if img is None:
    print("R: no 256x4096 atlas image found"); raise SystemExit(1)
print("R: using image %s  packed=%s" % (img.name, bool(img.packed_file)))

w, h = img.size
px = np.array(img.pixels[:], dtype=np.float32).reshape(h, w, 4)   # bottom-up, RGBA 0..1
rgb = np.clip(px[:, :, :3] * 255.0 + 0.5, 0, 255).astype(np.uint8)

lib = rrf.read_tlb_library(TLB)
palette = rrf.tlb_palette_to_rgb(lib.palette)
indices = rrf.quantize_to_palette(rgb, palette)
rrf.write_bmp8(OUT, indices, palette)
print("R: wrote %s  %d bytes" % (OUT, os.path.getsize(OUT)))

import collections
c = collections.Counter(indices.flatten().tolist())
print("R: distinct palette indices used: %d   top: %s" % (len(c), c.most_common(4)))
