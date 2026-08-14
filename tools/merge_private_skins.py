"""Merge per-part private skins into ONE library, so a model is loadable.

Why this is needed
------------------
`pe_give_private_skin` gives each part its own brand-new .TLB, and every one of them
restarts its entry ids at 0 while every part's faces still name library slot 0. So a
five-part vehicle ends up with five libraries all claiming the same addresses - the hull's
"id 3", the turret's "id 3" and the wheel's "id 3" are different pictures at the same
address. Whichever library loads wins and the rest get its artwork. The per-part flow
cannot produce a loadable model on its own.

This merges them: every entry is repacked into a single 256x4096 atlas, given a unique id,
and every face is rewritten to point at it. One library, one slot, one .RRI - which is how
real PE content is shipped.

The palettes match already (each private skin borrows the same one via
find_theatre_palette), so no requantising is needed - this is purely relocating rectangles
and renumbering. The palettes are checked anyway and the merge refuses if they differ,
because silently merging mismatched palettes would recolour half the model.

Usage:
    python merge_private_skins.py <model.RRF> [-o OUTPUT.RRF]
"""
import argparse
import glob
import importlib.util
import os
import struct
import sys

PLUGIN = r"L:/2025/PE/PE SOURCE/BlenderRRFPlugin/io_import_rrf.py"


def load_plugin():
    here = os.path.dirname(os.path.abspath(__file__))
    stub = os.path.join(here, "headless_oracle", "bpystub")
    if os.path.isdir(stub):
        sys.path.insert(0, stub)
    spec = importlib.util.spec_from_file_location("io_import_rrf", PLUGIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read_bmp8(path):
    """Returns (width, height, rows_top_down) where each row is a bytes of palette indices."""
    d = open(path, "rb").read()
    if d[:2] != b"BM":
        raise ValueError("%s is not a BMP (magic %r) - was it saved from Blender's "
                         "Image>Save instead of the plugin's atlas exporter?"
                         % (os.path.basename(path), d[:4]))
    off = struct.unpack_from("<I", d, 10)[0]
    w, h = struct.unpack_from("<ii", d, 18)
    bits = struct.unpack_from("<H", d, 28)[0]
    if bits != 8:
        raise ValueError("%s is %d-bit, need 8-bit paletted" % (os.path.basename(path), bits))
    stride = w + ((4 - (w % 4)) % 4)
    px = d[off:]
    rows = [px[y * stride:y * stride + w] for y in range(h)]
    rows.reverse()                      # BMP stores bottom-up
    return w, h, rows


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("model", help="the .RRF whose parts were given private skins")
    ap.add_argument("-o", "--output", default=None, help="output .RRF (default: <model>_merged.RRF)")
    args = ap.parse_args()

    rrf = load_plugin()
    model = os.path.abspath(args.model)
    folder = os.path.dirname(model)
    stem = os.path.splitext(os.path.basename(model))[0]
    out_rrf = args.output or os.path.join(folder, stem + "_merged.RRF")
    out_tlb = os.path.join(folder, stem + "_merged.TLB")
    out_bmp = os.path.join(folder, stem + "_merged_8.BMP")
    out_rri = os.path.splitext(out_rrf)[0] + ".RRI"

    # ---- which private library belongs to which part ---------------------------------
    data = bytearray(rrf.read_rrf_raw(model))
    obj_count = struct.unpack_from("<I", data, 4)[0]
    part_names = {}
    for p in range(obj_count):
        raw = bytes(data[rrf.HEADER_SIZE + p * rrf.PART_SIZE:][:24])
        part_names[p] = raw.split(b"\x00")[0].decode("mbcs", "replace")

    libs = {}
    for path in sorted(glob.glob(os.path.join(folder, "%s_*_private.TLB" % stem))):
        part = os.path.basename(path)[len(stem) + 1:-len("_private.TLB")]
        libs[part] = path
    if not libs:
        print("no %s_*_private.TLB found in %s" % (stem, folder))
        return 1
    print("found %d private librar%s" % (len(libs), "y" if len(libs) == 1 else "ies"))

    # ---- merged atlas ----------------------------------------------------------------
    merged = None
    canvas = [bytearray(256) for _ in range(4096)]
    remap = {}            # part_name -> {old_id: new_id}
    total_entries = 0

    for part, tlb_path in libs.items():
        lib = rrf.read_tlb_library(tlb_path)
        bmp_path = os.path.splitext(tlb_path)[0] + "_8.BMP"
        if not os.path.exists(bmp_path):
            for cand in glob.glob(os.path.splitext(tlb_path)[0] + "_8.*"):
                bmp_path = cand
                break
        w, h, rows = read_bmp8(bmp_path)

        if merged is None:
            merged = rrf.new_tlb_library(palette=lib.palette)
            base_palette = bytes(lib.palette)
        elif bytes(lib.palette) != base_palette:
            print("REFUSING: %s has a different palette from the first library.\n"
                  "Merging them would recolour that part. Re-run the private skins so "
                  "they all borrow the same palette." % os.path.basename(tlb_path))
            return 2

        remap[part] = {}
        for e in lib.entries:
            pos = rrf.find_free_atlas_space(merged, e.sizeX, e.sizeY)
            if pos is None:
                print("REFUSING: ran out of atlas space packing %s entry id %d (%dx%d).\n"
                      "The merged model needs more than one 256x4096 atlas - reduce island "
                      "sizes (lower the unwrap angle limit) and try again."
                      % (part, e.id, e.sizeX, e.sizeY))
                return 3
            npx, npy = pos
            new_id = rrf.append_tlb_entry(merged, e.sizeX, e.sizeY, npx, npy,
                                          filename=e.filename)
            remap[part][e.id] = new_id
            # copy the pixels
            sx0, sy0 = e.posX * 16, e.posY * 16
            dx0, dy0 = npx * 16, npy * 16
            for y in range(e.sizeY):
                src = rows[sy0 + y][sx0:sx0 + e.sizeX]
                canvas[dy0 + y][dx0:dx0 + e.sizeX] = src
            total_entries += 1
        print("   %-12s %3d entries -> ids %s"
              % (part, len(lib.entries),
                 "%d..%d" % (min(remap[part].values()), max(remap[part].values()))
                 if remap[part] else "-"))

    # ---- rewrite every face to the merged library ------------------------------------
    changed = skipped = 0
    for p in range(obj_count):
        name = part_names.get(p, "")
        table = remap.get(name)
        mesh_off = rrf._mesh_record_offset(p, 0)
        face_count, face_list = struct.unpack_from("<II", data, mesh_off + 4)
        if face_count == 0 or face_list + face_count * 24 > len(data):
            continue
        for f in range(face_count):
            off = face_list + f * 24
            to = struct.unpack_from("<I", data, off + 12)[0]
            if not (to & 0x80000000):
                continue                      # flat-colour face, leave alone
            upper, slot, pid = rrf.decode_texture_offset(to)
            if table is None or pid not in table:
                skipped += 1
                continue
            new_to = rrf.encode_texture_offset(upper, 0, table[pid])
            struct.pack_into("<I", data, off + 12, new_to)
            changed += 1

    # ---- write everything ------------------------------------------------------------
    rrf.write_tlb_library(out_tlb, merged)
    # write_bmp8 wants an (H, W) numpy array in BOTTOM-UP row order (it writes rows
    # straight to disk, and a positive-height BMP stores them bottom-first). `canvas` is
    # built top-down - same convention read_bmp8() hands back - so it has to be reversed
    # here, or every merged atlas comes out upside down.
    import numpy as np
    indices = np.array([list(r) for r in reversed(canvas)], dtype=np.uint8)
    rrf.write_bmp8(out_bmp, indices, rrf.tlb_palette_to_rgb(base_palette))
    open(out_rrf, "wb").write(bytes(data))
    rrf.write_rri(out_rri, {0: out_tlb})

    print("\nmerged %d entries into one library" % total_entries)
    print("faces repointed: %d   left alone: %d" % (changed, skipped))
    for f in (out_rrf, out_tlb, out_bmp, out_rri):
        print("   %-40s %d bytes" % (os.path.basename(f), os.path.getsize(f)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
