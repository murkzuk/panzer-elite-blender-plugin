bl_info = {
    "name": "Panzer Elite RRF Importer",
    "author": "Jeff",
    "version": (0, 56, 0),
    "blender": (3, 6, 0),
    "location": "File > Import > Panzer Elite Model (.rrf), File > Export > Panzer Elite Texture Atlas (.bmp), Edit Mode mesh context menu > PE: Detach Face From Shared Texture Cell / PE: Write Vertex Positions / PE: Delete Face(s)",
    "description": "Import Panzer Elite (1999) .RRF model files: geometry, part hierarchy, pivots, gameplay attribute tags, and (optionally) UVs/texture from a matching .TLB texture library. Export a repainted texture atlas back out for re-use in the game, detach individual faces from a shared texture cell onto their own independent copy, write repositioned vertices back to the model's own .RRF (same-topology geometry edits), and delete faces with a real write-back (resizes the part and shifts every later part's file offsets accordingly).",
    "category": "Import-Export",
}

import struct
import os
import shutil
import math
import bpy
import bmesh
from bpy_extras.io_utils import ImportHelper, ExportHelper
from bpy.props import StringProperty, BoolProperty, EnumProperty, FloatVectorProperty, IntProperty, FloatProperty
from mathutils import Matrix, Vector

ATLAS_EXPECTED_SIZE = (256, 4096)

HEADER_SIZE = 20
PART_SIZE = 512
MESH_SIZE = 36
FACE_SIZE = 24
VERTEX_SIZE = 12
MAX_LOD = 8
MAX_CHILD = 32

MAT_SHADING_MASK = 0x3
MAT_SHADING_DEEP = 0x3
MAT_TEXTRUE_MASK = 0xC
MAT_QUAD = 0x10
OBJ_ATTRIB_HIDE = 0x80000000

# Mirrors the real ObjEdit's own "Select Theatre" dialog (Desert/Italy/Normandy/Custom A/
# Custom B/Custom C/None) - it doesn't guess which library a model uses, it just asks the
# user this exact question and filters by name prefix. Confirmed against the real Texture
# folder: Desert1-8.TLB, Italy1-6.TLB, Normandy1-6.TLB ship with the base game; CustomA*/
# CustomB*/CustomC* are typically added by mods. See find_matching_tlbs()'s name_prefix
# parameter and IMPORT_OT_rrf.theatre.
THEATRE_PREFIXES = {
    "DESERT": "Desert",
    "ITALY": "Italy",
    "NORMANDY": "Normandy",
    "CUSTOM_A": "CustomA",
    "CUSTOM_B": "CustomB",
    "CUSTOM_C": "CustomC",
}

# .TLB texture library format (decoded from ObjEdit\ImageLibUnit.pas Save1Click/LoadLib):
# header(8) + libPal(2048) + libMatPal(256) then libParts[4096] @ 112 bytes each.
TLB_PARTS_OFFSET = 2312
TLB_ENTRY_SIZE = 112
TLB_MAX_PARTS = 4096
# ObjEdit can have up to 32 texture libraries loaded at once (numbered slot buttons in
# ImageLibUnit.pas); a face's textureOfset low 31 bits is (part_id + slot*TLB_MAX_PARTS),
# where "slot" is whichever of the 32 slots that library happened to be loaded into during
# the session it was painted in - not a fixed property of the .TLB file. Confirmed by a
# live paint-and-save test in the real ObjEdit (PEx_105_ObjEdit.exe): painting a face from
# a library titled "8202" wrote textureOfset low31=8202, and CustomB3.TLB's part id=10
# (sizeX=64,sizeY=128, matching the tool's own displayed size) resolves exactly when
# slot=2 (8202 - 2*4096 = 10). The remainder mod TLB_MAX_PARTS always identifies the
# right entry regardless of how large the implied slot is - see resolve_texture_id()
# below. A small residual of faces may still fail to resolve (a stray/removed .TLB
# entry, or genuinely a runtime-only handle) - real content checked resolves 88-100%
# once the correct library/libraries are used, so this is the rare exception, not the norm.
MAX_LIBS = 32
# Every _8.BMP/_24.BMP atlas is a fixed 256x4096 image (confirmed from the actual BMP
# header, not just file size - 256x4096 and 1024x1024 have the same pixel count so file
# size alone doesn't distinguish them. Matches MAX_X=15/MAX_Y=255 tile-grid constants in
# ImageLibUnit.pas: 16 tiles wide x 256 tiles tall = 256x4096).
ATLAS_WIDTH = 256
ATLAS_HEIGHT = 4096

# Real-world-meter conversion. PE's own rrCoord fixed-point values are NOT meters (a
# raw import comes out ~6-9x too big on every axis).
#
# CORRECTED 2026-08-12 from the real engine source, replacing an earlier empirically-
# fitted 0.14. `RRF object hex/object.c` (Alan Barber's own PE-X collision-box code)
# states it outright, in a comment on the pad it applies to a landscape object's box:
#
#     // 0x21800/0x400000 = .327148 meters in RRF vertex position
#
# Decoded: 0x21800 = 137216 raw, which in this format's 16.16 fixed point (RRF_FORMAT.md)
# is 137216/65536 = 2.09375 units. The quoted 0.3271484375 m / 2.09375 units =
# 0.15625 m per unit exactly - equivalently meters = raw * 10 / 0x400000, i.e. a clean
# 64 units = 10 meters. The comment's own metre figure reproduces to the last digit, so
# this is an exact engine constant, not a fit.
#
# The previous 0.14 came from fitting whole-model bounding boxes to known historical
# vehicle dimensions. That method is biased low: a whole-model box includes gun
# overhang, Schuerzen side skirts and open hatches, so the raw box is inflated relative
# to the hull figure it was being matched against, which drags the fitted scale down -
# which is exactly the ~10% shortfall seen (0.14 / 0.15625 = 0.896). Re-measured after
# this correction: a real KV-1 (Kv176-0.rrf) comes out 3.23m wide / 2.77m high against
# a real 3.32m / 2.71m (-2.7% / +2.3%), versus -12.8% / -8.4% under 0.14.
PE_TO_METERS_SCALE = 0.15625

# From Rrattrib.h - only the common/recognizable ones, for a readable custom property.
# Complete OBJ_TYPE_ set transcribed from the real Rrattrib.h - all 89 constants, where
# an earlier hand-made table named only 37. The low byte of a part's objAttribut holds
# this type, and it is what makes a part FUNCTION in game rather than just render: a
# turret that is not tagged TURM is only geometry.
OBJ_TYPE_NAMES = { 0: "HAUS", 1: "TREE", 2: "WALL", 3: "TANK", 4: "TURM", 5: "KANNONE",
    6: "MUZZLE", 7: "KETTENVERTEX", 8: "RADVERTEX", 9: "MG1", 10: "MG2", 11: "MG3",
    12: "MG4", 13: "HATCH", 14: "SMOKE1", 15: "SMOKE2", 16: "SMOKE3", 17: "SMOKE4",
    18: "SMOKE5", 19: "SMOKE6", 20: "SMOKEM", 21: "DUSTH", 22: "DUSTV", 23: "VISIONAREA",
    24: "COMMANDERPOS", 25: "BINOCULAR", 26: "SCOPESCALE", 27: "DISPNO", 28: "DISPWATER",
    29: "DISPOEL", 30: "DISPOELPRESURE", 31: "DISPGEAR", 32: "DISPSPEED", 33: "DISPRPM",
    34: "DISPTURM", 35: "DISP9", 36: "DISP10", 37: "DISP11", 38: "DISP12", 39: "DISP13",
    40: "DISP14", 41: "DISP15", 42: "DISPBLACKGFX", 43: "DISPTRACK1", 44: "DISPTRACK2",
    45: "DISPTRACK3", 46: "DISPTRACK4", 47: "DISPTRACKR1", 48: "DISPTRACKR2",
    49: "DISPTRACKR3", 50: "DISPTRACKR4", 62: "M10X6", 63: "M10X1", 88: "DISP62",
    89: "DISP63", 90: "DISP64", 91: "MANTLEXA", 92: "SCHUERZEN", 93: "HSCHUERZEN",
    95: "HEDGEHOG", 96: "RADIO", 97: "WETSTORAGE", 98: "PLATESTURRET", 99: "PLATESHULL",
    100: "UMBRELLA", 101: "COMSPRITE", 102: "TRACKL", 103: "TRACKR", 104: "MISC1",
    105: "MISC2", 106: "BARREL", 110: "IDELER", 114: "CREW_DRIVER", 115: "CREW_RADIOOP",
    116: "CREW_GUNNER", 117: "CREW_LOADER", 118: "CREW_COMMANDER", 120: "JUNK",
    121: "CAMMO", 122: "HATCH2", 127: "PINE", 128: "PINE2", 129: "PALM", 130: "SIGN",
    131: "BARE", 133: "INFTRENCH", 135: "SOLID", 136: "SOLID_2", 255: "NULL",
}


def fixed_to_float(raw):
    """rrCoord/rrAngle are always 32-bit 16.16 fixed point, never plain float, in every file checked."""
    return raw / 65536.0


def float_to_fixed(value):
    """Inverse of fixed_to_float() - rounds to the nearest representable 16.16
    fixed-point int32. Raises if the value doesn't fit in a signed int32 once scaled,
    which would mean the coordinate is already far outside anything a real model uses."""
    raw = round(value * 65536.0)
    if not (-2**31 <= raw < 2**31):
        raise ValueError(f"value {value} does not fit in a signed 16.16 fixed-point int32")
    return raw


def _corner_xy(raw_field):
    """UV pixel offset within the assigned texture part, packed into the upper 16 bits of
    v1/v2/v3/textureHalf (confirmed in Rrdwire.c rrSetTexture: (yStart<<24)|(xSize<<16) etc.)."""
    upper = (raw_field >> 16) & 0xFFFF
    x = upper & 0xFF
    y = (upper >> 8) & 0xFF
    return x, y


class RRFPart:
    __slots__ = (
        "index", "name", "pivot", "obj_attribut", "parent_no", "child_count",
        "child_array", "vertices", "faces", "face_texture_id", "face_uv_corners",
        "face_crop_size",
    )


def read_tlb(filepath):
    """Returns {texture_id: (posX, posY, sizeX, sizeY)} - posX/posY are in 16px tile units."""
    with open(filepath, "rb") as f:
        data = f.read()

    libNextID, libEntryCount = struct.unpack_from("<ii", data, 0)
    libEntryCount = max(0, min(libEntryCount, TLB_MAX_PARTS))

    parts = {}
    for i in range(libEntryCount):
        off = TLB_PARTS_OFFSET + i * TLB_ENTRY_SIZE
        entry_id, = struct.unpack_from("<i", data, off)
        cutX, cutY, sizeX, sizeY, posX, posY = struct.unpack_from("<iiiiii", data, off + 84)
        parts[entry_id] = (posX, posY, sizeX, sizeY)
    return parts


TLB_FILE_SIZE = 8 + 2048 + 256 + TLB_MAX_PARTS * TLB_ENTRY_SIZE  # 461064, every real .TLB checked


class TLBEntry:
    __slots__ = ("id", "filename", "cutX", "cutY", "sizeX", "sizeY", "posX", "posY", "_reserved")


class TLBLibrary:
    __slots__ = ("lib_next_id", "palette", "mat_pal", "entries", "_raw_parts_baseline")


def read_tlb_library(filepath):
    """Full-fidelity .TLB read for anything that needs to WRITE the file back out -
    read_tlb() above only keeps what the importer needs (a texture_id -> rect lookup) and
    throws away the palette, libNextID counter, filenames, and crop origin, none of which
    round-trip through it. Returns a TLBLibrary.

    Entry ids are kept exactly as stored, with no assumption they fit in [0, TLB_MAX_PARTS)
    - real content has occasional entries carrying a much larger id inherited from a
    different library the content was originally copied from (confirmed on CustomB1.TLB:
    2 of 275 entries carry an id in the millions, with a real "Desert1_8.bmp" source
    filename - clearly reused content, not corruption). Those entries can never actually
    be reached by resolve_texture_id()'s modulo lookup (candidate is always < TLB_MAX_PARTS),
    but they're still real, valid file content and must round-trip untouched regardless.

    Each entry's trailing 4 bytes (offset 108, TLB_FORMAT.md's "unused" field) are kept
    too, as `_reserved` - real files have non-zero leftover bytes there (an editor-only
    in-memory pointer that apparently never gets cleared before saving), not always zero
    as first assumed. Meaningless to interpret, but real on-disk content that a byte-exact
    round-trip needs to preserve rather than silently zero out.

    Also keeps the *entire* 4096-slot parts array as `_raw_parts_baseline`, not just the
    first libEntryCount entries - slots beyond libEntryCount aren't zeroed either in real
    files (confirmed on CustomA11.TLB: stale non-zero bytes sitting past its own
    libEntryCount=75, presumably a deleted/replaced entry's leftover data the editor never
    bothered clearing). write_tlb_library() uses this as a base layer and only overwrites
    the slots covered by `entries`, so anything else round-trips exactly regardless of
    what it actually is.
    """
    with open(filepath, "rb") as f:
        data = f.read()

    if len(data) != TLB_FILE_SIZE:
        # Found one real file like this (`_Normandy7.TLB`, leading underscore - the same
        # "disabled" naming convention used elsewhere in this asset set): a completely
        # normal-looking header and entry table, but ~3.1MB of repeating junk bytes
        # appended after the real 461,064-byte structure. Refuse rather than silently
        # dropping that tail on write - a genuine format variant would need investigating,
        # not guessing at here.
        raise ValueError(
            f"{filepath} is {len(data)} bytes, not the expected {TLB_FILE_SIZE} - not a "
            f"standard .TLB (or has trailing garbage/is corrupted); refusing to read since "
            f"a byte-exact round trip can't be guaranteed"
        )

    lib_next_id, lib_entry_count = struct.unpack_from("<ii", data, 0)
    lib_entry_count = max(0, min(lib_entry_count, TLB_MAX_PARTS))

    library = TLBLibrary()
    library.lib_next_id = lib_next_id
    library.palette = bytes(data[8:8 + 2048])
    library.mat_pal = bytes(data[2056:2056 + 256])
    library.entries = []
    library._raw_parts_baseline = bytes(data[TLB_PARTS_OFFSET:TLB_PARTS_OFFSET + TLB_MAX_PARTS * TLB_ENTRY_SIZE])

    for i in range(lib_entry_count):
        off = TLB_PARTS_OFFSET + i * TLB_ENTRY_SIZE
        entry_id, = struct.unpack_from("<i", data, off)
        cutX, cutY, sizeX, sizeY, posX, posY = struct.unpack_from("<iiiiii", data, off + 84)
        entry = TLBEntry()
        entry.id = entry_id
        entry.filename = bytes(data[off + 4:off + 84])  # raw char[80], kept verbatim - author-time path, no encoding to assume
        entry.cutX, entry.cutY = cutX, cutY
        entry.sizeX, entry.sizeY = sizeX, sizeY
        entry.posX, entry.posY = posX, posY
        entry._reserved = bytes(data[off + 108:off + 112])
        library.entries.append(entry)

    return library


# The .TLB palette block is 2048 bytes at offset 8, but only the first 1024 carry data:
# 256 entries of 4 bytes, and the remaining 1024 are zero in every real library checked
# (20/20). Byte order is [R, G, B, 0] - the REVERSE of a BMP palette's [B, G, R, 0].
# Both facts come from the engine itself: rrSendTexturePal() takes 256*4 bytes and
# unpacks them as red = (uint8)pal[i], green = pal[i]>>8, blue = pal[i]>>16, and a real
# Normandy1.TLB/BMP pair matches entry for entry under that swap.
TLB_PALETTE_SIZE = 2048
TLB_PALETTE_ENTRIES = 256


def read_tlb_palette(tlb_filepath):
    """Returns a real .TLB's raw 2048-byte palette block, ready to hand to
    new_tlb_library(). The cheapest correct way to give a new library real colours is to
    copy one from a library of the theatre the model belongs to."""
    with open(tlb_filepath, "rb") as f:
        f.seek(8)
        block = f.read(TLB_PALETTE_SIZE)
    if len(block) != TLB_PALETTE_SIZE:
        raise ValueError("%s is too short to contain a palette block" % tlb_filepath)
    return block


def tlb_palette_to_rgb(block):
    """Unpacks a .TLB palette block into 256 (R, G, B) tuples - the same shape
    read_bmp8_palette() returns, so either can feed quantize_to_palette()."""
    return [(block[i * 4], block[i * 4 + 1], block[i * 4 + 2]) for i in range(TLB_PALETTE_ENTRIES)]


def rgb_to_tlb_palette(rgb_entries):
    """Packs 256 (R, G, B) tuples into a .TLB palette block: [R,G,B,0] per entry, then
    1024 zero bytes of padding to fill the block, matching every real library."""
    out = bytearray()
    for i in range(TLB_PALETTE_ENTRIES):
        r, g, b = rgb_entries[i] if i < len(rgb_entries) else (0, 0, 0)
        out += bytes((r & 0xFF, g & 0xFF, b & 0xFF, 0))
    out += bytes(TLB_PALETTE_SIZE - len(out))
    return bytes(out)


def find_theatre_palette(texture_folder, theatre_prefix=None):
    """Finds a real .TLB to borrow a palette from, preferring the given theatre prefix
    ("Normandy", "Desert", "Italy", "CustomA"...). Returns (palette_block, source_path),
    or (None, None) if the folder holds no readable library.

    A brand-new library has no palette of its own, and the game reads paletted 8-bit
    bitmaps - so painting into one with an all-zero (black) palette produces a black
    texture no matter what colours were painted. Borrowing from the model's own theatre
    also keeps a repaint looking native next to stock vehicles."""
    if not texture_folder or not os.path.isdir(texture_folder):
        return None, None
    names = [n for n in os.listdir(texture_folder) if n.lower().endswith(".tlb")]
    if theatre_prefix:
        preferred = [n for n in names if n.lower().startswith(theatre_prefix.lower())]
        names = preferred + [n for n in names if n not in preferred]
    for name in names:
        path = os.path.join(texture_folder, name)
        try:
            block = read_tlb_palette(path)
        except (OSError, ValueError):
            continue
        if any(block):          # skip any library that is itself all-zero
            return block, path
    return None, None


def new_tlb_library(palette=None):
    """A blank TLBLibrary for building a .TLB from scratch (no existing file to base it
    on) - zero-filled palette/mat_pal/parts-array baseline, id counter starting at 0, no
    entries. Real .TLB files always have SOME palette data, but this project has no
    genuine "build a fresh library" use case yet (only modifying existing ones), so this
    is an honestly-blank starting point, not a claim about what a real fresh ObjEdit
    library's palette looks like."""
    library = TLBLibrary()
    library.lib_next_id = 0
    # A zero palette is not a neutral default - the game reads 8-bit paletted bitmaps,
    # so an all-zero palette renders every painted pixel black regardless of what was
    # painted. Callers should pass a real one (read_tlb_palette / find_theatre_palette);
    # zeros remain only as the last-resort fallback when no library can be read at all.
    library.palette = bytes(TLB_PALETTE_SIZE) if palette is None else bytes(palette)
    library.mat_pal = bytes(256)
    library.entries = []
    library._raw_parts_baseline = bytes(TLB_MAX_PARTS * TLB_ENTRY_SIZE)
    return library


def write_tlb_library(filepath, library):
    """Writes a TLBLibrary back out to the exact 461,064-byte .TLB layout - the write side
    of read_tlb_library(). Slots not covered by `entries` keep whatever was in
    `_raw_parts_baseline` at that position (see read_tlb_library()'s docstring) rather
    than being zeroed, so modifying a handful of entries in an existing library round-trips
    every other byte in the file exactly."""
    if len(library.entries) > TLB_MAX_PARTS:
        raise ValueError(f"{len(library.entries)} entries exceeds the .TLB format's {TLB_MAX_PARTS}-entry limit")

    buf = bytearray(TLB_FILE_SIZE)
    struct.pack_into("<ii", buf, 0, library.lib_next_id, len(library.entries))
    buf[8:8 + 2048] = library.palette
    buf[2056:2056 + 256] = library.mat_pal
    buf[TLB_PARTS_OFFSET:TLB_PARTS_OFFSET + TLB_MAX_PARTS * TLB_ENTRY_SIZE] = library._raw_parts_baseline

    for i, entry in enumerate(library.entries):
        off = TLB_PARTS_OFFSET + i * TLB_ENTRY_SIZE
        struct.pack_into("<i", buf, off, entry.id)
        buf[off + 4:off + 84] = entry.filename[:80].ljust(80, b"\x00")
        struct.pack_into(
            "<iiiiii", buf, off + 84,
            entry.cutX, entry.cutY, entry.sizeX, entry.sizeY, entry.posX, entry.posY,
        )
        buf[off + 108:off + 112] = entry._reserved

    with open(filepath, "wb") as f:
        f.write(buf)


def append_tlb_entry(library, sizeX, sizeY, posX, posY, cutX=0, cutY=0, filename=b""):
    """Allocates a new entry: assigns library.lib_next_id as the id (matching ObjEdit's
    own running counter - confirmed against real content where libNextID sits exactly one
    past the highest *normal* id in nearly every file checked) and increments it, so newly
    assigned ids stay small and land correctly within resolve_texture_id()'s modulo lookup
    range, regardless of any pre-existing oddities already in the file. Caller is
    responsible for finding free atlas space (posX/posY) - this only manages the .TLB's
    own id counter and entry array. Returns the newly assigned id."""
    if len(library.entries) >= TLB_MAX_PARTS:
        raise ValueError(f"library is full ({TLB_MAX_PARTS} entries)")

    filename_bytes = filename if isinstance(filename, (bytes, bytearray)) else filename.encode("latin-1")

    entry = TLBEntry()
    entry.id = library.lib_next_id
    entry.filename = filename_bytes
    entry.cutX, entry.cutY = cutX, cutY
    entry.sizeX, entry.sizeY = sizeX, sizeY
    entry.posX, entry.posY = posX, posY
    entry._reserved = b"\x00\x00\x00\x00"
    library.entries.append(entry)
    library.lib_next_id += 1
    return entry.id


ATLAS_TILE_SIZE = 16
ATLAS_GRID_WIDTH = ATLAS_WIDTH // ATLAS_TILE_SIZE    # 16 tile columns
ATLAS_GRID_HEIGHT = ATLAS_HEIGHT // ATLAS_TILE_SIZE  # 256 tile rows


def find_free_atlas_space(library, sizeX, sizeY):
    """Finds an unused posX/posY (tile-grid units, per TLB_FORMAT.md) in `library`'s atlas
    big enough for a new sizeX x sizeY (pixels) entry, without overlapping any existing
    entry. Needed for the "detach face from shared texture cell" feature (TODO.md) - once
    append_tlb_entry() has an id, it still needs somewhere real in the shared atlas image
    to actually live.

    Confirmed against all 25,614 real entries checked across the asset set: every one has
    sizeX/sizeY as an exact multiple of the 16px tile (0 exceptions) and the grid really is
    16 columns x 256 rows (max posX seen: 15, max posY seen: 254) - matching ImageLibUnit
    .pas's MAX_X=15/MAX_Y=255 constants exactly, so this isn't guessed, it's measured.

    Deliberately tolerant of two rare-but-real oddities rather than raising on them:
    - A handful of entries (about 1 in 2500) claim a size/position that doesn't actually
      fit the 16x256 grid at all (e.g. one real entry claims sizeX=1120px, wider than the
      entire 256px-wide atlas). Nonsensical claims like this can't reliably tell us
      anything about real occupied space, so they're skipped rather than treated as
      blocking an otherwise-free area.
    - At least one real library (CustomA14.TLB) has entries that genuinely overlap each
      other in-bounds - almost certainly a stale/superseded entry whose old space was
      later reused by something newer, with the old record never cleaned up (the same
      "real files don't tidy up after themselves" pattern found while building the .TLB
      writer). Both entries' claimed tiles are simply marked occupied; no special handling
      needed since a tile occupied by more than one entry is still just occupied.

    Returns (posX, posY) in tile-grid units, or None if no free space of the requested
    size exists anywhere in the atlas."""
    if sizeX <= 0 or sizeY <= 0 or sizeX % ATLAS_TILE_SIZE or sizeY % ATLAS_TILE_SIZE:
        raise ValueError(f"sizeX/sizeY ({sizeX}x{sizeY}) must be positive multiples of {ATLAS_TILE_SIZE}")

    tiles_w = sizeX // ATLAS_TILE_SIZE
    tiles_h = sizeY // ATLAS_TILE_SIZE
    if tiles_w > ATLAS_GRID_WIDTH or tiles_h > ATLAS_GRID_HEIGHT:
        return None

    occupied = set()
    for entry in library.entries:
        if entry.sizeX <= 0 or entry.sizeY <= 0 or entry.sizeX % ATLAS_TILE_SIZE or entry.sizeY % ATLAS_TILE_SIZE:
            continue  # nonsensical size, can't reliably mark any tiles - see docstring
        etw = entry.sizeX // ATLAS_TILE_SIZE
        eth = entry.sizeY // ATLAS_TILE_SIZE
        if entry.posX < 0 or entry.posY < 0 or entry.posX + etw > ATLAS_GRID_WIDTH or entry.posY + eth > ATLAS_GRID_HEIGHT:
            continue  # doesn't fit the real grid at all - see docstring
        for tx in range(entry.posX, entry.posX + etw):
            for ty in range(entry.posY, entry.posY + eth):
                occupied.add((tx, ty))

    for posY in range(ATLAS_GRID_HEIGHT - tiles_h + 1):
        for posX in range(ATLAS_GRID_WIDTH - tiles_w + 1):
            if all((posX + dx, posY + dy) not in occupied for dx in range(tiles_w) for dy in range(tiles_h)):
                return posX, posY

    return None


def detect_uv_islands(bm, uv_layer, faces=None, epsilon=1e-5):
    """Groups faces into UV islands (connected components in UV space), for the "give a
    whole vehicle its own private skin" workflow - each island becomes one new .TLB
    entry, sized to fit it, once Smart UV Project (or any other unwrap) has already laid
    out non-overlapping islands in the mesh's active UV map.

    Two faces sharing a mesh edge are only counted as UV-connected if their UV
    coordinates *at both ends of that shared edge* also match (within `epsilon`) - an
    unwrap seam breaks UV continuity there even though the underlying mesh edge is still
    shared, which is exactly the boundary between two different islands. Plain
    mesh-adjacency (ignoring UV) would merge every island of a single connected
    part into one, which is the one thing this must not do.

    Returns a list of lists of face indices (each inner list is one island). Faces
    outside `faces` (if given) are ignored entirely, including as neighbors."""
    faces = list(faces) if faces is not None else list(bm.faces)
    face_ids = {f.index for f in faces}
    parent = {f.index: f.index for f in faces}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    def close(uv_a, uv_b):
        return abs(uv_a[0] - uv_b[0]) < epsilon and abs(uv_a[1] - uv_b[1]) < epsilon

    face_vert_uv = {}
    for f in faces:
        for loop in f.loops:
            face_vert_uv[(f.index, loop.vert.index)] = tuple(loop[uv_layer].uv)

    for f in faces:
        for edge in f.edges:
            v1, v2 = edge.verts[0].index, edge.verts[1].index
            uv1_f = face_vert_uv.get((f.index, v1))
            uv2_f = face_vert_uv.get((f.index, v2))
            if uv1_f is None or uv2_f is None:
                continue
            for other in edge.link_faces:
                if other.index == f.index or other.index not in face_ids:
                    continue
                uv1_o = face_vert_uv.get((other.index, v1))
                uv2_o = face_vert_uv.get((other.index, v2))
                if uv1_o is None or uv2_o is None:
                    continue
                if close(uv1_f, uv1_o) and close(uv2_f, uv2_o):
                    union(f.index, other.index)

    islands = {}
    for f in faces:
        islands.setdefault(find(f.index), []).append(f.index)
    return list(islands.values())


def size_islands_to_tiles(uv_bboxes, min_tiles=1, max_tiles=None, budget_fraction=0.6):
    """Converts each island's UV-space bounding box (min_u, min_v, max_u, max_v) into a
    (tiles_w, tiles_h) size in 16px-tile units for a brand-new, empty atlas, preserving
    each island's own aspect ratio and relative size (islands that occupy more of the
    source UV space - which Smart UV Project already scales roughly by real surface
    area - get proportionally more atlas pixels).

    This is an engineering choice, not a reverse-engineered fact: there's no "correct"
    pixel density to give a freshly unwrapped model, since the original format has no
    concept of one texel-per-real-world-unit standard. The approach here: treat each
    island's UV bbox area as its relative weight, scale all islands uniformly so their
    *total* tile area fits within `budget_fraction` of the full atlas grid (leaving
    headroom rather than packing edge-to-edge), then round each island's own width/height
    up to the nearest tile and clamp to [min_tiles, max_tiles] per side - `max_tiles`
    defaults to ATLAS_GRID_WIDTH (16 tiles = 256px), this format's real per-face crop cap
    (RRF_FORMAT.md), so no single island can ever request more than one .TLB entry can
    actually hold.

    Returns (sizes, warnings): `sizes` is a list of (tiles_w, tiles_h) parallel to
    `uv_bboxes`; `warnings` lists any island indices that had to be clamped down from
    their proportional size to fit the format's per-entry cap (still usable, just
    lower-resolution than the scaling alone would have given them)."""
    if max_tiles is None:
        max_tiles = ATLAS_GRID_WIDTH
    n = len(uv_bboxes)
    if n == 0:
        return [], []

    widths = [max(max_u - min_u, 1e-9) for min_u, min_v, max_u, max_v in uv_bboxes]
    heights = [max(max_v - min_v, 1e-9) for min_u, min_v, max_u, max_v in uv_bboxes]
    areas = [w * h for w, h in zip(widths, heights)]
    total_area = sum(areas) or 1e-9

    atlas_tile_budget = ATLAS_GRID_WIDTH * ATLAS_GRID_HEIGHT * budget_fraction
    # scale so that sum(px_area_i) == atlas_tile_budget, preserving each island's own
    # aspect ratio: px_area_i = atlas_tile_budget * (area_i / total_area), and
    # px_w_i/px_h_i = w_i/h_i (same aspect as its own UV bbox).
    sizes = []
    warnings = []
    for i, (w, h) in enumerate(zip(widths, heights)):
        target_px_area = atlas_tile_budget * (areas[i] / total_area)
        aspect = w / h
        # target_px_area = px_w * px_h, px_w = aspect * px_h  =>  px_h = sqrt(target_px_area / aspect)
        px_h = max((target_px_area / aspect) ** 0.5, float(min_tiles))
        px_w = px_h * aspect
        tiles_w = max(min_tiles, min(max_tiles, round(max(px_w, min_tiles))))
        tiles_h = max(min_tiles, min(max_tiles, round(max(px_h, min_tiles))))
        if round(px_w) > max_tiles or round(px_h) > max_tiles:
            warnings.append(i)
        sizes.append((tiles_w, tiles_h))
    return sizes, warnings


def pack_islands_shelf(sizes, grid_width=None, grid_height=None):
    """Packs a list of (tiles_w, tiles_h) sizes (16px-tile units) into a `grid_width` x
    `grid_height` tile grid (defaults to one whole fresh .TLB atlas, ATLAS_GRID_WIDTH x
    ATLAS_GRID_HEIGHT) using simple shelf packing: sort tallest-first, fill each row
    left to right, start a new row once the current one won't fit the next island.

    Not space-optimal (a true 2D bin-packer would do better), but simple, deterministic,
    and easy to verify by hand - reasonable for the actual scale here (a vehicle's worth
    of UV islands, dozens at most, packed into a 16x256-tile atlas with real headroom).

    Returns a list of (posX, posY) tile-grid positions parallel to `sizes`. Raises
    ValueError (naming which island, by index, and why) rather than silently truncating
    or overlapping if something doesn't fit - this project's own testing found "assume
    it always fits" a bad habit, see TODO.md's "private skin" scoping note."""
    grid_width = ATLAS_GRID_WIDTH if grid_width is None else grid_width
    grid_height = ATLAS_GRID_HEIGHT if grid_height is None else grid_height

    order = sorted(range(len(sizes)), key=lambda i: -sizes[i][1])
    positions = [None] * len(sizes)
    cursor_x = 0
    cursor_y = 0
    shelf_height = 0
    for i in order:
        w, h = sizes[i]
        if w > grid_width or h > grid_height:
            raise ValueError(f"island {i} is {w}x{h} tiles, too big for a {grid_width}x{grid_height}-tile atlas on its own")
        if cursor_x + w > grid_width:
            cursor_x = 0
            cursor_y += shelf_height
            shelf_height = 0
        if cursor_y + h > grid_height:
            raise ValueError(
                f"island {i} ({w}x{h} tiles) doesn't fit - ran out of room in the "
                f"{grid_width}x{grid_height}-tile atlas after packing {i} of {len(sizes)} islands"
            )
        positions[i] = (cursor_x, cursor_y)
        cursor_x += w
        shelf_height = max(shelf_height, h)
    return positions


def plan_private_skin(bm, uv_layer, faces=None, budget_fraction=0.6, per_face=False):
    """Planning step for "give this part its own private, freely-paintable skin"
    (TODO.md): given a mesh that already has a real UV unwrap (e.g. Smart UV Project),
    works out everything needed to move it onto a brand-new, dedicated .TLB atlas -
    detects UV islands, sizes each proportional to its UV footprint, and packs them into
    a fresh empty atlas. Doesn't touch the mesh, .RRF, or any file - inspectable/testable
    before anything is written, see apply_private_skin() for that part.

    Returns (plans, warnings): `plans` is a list of
    {"faces": [face_index,...], "bbox": (min_u,min_v,max_u,max_v), "tiles": (w,h), "pos": (posX,posY)}
    dicts, one per island; `warnings` is the list from size_islands_to_tiles() (islands
    that had to be clamped to the format's 256x256 per-entry cap)."""
    # Real bug caught 2026-07-08: bm.faces[fi] below needs a fresh lookup table - fine
    # right after a plain unwrap, but any UV operator run afterward (average_islands_scale,
    # pack_islands, etc.) can leave the previous table stale, causing an IndexError even
    # though the face count itself hasn't changed.
    bm.faces.ensure_lookup_table()
    faces = list(faces) if faces is not None else list(bm.faces)
    if per_face:
        # One rectangle per FACE. Faces sharing an island overlap once each is snapped to
        # its bounding box (98 overlapping pairs on a five-part Psw222), so painting one
        # face alters its neighbour. Stock PE content gives every face its own rectangle;
        # this matches it. Costs atlas space and gives up continuous seams across a
        # surface - neither of which the format supported anyway.
        islands = [[f.index if hasattr(f, "index") else f] for f in faces]
    else:
        islands = detect_uv_islands(bm, uv_layer, faces)

    bboxes = []
    for island in islands:
        us, vs = [], []
        for fi in island:
            for loop in bm.faces[fi].loops:
                u, v = loop[uv_layer].uv
                us.append(u)
                vs.append(v)
        bboxes.append((min(us), min(vs), max(us), max(vs)))

    # budget_fraction: how much of the atlas THIS part may claim. The default assumes the
    # part owns the whole atlas, which is right for a single private skin and wrong the
    # moment several parts are merged into one library - five parts at 0.6 each need 300%
    # of an atlas and the merge runs out of space. Callers merging N parts should pass
    # roughly 0.6/N. (tools/merge_private_skins.py)
    sizes, warnings = size_islands_to_tiles(bboxes, budget_fraction=budget_fraction)
    positions = pack_islands_shelf(sizes)

    plans = [
        {"faces": island, "bbox": bbox, "tiles": size, "pos": pos}
        for island, bbox, size, pos in zip(islands, bboxes, sizes, positions)
    ]
    return plans, warnings


def apply_private_skin(rrf_data, part_index, bm, uv_layer, plans, library, margin_px=2):
    """Write side of the "private skin" workflow: given plans from plan_private_skin()
    and a TLBLibrary to add entries to (pass a fresh new_tlb_library() for a genuinely
    new, dedicated atlas), allocates one .TLB entry per island (append_tlb_entry()),
    repoints every face in that island at it (patch_face_texture_id()), and writes each
    face's real per-vertex crop within the island's own packed rectangle
    (patch_face_corners_per_vertex()) - each vertex gets its own independent (x,y)
    position, preserving whatever real (possibly non-rectangular) UV shape the unwrap in
    `plans` produced, rather than collapsing a face to its bounding box.

    Reconsidered 2026-07-08: an earlier version of this function *did* collapse each
    face to its bounding box and write that via patch_face_corners()'s fixed
    v1=top-right/v2=top-left/v3=bottom-left/textureHalf=bottom-right pattern - which
    caused real, confirmed stretching/banding in ObjEdit (organic Smart-UV-Project
    shapes don't fit a plain rectangle). The next attempt forced every face into its own
    small rectangular grid cell to match that fixed pattern exactly - a real
    improvement, but still not fully resolved in a second ObjEdit test, and two
    triangles sharing a real mesh edge (a flat panel modeled as 2 triangles, the common
    case) could never share one clean rectangle under a *fixed*-role model regardless of
    cell sizing, since neither triangle can ever supply a 4th corner. That pointed at
    the fixed-role premise itself being wrong: this project's own read side, _corner_xy()
    (sourced from the real engine's Rrdwire.c), documents the field only as "a UV pixel
    offset within the assigned texture part" - a generic per-vertex coordinate, not a
    named role. The "v1=top-right" pattern was only ever confirmed from one community
    writer's own code for the one case of assigning a plain rectangle, not from the
    engine enforcing it. This version trusts the generic-coordinate reading instead:
    each vertex's own real position, computed from its actual relative position within
    the island's own UV bounding box (organic shape preserved), is written directly into
    whichever slot (v1/v2/v3/textureHalf) that vertex occupies in the face's *own*
    record - found via each bmesh vertex's pe_vertex_index custom attribute (stamped at
    import time), matched against the file's current v1/v2/v3/textureHalf vertex-index
    bits (the low 16 bits, untouched by any corner/texture-id patch). Two faces sharing
    a real mesh edge naturally agree there, since a shared vertex has exactly one real
    position and both faces read the same value for it - no special-case pairing logic
    needed.

    `margin_px` insets each island's actual content a couple pixels in from its entry's
    own edges, rather than mapping content flush to the boundary - confirmed directly by
    Alan/Brit44 on the public PEDG forum (2026-07-08, "Blender importer/exporter"
    thread): adjacent right-triangle texture crops bleed across their shared hypotenuse
    by about a pixel, so real PE texture work leaves a couple of pixels of buffer beyond
    a region's nominal content rather than filling it edge-to-edge. This only changes
    where content gets placed *within* an already-sized entry (see size_islands_to_tiles()
    for how the entry's own size is chosen) - it doesn't grow the entry or touch the
    packer, so it can't ever push something over the format's 256x256 per-entry cap.

    Mutates rrf_data and library in place (caller writes them out - see
    MESH_OT_pe_give_private_skin) and the mesh's UV layer directly. Returns the number
    of faces updated."""
    bm.faces.ensure_lookup_table()  # same staleness risk as plan_private_skin() - see there
    bm.verts.ensure_lookup_table()
    vertex_index_layer = bm.verts.layers.int.get("pe_vertex_index")

    updated = 0
    for plan in plans:
        tiles_w, tiles_h = plan["tiles"]
        posX, posY = plan["pos"]
        sizeX, sizeY = tiles_w * ATLAS_TILE_SIZE, tiles_h * ATLAS_TILE_SIZE
        new_id = append_tlb_entry(library, sizeX=sizeX, sizeY=sizeY, posX=posX, posY=posY)

        min_u, min_v, max_u, max_v = plan["bbox"]
        bbox_w = max(max_u - min_u, 1e-9)
        bbox_h = max(max_v - min_v, 1e-9)

        # Inset the usable content range by margin_px on each side (clamped so a very
        # small entry - the format's own 16px minimum - still leaves at least a 1px-wide
        # usable strip rather than inverting into a negative range).
        safe_margin = min(margin_px, (sizeX - 1) // 2, (sizeY - 1) // 2)
        usable_x0, usable_x1 = safe_margin, sizeX - 1 - safe_margin
        usable_y0, usable_y1 = safe_margin, sizeY - 1 - safe_margin

        for face_index in plan["faces"]:
            patch_face_texture_id(rrf_data, part_index, 0, face_index, new_id)
            face = bm.faces[face_index]

            off = _face_record_offset(rrf_data, part_index, 0, face_index)
            v1, v2, v3, _textureOfset, textureHalf, materialInfo = struct.unpack_from("<IIIIII", rrf_data, off)
            is_quad = bool(materialInfo & MAT_QUAD)
            slot_of_vidx = {v1 & 0xFFFF: "v1", v2 & 0xFFFF: "v2", v3 & 0xFFFF: "v3"}
            if is_quad:
                slot_of_vidx[textureHalf & 0xFFFF] = "textureHalf"

            corners = {}
            for loop in face.loops:
                u, v = loop[uv_layer].uv
                # Local pixel position within the newly allocated entry - x increases
                # left to right same as u; y increases top to bottom, so v (which
                # increases "up" in Blender's UV space) has to flip, matching the same
                # convention _corner_xy()/the importer's own atlas_y = (1-v)*ATLAS_HEIGHT
                # already use. Mapped into the inset [usable_x0, usable_x1] range, not
                # the entry's full [0, sizeX-1] span.
                local_x = usable_x0 + (u - min_u) / bbox_w * (usable_x1 - usable_x0)
                local_y = usable_y0 + (max_v - v) / bbox_h * (usable_y1 - usable_y0)
                lx = max(0, min(255, round(local_x)))
                ly = max(0, min(255, round(local_y)))

                atlas_x = posX * ATLAS_TILE_SIZE + lx
                atlas_y = posY * ATLAS_TILE_SIZE + ly
                loop[uv_layer].uv = (atlas_x / ATLAS_WIDTH, 1.0 - atlas_y / ATLAS_HEIGHT)

                file_vidx = loop.vert[vertex_index_layer] if vertex_index_layer is not None else None
                slot = slot_of_vidx.get(file_vidx)
                if slot is not None:
                    corners[slot] = (lx, ly)

            # SNAP TO AN AXIS-ALIGNED RECTANGLE.
            #
            # Stock PE content is 100% axis-aligned rectangles - all 137 textured faces of
            # a stock Psw222, no exceptions. The format stores a face's mapping as
            # origin+size, so the engine reduces any quad to a rectangle and stretches the
            # crop over it. A non-rectangular face therefore renders crisp in Blender
            # (which honours all four corners) and as smears in ObjEdit. Smart UV Project
            # was giving us 67% non-rectangular faces, which is the whole of the
            # long-standing "private-skin stretching" problem.
            #
            # Snapping each vertex to the NEARER edge of its own face's UV bounding box
            # forces a rectangle while keeping each vertex on the corner it already
            # occupied, so orientation and winding are preserved. Faces whose unwrapped
            # shape was not rectangular are mildly distorted - unavoidable, since the
            # format cannot express them at all.
            # The face is snapped to its own UV bounding box (real PE content is 100%
            # axis-aligned rectangles - all 137 faces of a stock Psw222), and that
            # rectangle is then written through patch_face_corners(), which packs it as
            # ORIGIN+SIZE the way rrSetTexture() does. Writing per-vertex positions
            # instead hands the engine a coordinate where it expects a width, and the
            # texture smears across the face.
            if corners:
                cxs = [xy[0] for xy in corners.values()]
                cys = [xy[1] for xy in corners.values()]
                patch_face_corners(rrf_data, part_index, 0, face_index,
                                   min(cxs), min(cys), max(cxs), max(cys))
            updated += 1
    return updated


def decode_texture_offset(value):
    """Splits a face's textureOfset into (upper16, library_slot, part_id).

    Layout confirmed from rrReNumTLB() (rrobjpex.c): the low 16 bits hold the library
    slot in bits 12-15 and the part id in bits 0-11, with a 32-library extension - a part
    id above 2047 means the real slot is slot+16 and the real id is id-2048. The upper 16
    bits (which include bit 31, the "is textured" flag) are carried around untouched by
    the real code and are returned here so they can be put back verbatim."""
    upper = (value >> 16) & 0xFFFF
    slot = (value >> 12) & 0xF
    part_id = value & 0xFFF
    if part_id > 2047:
        slot += 16
        part_id -= 2048
    return upper, slot, part_id


def texture_slot_candidates(value):
    """Every plausible (slot, part_id) reading of a textureOfset, best guess first.

    `decode_texture_offset()` applies a "part id above 2047 means slot+16, id-2048" rule
    for 32-library content. That rule is right for some models and demonstrably wrong for
    others: on a real Desert install it asks for slots 16/17/23 - i.e. Desert17/18/24 -
    when only Desert1-6 and 8 exist, blocking 56 models. Decoding the raw fields instead
    (slot = bits 12-15, id = bits 0-11) names slots that DO exist and cover their ids at
    98-100%.

    Neither reading can be declared correct in isolation - the hack was added for real
    32-library content (it took TigerE_1 from 35% to 100% resolved and TigerL from 71%),
    and its "115,613 faces round-tripped" evidence only ever proved decode and encode are
    inverses: reversibility, not correctness. So both readings are offered here and the
    caller picks whichever names a library that actually exists, which is evidence neither
    reading can supply on its own.

    Returns [(slot, part_id), ...] - the hacked reading first (preserving existing
    behaviour when both are viable), the raw reading second when it differs.
    """
    raw_slot = (value >> 12) & 0xF
    raw_id = value & 0xFFF
    out = []
    if raw_id > 2047:
        out.append((raw_slot + 16, raw_id - 2048))
    out.append((raw_slot, raw_id))
    # de-duplicate while keeping order
    seen = set()
    ordered = []
    for cand in out:
        if cand not in seen:
            seen.add(cand)
            ordered.append(cand)
    return ordered


def encode_texture_offset(upper, slot, part_id):
    """Inverse of decode_texture_offset(), including the 32-library extension: slots 16
    and above are stored as slot-16 with 2048 added to the part id."""
    if slot > 15:
        slot -= 16
        part_id += 2048
    return ((upper & 0xFFFF) << 16) | ((slot & 0xF) << 12) | (part_id & 0xFFF)


def remap_part_library(data, part_index, old_slot, new_slot, max_id=4095, lod=0):
    """Repoints every face of a part that uses `old_slot` at `new_slot`, exactly as
    ObjEdit's ReNumTLB does. Mutates `data` in place.

    Faces whose part id is greater than `max_id` are left alone and counted rather than
    remapped - the real code does the same, because the target library may simply not
    have an entry that high, and silently pointing a face at a non-existent rectangle
    would be worse than leaving it where it is.

    Returns (remapped, skipped)."""
    mesh_off = _mesh_record_offset(part_index, lod)
    faceCount, faceList_off = struct.unpack_from("<II", data, mesh_off + 4)
    remapped = skipped = 0
    for f in range(faceCount):
        off = faceList_off + f * FACE_SIZE + 12
        value, = struct.unpack_from("<I", data, off)
        if not (value & 0x80000000):
            continue
        upper, slot, part_id = decode_texture_offset(value)
        if slot != old_slot:
            continue
        if part_id > max_id:
            skipped += 1
            continue
        struct.pack_into("<I", data, off, encode_texture_offset(upper, new_slot, part_id))
        remapped += 1
    return remapped, skipped


def theatre_prefix_from_path(rrf_filepath):
    """A model's own folder names its theatre: Normandy_Obj -> "Normandy". Returns None
    when the folder is not a recognised theatre folder."""
    folder = os.path.basename(os.path.dirname(os.path.abspath(rrf_filepath)))
    key = folder.lower()
    for prefix in ("Normandy", "Italy", "Desert", "CustomA", "CustomB", "CustomC"):
        low = prefix.lower()
        if key in (low, low + "_obj") or key.startswith(low + "_"):
            return prefix
    return None


def slots_used_by(parts, available_slots=None):
    """The set of library slots a model's faces actually name.

    `available_slots`, when given, is the set of slot numbers for which a library really
    exists. A face whose primary (hacked) reading names a slot outside that set is
    re-read with the raw fields - see texture_slot_candidates(). Without it, behaviour is
    unchanged.
    """
    used = set()
    for part in parts:
        for tid in getattr(part, "face_texture_id", None) or ():
            if tid is None:
                continue
            cands = texture_slot_candidates(tid)
            if available_slots is not None:
                for slot, _pid in cands:
                    if slot in available_slots:
                        used.add(slot)
                        break
                else:
                    used.add(cands[0][0])
            else:
                used.add(cands[0][0])
    return used


def theatre_set_libraries(search_folder, parts, theatre_prefix):
    """Resolve libraries the way the GAME does: by position in the theatre's numbered set.

    A real install's Texture folder is numbered per theatre - Normandy1..6, Italy1..6,
    Desert1..8 - and the game loads that set in order, so a face's library slot is simply
    an index into it:

        slot N  ->  <Theatre>(N+1).TLB

    Preferred over id-overlap scoring, which is not merely imprecise but actively
    misleading: many libraries share ids, so a 100% score is not evidence. Measured on a
    real Normandy M4a3 (slot 1) - scoring picked Italy5.TLB at 100% and produced
    brown/white garbage, while this rule picks Normandy2.tlb at 98% and produces a correct
    olive-drab Sherman. The lower-scoring library is the right one.

    Returns ({slot: (tlb_parts, atlas_image_path, tlb_filepath)}, report_lines) - the same
    shape as assign_libraries_to_slots(), so callers can merge the two.
    """
    resolved = {}
    report = []
    if not search_folder or not theatre_prefix:
        return resolved, report
    try:
        existing = {n.lower(): os.path.join(search_folder, n)
                    for n in os.listdir(search_folder) if n.lower().endswith(".tlb")}
    except OSError:
        return resolved, report
    # Which theatre slots have a library at all? Faces whose hacked slot falls outside
    # this set are re-read raw, which is what unblocks the Desert models asking for
    # nonexistent Desert17/18/24.
    available = set()
    for idx in range(32):
        if ("%s%d.tlb" % (theatre_prefix, idx + 1)).lower() in existing:
            available.add(idx)
    for slot in sorted(slots_used_by(parts, available_slots=available)):
        wanted = "%s%d.tlb" % (theatre_prefix, slot + 1)
        # `existing` is keyed lowercase; real installs mix Normandy2.tlb / Normandy3.TLB.
        path = existing.get(wanted.lower())
        if not path:
            report.append("theatre rule: slot %d wants %s - not on disk" % (slot, wanted))
            continue
        try:
            tlb_parts = read_tlb(path)
        except Exception:
            continue
        resolved[slot] = (tlb_parts, find_atlas_image(path), path)
        report.append("theatre rule: slot %d -> %s" % (slot, os.path.basename(path)))
    return resolved, report


def assign_libraries_to_slots(search_folder, parts, name_prefix=None):
    """Works out WHICH library belongs in WHICH slot, instead of merely which libraries
    cover the most ids.

    Every face names its own library slot, so a model tells you how it is organised: group
    its faces by slot, and for each slot pick the library that best covers that slot's own
    set of part ids. Scoring libraries globally and then numbering them 0,1,2... by score -
    which is what this add-on used to do without a .RRI - produces a model that is fully
    textured with the wrong textures, because a face's slot then means nothing.

    Returns ({slot: (tlb_parts, atlas_image_path, tlb_filepath)}, report_lines)."""
    by_slot = {}
    for part in parts:
        for tid in (part.face_texture_id or []):
            if tid is None:
                continue
            _u, slot, pid = decode_texture_offset(tid)
            by_slot.setdefault(slot, set()).add(pid)
    if not by_slot:
        return {}, []

    libraries = []
    for name in sorted(os.listdir(search_folder)):
        if not name.lower().endswith(".tlb"):
            continue
        if name_prefix and not name.lower().startswith(name_prefix.lower()):
            continue
        path = os.path.join(search_folder, name)
        try:
            tlb_parts = read_tlb(path)
        except Exception:
            continue
        if tlb_parts:
            libraries.append((path, tlb_parts))
    if not libraries:
        return {}, []

    # Two passes. A slot with plenty of distinct ids identifies its library on its own;
    # a slot with only a handful does not - real models exist where 89% of the faces sit
    # in one slot that references just TWO entries, because those are large sheets and
    # each face's corner crop picks a sub-area out of them. Every library in the folder
    # contains ids that low, so the score ties and the winner is arbitrary (an Italy Tiger
    # was picking up CustomA1). Settle the confident slots first, then break ties on the
    # remaining ones in favour of the family already chosen - a model overwhelmingly draws
    # from one theatre's libraries.
    CONFIDENT_MIN_IDS = 5

    def family(path):
        """The leading alphabetic run of a library name - "CustomA1" -> "customa",
        "Italy3" -> "italy" - so libraries from the same theatre/pack group together."""
        base = os.path.splitext(os.path.basename(path))[0]
        out = []
        for ch in base:
            if ch.isalpha():
                out.append(ch)
            else:
                break
        return ("".join(out) or base).lower()

    assigned, report = {}, []
    order = sorted(by_slot, key=lambda sl: -len(by_slot[sl]))
    chosen_families = set()
    for slot in order:
        ids = by_slot[slot]
        confident = len(ids) >= CONFIDENT_MIN_IDS
        best, best_hits, best_pref = None, 0, False
        for path, tlb_parts in libraries:
            hits = sum(1 for i in ids if i in tlb_parts)
            if not hits:
                continue
            prefer = (not confident) and chosen_families and family(path) in chosen_families
            if hits > best_hits or (hits == best_hits and prefer and not best_pref):
                best, best_hits, best_pref = (path, tlb_parts), hits, prefer
        if best is None or not best_hits:
            report.append("slot %d: %d id(s), no library matched" % (slot, len(ids)))
            continue
        path, tlb_parts = best
        atlas = find_atlas_image(path)
        if atlas is None:
            report.append("slot %d: best match %s has no atlas bitmap"
                          % (slot, os.path.basename(path)))
            continue
        assigned[slot] = (tlb_parts, atlas, path)
        if len(ids) >= CONFIDENT_MIN_IDS:
            chosen_families.add(family(path))
        report.append("slot %d -> %s (%d/%d ids%s)"
                      % (slot, os.path.basename(path), best_hits, len(ids),
                         "" if len(ids) >= CONFIDENT_MIN_IDS else ", few ids - low confidence"))
    return assigned, report


def resolve_texture_id(texture_id, slot_to_parts):
    """slot_to_parts: {key: tlb_parts_dict} (key is just a label to say which library
    matched, e.g. a .RRI slot number - it doesn't need to mean anything to this function).

    Correction from an earlier version of this importer: real content routinely has a
    face's texture_id imply a "slot" number far larger than the tool's ~16-32 visible UI
    slots - confirmed up to the high hundreds on real shipped models, and it still
    resolves against an ordinary .TLB the model is known to use (verified: a Tiger
    model's turret plate, magenta under the old code, uses ids like 1181712 that turned
    out to be valid entries in the exact same CustomB1.TLB that already resolved its
    other faces - just at implied slot 288 instead of a "reasonable" 0-31). The earlier
    version capped the slot search at 32 and treated everything past that as an
    unrecoverable live hardware handle. That conclusion was wrong for these cases.

    The actual math: subtracting any multiple of TLB_MAX_PARTS (4096) from texture_id
    doesn't change its remainder, and every real .TLB entry id already lives in
    [0, TLB_MAX_PARTS) by construction (it's a fixed-size 4096-slot array). So the
    candidate id is always exactly texture_id % TLB_MAX_PARTS, regardless of how large
    the implied slot is - no need to search a slot range at all, "high slot numbers"
    were never actually a barrier, just an artifact of capping the search too low.

    There is still a real, separate, unrecoverable case: a small number of faces
    genuinely carry a live hardware texture handle from the renderer rather than any
    stable id (see TEXTURE_ID_RESOLUTION.md) - those just won't match any candidate id
    in any real .TLB, which is exactly what "returns (None, None)" from this function
    now means in practice, not "the slot was too high to search"."""
    # Two candidates, not one. rrReNumTLB() (rrobjpex.c) shows textureOfset's low 16 bits
    # as slot = (id >> 12) & 0xf and part = id & 0xfff, PLUS a 32-library extension: when
    # that part number exceeds 2047 the real slot is slot+16 and the real part is
    # part-2048. 22.8% of textured faces across a real install use that encoding, so
    # resolving only `id % 4096` misses them - TigerE_1 resolved 35% of its faces that way
    # and 100% once the extension is decoded, TigerL 71% -> 100%.
    #
    # Both forms are tried rather than switching on the >2047 test, because which one is
    # correct depends on what is actually loaded in the slot: models exist (Is2-0) whose
    # ids above 2047 are genuine part numbers, and forcing the subtraction drops them from
    # 99.2% to 94.4%. Trying both can only ever add a resolution, never remove one - all
    # five models checked reach 100%.
    candidates = [texture_id % TLB_MAX_PARTS]
    low = texture_id & 0xFFF
    if low > 2047 and (low - 2048) not in candidates:
        candidates.append(low - 2048)

    # A face names its OWN library slot (bits 12-15, plus the 32-library extension), and
    # that slot is the answer whenever a library is actually loaded there. Trying it first
    # matters: several libraries commonly contain an entry with the same part id, and
    # taking the first match in slot order hands the face whichever library happens to
    # sort earliest - which is exactly how a model comes in fully textured but with the
    # wrong textures (winter camo on a summer Tiger, red road wheels, and so on).
    _upper, face_slot, face_part = decode_texture_offset(texture_id)
    own = slot_to_parts.get(face_slot)
    if own is not None:
        entry = own.get(face_part)
        if entry is not None:
            return entry, face_slot
        for candidate in candidates:
            entry = own.get(candidate)
            if entry is not None:
                return entry, face_slot

    # Fall back to searching every loaded library. Necessary when the real slot mapping
    # isn't known (auto-detect without a .RRI) or the named slot's library is missing from
    # disk - a texture from the wrong library still beats a magenta face.
    for candidate in candidates:
        for slot in sorted(slot_to_parts):
            entry = slot_to_parts[slot].get(candidate)
            if entry is not None:
                return entry, slot
    return None, None


def find_atlas_image(tlb_filepath):
    base = os.path.splitext(tlb_filepath)[0]
    for suffix in ("_24.BMP", "_24.bmp", "_8.BMP", "_8.bmp"):
        candidate = base + suffix
        if os.path.isfile(candidate):
            return candidate
    return None


def find_source_bmp8(tlb_filepath):
    """Specifically the paletted "_8.BMP" companion, never "_24.BMP" - unlike
    find_atlas_image() (which prefers _24 for *importing*, since it's higher fidelity
    when present), exporting needs the real _8.BMP as the source of truth for its
    palette: confirmed against a real running install that the game reads _8.BMP, not
    _24.BMP, regardless of which one is present (see PAINT_AND_EXPORT_SCOPING.md)."""
    base = os.path.splitext(tlb_filepath)[0]
    for suffix in ("_8.BMP", "_8.bmp"):
        candidate = base + suffix
        if os.path.isfile(candidate):
            return candidate
    return None


def read_bmp8_palette(filepath):
    """Reads the 256-entry BGRA palette from an existing 8-bit indexed BMP (the format
    every real .TLB's own "_8.BMP" companion uses - confirmed: 40-byte
    BITMAPINFOHEADER, palette starting at the standard offset 54, 4 bytes/entry).
    Returns a list of 256 (R, G, B) tuples - the trailing byte (always 0/unused in real
    files checked) is discarded."""
    with open(filepath, "rb") as f:
        header = f.read(54)
        pal_data = f.read(256 * 4)
    bpp = struct.unpack_from("<H", header, 28)[0]
    if bpp != 8:
        raise ValueError(f"{filepath} is {bpp}-bit, not 8-bit - can't read a palette from it")
    palette = []
    for i in range(256):
        b, g, r, _ = pal_data[i * 4:i * 4 + 4]
        palette.append((r, g, b))
    return palette


def quantize_to_palette(pixels_rgb, palette):
    """pixels_rgb: (H, W, 3) uint8 array. palette: 256 (R, G, B) tuples. Returns an
    (H, W) uint8 array of nearest-palette-color indices (plain Euclidean RGB distance,
    no dithering). Repainted colors that don't already exist in the fixed 256-entry
    palette land on their closest available match - an unavoidable consequence of the
    paletted format the game actually reads, not a bug in this function. Chunked to
    avoid building one huge (H*W, 256) distance matrix in memory at once."""
    import numpy as np
    pal_arr = np.array(palette, dtype=np.int32)  # (256, 3)
    h, w, _ = pixels_rgb.shape
    flat = pixels_rgb.reshape(-1, 3).astype(np.int32)  # (H*W, 3)
    indices = np.empty(flat.shape[0], dtype=np.uint8)
    chunk = 65536
    for start in range(0, flat.shape[0], chunk):
        block = flat[start:start + chunk]  # (N, 3)
        dists = np.sum((block[:, None, :] - pal_arr[None, :, :]) ** 2, axis=2)  # (N, 256)
        indices[start:start + chunk] = np.argmin(dists, axis=1).astype(np.uint8)
    return indices.reshape(h, w)


def write_bmp8(filepath, indices, palette):
    """Writes a standard 8-bit indexed BMP - the format real "_8.BMP" atlas files
    actually use (40-byte BITMAPINFOHEADER, 256-entry BGRA palette at offset 54, pixel
    data starting at offset 1078). indices: (H, W) uint8 array. palette: 256 (R, G, B)
    tuples.

    Row order: a positive-height BMP stores rows bottom-up on disk (first row written
    = bottom of the image). Blender's own Image.pixels buffer (what indices is derived
    from via quantize_to_palette()) is *also* bottom-up - index 0 already corresponds
    to v=0, the bottom row, matching Blender's own UV convention. That means indices'
    row order already matches BMP's on-disk order directly with no reversal needed;
    reversing it here would silently flip every exported atlas upside down."""
    h, w = indices.shape
    if w % 4 != 0:
        raise ValueError(f"width {w} isn't a multiple of 4 - BMP row padding isn't handled here")
    pal_bytes = bytearray(256 * 4)
    for i, (r, g, b) in enumerate(palette):
        pal_bytes[i * 4:i * 4 + 4] = bytes((b, g, r, 0))
    data_offset = 54 + 256 * 4
    pixel_data_size = w * h  # 1 byte/pixel, no row padding since width is a multiple of 4
    file_size = data_offset + pixel_data_size

    header = bytearray(54)
    header[0:2] = b"BM"
    struct.pack_into("<I", header, 2, file_size)
    struct.pack_into("<I", header, 10, data_offset)
    struct.pack_into("<I", header, 14, 40)  # BITMAPINFOHEADER size
    struct.pack_into("<i", header, 18, w)
    struct.pack_into("<i", header, 22, h)
    struct.pack_into("<H", header, 26, 1)  # planes
    struct.pack_into("<H", header, 28, 8)  # bpp
    struct.pack_into("<I", header, 30, 0)  # BI_RGB, no compression
    struct.pack_into("<I", header, 34, pixel_data_size)
    struct.pack_into("<I", header, 46, 256)  # colors used
    struct.pack_into("<I", header, 50, 256)  # colors important

    with open(filepath, "wb") as f:
        f.write(header)
        f.write(pal_bytes)
        for row in range(h):  # no reversal - see docstring
            f.write(indices[row].tobytes())


def write_bmp24(filepath, rgb):
    """Writes a standard, uncompressed 24-bit BMP (no palette) - built by hand rather
    than via Blender's own Image.save(), so every detail (header layout, byte order,
    row padding, row direction) can be independently, explicitly verified instead of
    trusted. This matters: an earlier version of the plugin's export operator used
    Blender's generic Image.save(file_format="BMP") for this, and a real in-game test
    of that output showed no effect and broke ObjEdit's own 3D view - but genuine
    ObjEdit source (ImageLibUnit.pas) confirms _24.BMP *is* the preferred, expected
    format when present, which means that earlier negative result may have been
    testing a malformed file, not proof the format itself doesn't work. This writer
    exists to test that possibility properly instead of assuming either way.

    rgb: (H, W, 3) uint8 array, row 0 = bottom (Blender's own Image.pixels convention -
    see write_bmp8()'s docstring for why that already matches a positive-height BMP's
    on-disk bottom-up row order with no reversal needed). BMP rows must be padded to a
    4-byte boundary; handled generically here even though every real 256px-wide atlas
    in this format happens to need none (256*3=768, already a multiple of 4)."""
    h, w, _ = rgb.shape
    row_bytes_unpadded = w * 3
    row_bytes = ((row_bytes_unpadded + 3) // 4) * 4
    pad = row_bytes - row_bytes_unpadded
    data_offset = 54
    pixel_data_size = row_bytes * h
    file_size = data_offset + pixel_data_size

    header = bytearray(54)
    header[0:2] = b"BM"
    struct.pack_into("<I", header, 2, file_size)
    struct.pack_into("<I", header, 10, data_offset)
    struct.pack_into("<I", header, 14, 40)  # BITMAPINFOHEADER size
    struct.pack_into("<i", header, 18, w)
    struct.pack_into("<i", header, 22, h)
    struct.pack_into("<H", header, 26, 1)  # planes
    struct.pack_into("<H", header, 28, 24)  # bpp
    struct.pack_into("<I", header, 30, 0)  # BI_RGB, no compression
    struct.pack_into("<I", header, 34, pixel_data_size)

    padding = b"\x00" * pad
    with open(filepath, "wb") as f:
        f.write(header)
        for row in range(h):  # no reversal - see docstring
            bgr_row = rgb[row][:, ::-1].tobytes()  # RGB -> BGR, per-pixel byte order
            f.write(bgr_row)
            if pad:
                f.write(padding)


def find_best_tlb(folder, unique_texture_ids, min_ratio=0.15, min_absolute=3):
    """Scan every .TLB directly inside `folder` (not recursive) and score each by how many
    of unique_texture_ids resolve against it via resolve_texture_id(). There's no reliable
    metadata anywhere (checked the unit CSV database - it only has damage-decal filenames)
    linking a model to the library it was painted from, so this brute-force score is the
    practical substitute: unrelated libraries share a handful of common low IDs (generic
    materials like flat black/green) - noise-floor matches sit around 3-6% of a model's
    unique IDs in every case checked, genuine matches 30%+, so a ratio threshold separates
    them far more reliably than a fixed count (a fixed count of 8 wrongly rejected a real
    4-of-12 match on a small model where most of the other IDs were permanently-unrecoverable
    HAL handles, not a resolution failure - see TEXTURE_ID_RESOLUTION.md).
    Returns (best_path, best_tlb_parts, best_atlas_path, best_score) or (None, None, None, 0).
    """
    if not unique_texture_ids:
        return None, None, None, 0

    candidates = []
    try:
        entries = sorted(os.listdir(folder))
    except OSError:
        return None, None, None, 0

    for name in entries:
        if not name.lower().endswith(".tlb"):
            continue
        candidates.append(os.path.join(folder, name))

    best_path, best_parts, best_score = None, None, 0
    for path in candidates:
        try:
            tlb_parts = read_tlb(path)
        except Exception:
            continue
        single = {0: tlb_parts}
        score = 0
        for tex_id in unique_texture_ids:
            if resolve_texture_id(tex_id, single)[0] is not None:
                score += 1
        if score > best_score:
            best_score, best_path, best_parts = score, path, tlb_parts

    min_score = max(min_absolute, min_ratio * len(unique_texture_ids))
    if best_path is None or best_score < min_score:
        return None, None, None, best_score

    return best_path, best_parts, find_atlas_image(best_path), best_score


def _classify_tlb_confidence(scored, total_resolved, total_ids):
    """Classifies how much an auto-detect result should be trusted. The honest answer,
    confirmed against real content rather than assumed: auto-detect is never treated as
    "high" confidence here, no matter how clean its score looks. Two concrete findings
    forced this, not a guess:

    1. Psw232's auto-detect guess scored 96% with a real, clean gap behind it - and was
       still the wrong library once checked against a real .RRI (the true answer needed
       a completely different pair of libraries the score never flagged as relevant).
    2. Scanning real playable vehicles (Pz4h, Pz4E, TigerL, PantherG, Psw232, SPW250MG,
       M4A1, StuG3G) against both this project's install's live Texture folder and the
       fuller original 98-library set showed *every single one* has another library
       scoring within 1-2 unique ids of the top pick. This asset format's generic base
       materials (flat colors, common metal/rubber tones) overlap too pervasively
       across the whole library set for a score gap to mean anything reliable - it's
       not a signal that happens to be missing sometimes, it structurally isn't there.

    In short: a clean-looking auto-detect score is not evidence this format's real
    content supports treating as trustworthy on its own. The only reliably correct
    confirmation this project has found in practice is external - a real `.RRI` file
    (handled separately in IMPORT_OT_rrf.execute(), stamped "rri", never routed through
    this function) or actually checking in-game. Everything auto-detect touches is
    "low" here, honestly labelled rather than implying a precision the data doesn't
    support - the resolved percentage and nearest runner-up are still reported in
    `reason` for context, since that's still useful information even though it isn't
    enough to call something trustworthy.

    scored: the full (score, path, tlb_parts, resolved_ids) list, sorted best-first,
    same shape find_matching_tlbs() builds internally (already filtered to the noise
    floor). total_resolved/total_ids: how many of the model's unique ids ended up
    covered by the returned combination, out of how many exist in total.

    Returns (confidence, reason): confidence is always "low" here. reason is a short,
    honest, human-readable summary of what auto-detect actually found."""
    if total_ids == 0 or total_resolved == 0:
        return "low", "nothing resolved"

    top_score = scored[0][0]
    top_pct = 100 * top_score // total_ids
    top_name = os.path.basename(scored[0][1])
    if len(scored) > 1:
        runner_up_name = os.path.basename(scored[1][1])
        runner_pct = 100 * scored[1][0] // total_ids
        return "low", (
            f"auto-detect only ({top_pct}% resolved via '{top_name}', '{runner_up_name}' "
            f"scores {runner_pct}% too) - a clean-looking score has still been wrong in "
            f"this project's own real testing, so auto-detect alone is never treated as "
            f"high-confidence here, only a real .RRI is"
        )
    return "low", (
        f"auto-detect only ({top_pct}% resolved via '{top_name}', no other library scored "
        f"above the noise floor) - no real .RRI to confirm this against, so auto-detect "
        f"alone is never treated as high-confidence here"
    )


def find_matching_tlbs(folder, unique_texture_ids, min_ratio=0.15, min_absolute=3, name_prefix=None):
    """Like find_best_tlb(), but returns every library worth using instead of just the
    single best-scoring one - models that genuinely draw from several libraries at once
    (common on larger/older vehicles) resolve far fewer faces if only one is tried, even
    when several individually score well above the noise floor. Confirmed on a real
    Tiger1: its .RRI lists 9 real libraries and resolves 94% of faces using all of them,
    but auto-detect picking only the single best-scoring one found just 1 of the 9 and
    only reached 21% (see TODO.md) - the same model, the same folder, just needlessly
    stopping at one library where several genuinely apply.

    Scores every .TLB in the folder against the *full* unique_texture_ids set first (same
    noise-floor-vs-real-match threshold as find_best_tlb() - unrelated libraries share a
    handful of common low IDs, real matches score well above that), then greedily adds
    qualifying libraries in score order, skipping any that wouldn't resolve at least one
    id none of the already-added libraries already cover - keeps near-duplicate map
    variants (e.g. CustomA/CustomB/CustomC copies of the same content) from all being
    added redundantly just because they happen to share the same generic materials.
    Stops early once every id is covered.

    name_prefix: if given, only .TLB files whose basename starts with this (case-
    insensitive) are even considered - mirrors the real ObjEdit's own "Select Theatre"
    dialog (Desert/Italy/Normandy/Custom A/Custom B/Custom C), which asks the user this
    exact question rather than guessing. Real files bear this out: the base game's
    shared Texture folder holds cleanly prefixed libraries (Desert1-8.TLB, Italy1-6.TLB,
    Normandy1-6.TLB), with CustomA*/CustomB*/CustomC* typically added by mods. Narrowing
    to one prefix before scoring removes most of the close cross-theatre competitors that
    otherwise cause wrong guesses (confirmed repeatedly in this project's own testing -
    see TEXTURE_ID_RESOLUTION.md) - a real, substantially more reliable question to ask
    than "which of all ~26+ libraries scores best," since the user usually already knows
    which theatre a given model belongs to even when they don't know the exact filename.

    Returns (matches, confidence, reason): matches is a list of (path, tlb_parts,
    atlas_image_path, score) tuples, in the order libraries were added (best overall
    match first) - an empty list if nothing scores above the noise floor, same as
    find_best_tlb() returning (None, None, None, 0). confidence is currently always
    "low" (see _classify_tlb_confidence() - real testing found auto-detect is never
    reliably distinguishable from a wrong guess by score alone), and reason is a short,
    human-readable explanation of what auto-detect actually found. A genuinely wrong guess has repeatedly
    looked plausible at a glance during this project's own testing (Psw232, and less
    obviously Pz4E), so this exists to make that risk visible at import time instead of
    only discoverable later in-game.
    """
    if not unique_texture_ids:
        return [], "low", "no unique texture ids to match against"

    try:
        entries = sorted(os.listdir(folder))
    except OSError:
        return [], "low", "could not list the texture folder"

    if name_prefix:
        entries = [name for name in entries if name.lower().startswith(name_prefix.lower())]
        if not entries:
            return [], "low", f"no .TLB in this folder starts with '{name_prefix}'"

    candidates = [os.path.join(folder, name) for name in entries if name.lower().endswith(".tlb")]

    scored = []
    for path in candidates:
        try:
            tlb_parts = read_tlb(path)
        except Exception:
            continue
        single = {0: tlb_parts}
        resolved_ids = {
            tex_id for tex_id in unique_texture_ids if resolve_texture_id(tex_id, single)[0] is not None
        }
        if resolved_ids:
            scored.append((len(resolved_ids), path, tlb_parts, resolved_ids))

    min_score = max(min_absolute, min_ratio * len(unique_texture_ids))
    scored = [s for s in scored if s[0] >= min_score]
    scored.sort(key=lambda s: s[0], reverse=True)

    result = []
    still_unresolved = set(unique_texture_ids)
    for score, path, tlb_parts, resolved_ids in scored:
        if not still_unresolved:
            break
        newly_covered = resolved_ids & still_unresolved
        if not newly_covered:
            continue  # everything this library resolves is already covered by a better-scoring one
        result.append((path, tlb_parts, find_atlas_image(path), score))
        still_unresolved -= newly_covered

    total_resolved = len(unique_texture_ids) - len(still_unresolved)
    confidence, reason = _classify_tlb_confidence(scored, total_resolved, len(unique_texture_ids))

    return result, confidence, reason


# .RRI layout, taken from ObjEdit's own save code (OEMainUnit.pas SaveObject1Click) and
# confirmed byte-for-byte against real files. Six blocks, in this order:
#   1. library name slots   - RRI_LIB_SLOTS x 128 bytes, null-padded ASCII
#   2. groupNameList        - RRI_GROUPS x 80 bytes
#   3. selGroupArray        - RRI_GROUPS x RRI_SEL_PER_GROUP x 16 (4 x int32 TSelectInfo)
#   4. selGroupArrayCount   - RRI_GROUPS x int32
#   5. matList              - 32 x (80-byte name + int32 info + int32 col)
#   6. attribList           - 32 x (80-byte name + int32 info + int32 value)
#
# Three variants ship in real installs, distinguishable purely by file size - the whole
# arithmetic below was checked against every .RRI on a real install and accounts for
# every byte:
#   214,144 =  8 libs, 32 groups,  400 sel/group   (oldest)
#   267,040 = 16 libs, 40 groups,  400 sel/group
#   668,448 = 32 libs, 40 groups, 1024 sel/group   (current ObjEdit build - what we write)
# Reading the wrong variant's slot count is not harmless: in the 8- and 16-slot files the
# bytes immediately after the libraries are GROUP NAMES, which a 32-slot read happily
# returns as if they were library paths (e.g. "PantherGa", "Name 16").
RRI_LIB_SLOTS = 32
RRI_GROUPS = 40
RRI_SEL_PER_GROUP = 1024
RRI_VARIANTS = {
    214144: (8, 32, 400),
    267040: (16, 40, 400),
    668448: (32, 40, 1024),
}

# Default block contents, read out of a real ObjEdit-written .RRI. The material `info`
# values are the same shading/texture-mode bits that appear in a face's materialInfo.
RRI_DEFAULT_MATERIALS = [
    ("** No Shading **", 0), ("No Single Texture", 8), ("No Two Texture", 40),
    ("No Single Mask Testure", 12), ("No Two Mask Texture", 44), (" ", 40),
    ("** Flat Shading **", 0), ("Flat Single Texture", 9), ("Flat Two Texture", 41),
    ("Flat Single Mask Texture", 13), ("Flat Two Mask Texture", 45), (" ", 0),
    ("** Phong Shading **", 0), ("Phong Single Texture", 10), ("Phong Two Texture", 42),
    ("Phong Single Mask Texture", 14), ("Phong Two Mask Texture", 46), (" ", 0),
    ("** Wireframe **", 0), ("Wireframe Single", 3), ("Wireframe Two Sided", 35), (" ", 0),
] + [("Material %d" % i, 0) for i in range(22, 32)]


def rri_variant_for_size(size):
    """Returns (lib_slots, groups, sel_per_group) for a real .RRI file size, or None if
    the size is not one of the three known variants."""
    return RRI_VARIANTS.get(size)


def write_rri(filepath, slots, lib_slots=None, groups=None, sel_per_group=None):
    """Writes a .RRI sidecar naming which .TLB is loaded in each library slot.

    `slots` is {slot_index: "texture\\Whatever.TLB"} - the same shape read_rri()
    returns. Everything else is written as ObjEdit's own defaults: group names
    "Not Used N", an all-zero selection array and counts, the stock material table, and
    "Attribut N" entries. Verified byte-identical to a real ObjEdit-written file when
    given that file's own slots.

    Defaults to the current build's 32-slot/668,448-byte variant, which is what a modern
    ObjEdit writes and expects. Why this matters: without a matching .RRI, ObjEdit warns
    "No RRI file found, No auto load of textures!" and loads the model with no textures
    at all - and it derives the path by swapping the .RRF name's last character for "I"
    (convNameRRF_To_RRI), so the file has to sit beside the .RRF under the same stem."""
    lib_slots = RRI_LIB_SLOTS if lib_slots is None else lib_slots
    groups = RRI_GROUPS if groups is None else groups
    sel_per_group = RRI_SEL_PER_GROUP if sel_per_group is None else sel_per_group

    out = bytearray()
    for i in range(lib_slots):
        name = slots.get(i, "")
        raw = name.encode("latin-1", "replace")[:127]
        out += raw + bytes(128 - len(raw))
    for g in range(groups):
        raw = ("Not Used %d" % g).encode("latin-1")[:79]
        out += raw + bytes(80 - len(raw))
    out += bytes(groups * sel_per_group * 16)   # selGroupArray - all zero in real files
    out += bytes(groups * 4)                    # selGroupArrayCount
    for name, info in RRI_DEFAULT_MATERIALS[:32]:
        raw = name.encode("latin-1")[:79]
        out += raw + bytes(80 - len(raw)) + struct.pack("<ii", info, 0)
    for a in range(32):
        raw = ("Attribut %d" % a).encode("latin-1")[:79]
        out += raw + bytes(80 - len(raw)) + struct.pack("<ii", 0, 0)

    with open(filepath, "wb") as f:
        f.write(bytes(out))
    return len(out)


def rri_path_for_rrf(rrf_filepath):
    """The .RRI path ObjEdit itself would use: the .RRF path with its final character
    replaced by "I" (convNameRRF_To_RRI in OEMainUnit.pas). Deliberately mirrors that
    rather than doing a tidier extension swap, so a file written here is found by the
    real tool."""
    return rrf_filepath[:-1] + ("I" if rrf_filepath[-1].isupper() else "i")


def read_rri(filepath):
    """Parses the .RRI sidecar ObjEdit writes next to a .RRF with the same stem, naming
    the .TLB loaded into each library slot when the model was painted. This is the
    authoritative slot->library mapping (confirmed against a real model: the slots here
    matched exactly what a live paint-and-save in the real ObjEdit produced).

    The number of library slots depends on which ObjEdit build wrote the file and is
    detected from its size (see RRI_VARIANTS). An earlier version of this function always
    read 16 slots and documented slots 16-31 as "not covered by this file format" - both
    wrong: the current build stores 32, and the older 8-slot files put GROUP NAMES
    directly after the libraries, so a fixed 16-slot read returns things like "Spw250sMG"
    and "Name 8" as if they were library paths.

    Returns {slot_index: relative_path_string} for the non-empty slots.
    """
    size = os.path.getsize(filepath)
    variant = rri_variant_for_size(size)
    if variant is None:
        # Unknown build - fall back to the most conservative slot count and only accept
        # entries that actually look like library paths, rather than guessing wider.
        lib_slots, strict = 8, True
    else:
        lib_slots, _groups, _sel = variant
        strict = False

    with open(filepath, "rb") as f:
        data = f.read(lib_slots * 128)

    slots = {}
    for slot in range(lib_slots):
        off = slot * 128
        raw = data[off:off + 128].split(b"\x00", 1)[0]
        text = raw.decode("latin-1", errors="replace").strip()
        if not text:
            continue
        if strict and ".tlb" not in text.lower():
            continue
        slots[slot] = text
    return slots


def find_rri_path(rrf_filepath, texture_folder=None):
    """Checks next to the .RRF first (the documented convention), then - if given - the
    shared Texture folder too. That second check matters: a real, genuine .RRI can
    exist there instead (confirmed on PantherG.RRI, sitting directly in Texture\\ with
    no matching .RRF alongside it) - checking only the .RRF's own directory silently
    missed a real answer that was sitting in plain sight, so the caller fell through to
    auto-detect and got a real answer wrong that a file already on disk would have
    given for free."""
    base = os.path.splitext(rrf_filepath)[0]
    for suffix in (".RRI", ".rri", ".RRi", ".rRI"):
        candidate = base + suffix
        if os.path.isfile(candidate):
            return candidate

    if texture_folder:
        rrf_base = os.path.splitext(os.path.basename(rrf_filepath))[0]
        texture_base = os.path.join(texture_folder, rrf_base)
        for suffix in (".RRI", ".rri", ".RRi", ".rRI"):
            candidate = texture_base + suffix
            if os.path.isfile(candidate):
                return candidate

    return None


def resolve_rri_libraries(rri_slots, rrf_filepath):
    """rri_slots' paths (e.g. "texture\\CustomB1.TLB") are relative to the pack's install
    root, and the .RRF itself lives at <root>\\<PackFolder>\\Model.RRF, so the natural root
    is the .RRF's own parent directory. Falls back to the .RRF's own directory in case the
    pack layout differs. Returns {slot_index: (tlb_parts, atlas_image_path, tlb_filepath)}
    for whichever slots actually resolve to a real file on disk - slots that don't
    (moved/renamed/missing library) are silently dropped rather than failing the whole
    import.
    """
    rrf_dir = os.path.dirname(os.path.abspath(rrf_filepath))
    candidate_roots = [os.path.dirname(rrf_dir), rrf_dir]

    resolved = {}
    for slot, rel_path in rri_slots.items():
        rel_path_native = rel_path.replace("\\", os.sep).replace("/", os.sep)
        for root in candidate_roots:
            abs_path = os.path.join(root, rel_path_native)
            if os.path.isfile(abs_path):
                try:
                    tlb_parts = read_tlb(abs_path)
                except Exception:
                    continue
                resolved[slot] = (tlb_parts, find_atlas_image(abs_path), abs_path)
                break
    return resolved


def default_texture_folder(rrf_filepath):
    """Same pack-layout assumption as resolve_rri_libraries(): the .RRF lives at
    <root>\\<PackFolder>\\Model.RRF, with a shared "Texture" folder as a sibling of
    PackFolder. Used to auto-run the folder-scan fallback with no user input needed,
    for models without a .RRI - so File > Import can "just work" generically for any
    model in this kind of layout, not only ones a user happens to type a path for."""
    rrf_dir = os.path.dirname(os.path.abspath(rrf_filepath))
    for candidate_root in (os.path.dirname(rrf_dir), rrf_dir):
        candidate = os.path.join(candidate_root, "Texture")
        if os.path.isdir(candidate):
            return candidate
    return None


PACK_FOLDERS = ("CustomA", "CustomB", "CustomC", "Desert_Obj", "Italy_Obj", "Normandy_Obj")


def find_sibling_variant_rrfs(rrf_filepath):
    """Finds same-named .RRF copies in the other known theatre "PackFolder" siblings
    under the same install root (the CustomA/CustomB/CustomC/Desert_Obj/Italy_Obj/
    Normandy_Obj layout every real asset checked in this project uses). These copies
    can genuinely differ (see TODO.md) - this exists purely so a candidate library's
    resolution rate can be cross-checked against every real copy of "the same"
    vehicle, not just the one being imported right now. Returns a list of absolute
    paths, excluding rrf_filepath itself - empty if none exist or the layout doesn't
    match this pattern."""
    rrf_dir = os.path.dirname(os.path.abspath(rrf_filepath))
    install_root = os.path.dirname(rrf_dir)
    basename = os.path.basename(rrf_filepath)

    siblings = []
    for pack_folder in PACK_FOLDERS:
        candidate_dir = os.path.join(install_root, pack_folder)
        if os.path.normcase(os.path.abspath(candidate_dir)) == os.path.normcase(rrf_dir):
            continue
        try:
            names = os.listdir(candidate_dir)
        except OSError:
            continue
        for name in names:
            if name.lower() == basename.lower():
                siblings.append(os.path.join(candidate_dir, name))
                break
    return siblings


def cross_check_tlb_across_variants(rrf_filepath, tlb_filepath):
    """Diagnostic only - doesn't change which library gets picked, just reports how
    consistently a chosen candidate resolves each sibling theatre-variant copy of the
    same-named .RRF (see find_sibling_variant_rrfs()). Confirmed useful by hand:
    PantherG/CustomA9 resolved 79-100% across three real copies (trustworthy); TigerL
    against its own best-guess library ranged 19%-95% across copies (a real,
    immediately visible red flag that trial-and-error in-game testing had to find the
    hard way instead).

    Returns a list of (rrf_path, resolved_count, total_ids) tuples, one per sibling
    found - empty if none exist or the candidate .TLB can't be read."""
    siblings = find_sibling_variant_rrfs(rrf_filepath)
    if not siblings:
        return []

    try:
        tlb_parts = read_tlb(tlb_filepath)
    except Exception:
        return []
    single = {0: tlb_parts}

    results = []
    for sibling_path in siblings:
        try:
            sibling_parts = read_rrf(sibling_path)
        except Exception:
            continue
        ids = sorted({t for part in sibling_parts for t in part.face_texture_id if t is not None})
        if not ids:
            continue
        resolved = sum(1 for tex_id in ids if resolve_texture_id(tex_id, single)[0] is not None)
        results.append((sibling_path, resolved, len(ids)))
    return results


def _read_mesh_lod0(data, mesh_off):
    (meshType, faceCount, faceList_off, faceNormList_off,
     vertexCount, vertexList_off, vertexNormList_off,
     sortList_off, attribVList_off) = struct.unpack_from("<IIIIIIIII", data, mesh_off)

    vertices = []
    for i in range(vertexCount):
        off = vertexList_off + i * VERTEX_SIZE
        x, y, z = struct.unpack_from("<iii", data, off)
        vertices.append((fixed_to_float(x), fixed_to_float(y), fixed_to_float(z)))

    faces = []
    face_texture_id = []
    face_uv_corners = []
    face_crop_size = []
    for i in range(faceCount):
        off = faceList_off + i * FACE_SIZE
        v1, v2, v3, textureOfset, textureHalf, materialInfo = struct.unpack_from("<IIIIII", data, off)
        is_quad = bool(materialInfo & MAT_QUAD)

        if is_quad:
            faces.append((v1 & 0xFFFF, v2 & 0xFFFF, v3 & 0xFFFF, textureHalf & 0xFFFF))
        else:
            faces.append((v1 & 0xFFFF, v2 & 0xFFFF, v3 & 0xFFFF))

        # Textured faces reference a shared .TLB library entry by ID when the top bit of
        # textureOfset is set (confirmed empirically against real shipped .RRF/.TLB pairs).
        # Deep-shaded faces (MAT_SHADING_DEEP) reuse textureOfset as a packed solid color
        # instead (see object.c rrObjOfsetToHiColor) so they're excluded here.
        textured = (
            (textureOfset & 0x80000000)
            and (materialInfo & MAT_TEXTRUE_MASK)
            and ((materialInfo & MAT_SHADING_MASK) != MAT_SHADING_DEEP)
        )
        if textured:
            face_texture_id.append(textureOfset & 0x7FFFFFFF)
            # Corner roles confirmed from Rrdwire.c rrSetTexture: v1=top-right, v2=top-left,
            # v3=bottom-left, textureHalf(quads only)=bottom-right.
            corners = [_corner_xy(v1), _corner_xy(v2), _corner_xy(v3)]
            if is_quad:
                corners.append(_corner_xy(textureHalf))
            face_uv_corners.append(tuple(corners))
            # The real crop size a face actually uses within its assigned .TLB entry -
            # confirmed real (2026-07-08) against a live ObjEdit comparison on 88Pak43.RRF/
            # Normandy1.tlb entry 160: ObjEdit showed this face using a 32x16 crop, not the
            # entry's own full 32x32 allocation - materialInfo bits 8-11/12-15 give that
            # 32x16 size directly ((nibble+1)*16 per axis), also confirmed sensible across
            # every distinct materialInfo value on that same part (always <= the entry's
            # own allocated size, in clean 16px multiples - consistent with one entry often
            # being shared by several faces, each using its own smaller sub-tile of it).
            # Also confirmed against the real engine source (Rrdwire.c rrUsedSelection()):
            # FSizeX=(((materialInfo&0xf00)>>8)+1)*16, FSizeY=(((materialInfo&0xf000)>>12)+1)*16.
            crop_size_x = (((materialInfo & 0x0F00) >> 8) + 1) * 16
            crop_size_y = (((materialInfo & 0xF000) >> 12) + 1) * 16
            # ...and where inside the entry that crop STARTS, in 16px units.
            #
            # This used to read bits 16-23, citing rrUsedSelection()'s all-zero branch
            #     StartX = ((TexInfo>>20)&0xf)*16;  StartY = ((TexInfo>>16)&0xf)*16;
            # but that conflates two different packings. TexInfo there is the SELECTION
            # QUERY word built by ObjEdit's UI, not the face's stored textureOfset. The
            # stored field is written by rrSetTextureSelection() as
            #     texture = rrTextLibPartIDHALStarts[i] | (orgY<<28) | (orgX<<24)
            # and read back by rrGetSelection() as
            #     xOfset = (textureOfset>>24)&0xf;  yOfset = (textureOfset>>28)&0xf
            # i.e. bits 24-27 and 28-31 - confirmed live against the real engine, by
            # calling rrGetMaterialSelection() through rrobjx5.dll on a loaded model and
            # matching its answer to the file bytes (tools/headless_oracle).
            #
            # Bit 31 is the "is textured" flag, so only bits 28-30 carry Y - the engine's
            # own >>28 & 0xf swallows the flag and reports a bogus yOfset of 8 on every
            # textured face, which is harmless there but must not be copied here.
            #
            # Measured on models with a real .RRI (10,614 all-zero-corner faces): the old
            # bits-16-23 reading invented a non-zero origin on 51.3% of faces and put the
            # crop inside its own TLB entry only 74.7% of the time; this reading fits
            # 99.9%. Bits 16-23 are in fact high bits of the texture id, not an origin.
            crop_start_x = ((textureOfset >> 24) & 0xF) * 16
            crop_start_y = ((textureOfset >> 28) & 0x7) * 16
            # textureOfset bits 16-31 also carry a per-face value (spread 0..24 on real
            # models, not a 2-bit flag). rrSetTextureSelection() has a commented-out
            # original that reads it as a LINEAR TILE INDEX into the entry:
            #     ofset  = (orgTexture>>16)&0xffff;
            #     xOfset = ofset/(rrTextLibPartSizeX[i]>>4);
            #     yOfset = ofset%(rrTextLibPartSizeX[i]>>4);
            # TESTED AND DISPROVED (2026-08-13): decoding it that way, in either axis
            # order, is far worse than leaving the origin at (0,0) - road wheels and hull
            # markings vanish entirely. The engine comments it out as "error with chris
            # tracks", so the shipped renderer does not use it either. Parsed and carried
            # here so the value is available, but deliberately NOT applied.
            crop_tile_index = (textureOfset >> 16) & 0xFFFF
            face_crop_size.append((crop_size_x, crop_size_y, crop_start_x, crop_start_y, crop_tile_index))
        else:
            face_texture_id.append(None)
            face_uv_corners.append(None)
            face_crop_size.append(None)

    return vertices, faces, face_texture_id, face_uv_corners, face_crop_size


def read_rrf(filepath):
    with open(filepath, "rb") as f:
        data = f.read()

    maxLOD, transInfo, objCount, maxAllVertex, textureStart, textureLen = struct.unpack_from(
        "<HHIIII", data, 0
    )

    expected_size = textureStart + textureLen
    if expected_size != len(data):
        raise ValueError(
            f"'{os.path.basename(filepath)}' does not look like a valid .RRF file: "
            f"header expects {expected_size} bytes, file is {len(data)} bytes."
        )

    parts = []
    for p in range(objCount):
        off = HEADER_SIZE + p * PART_SIZE

        raw_name = data[off:off + 12].split(b"\x00")[0]
        name = raw_name.decode("latin-1", errors="replace") or f"part{p}"

        pivotX, pivotY, pivotZ = struct.unpack_from("<iii", data, off + 12)
        objAttribut, maxVertex, parentNo, childCount = struct.unpack_from("<IIII", data, off + 80)
        childArray = struct.unpack_from("<32I", data, off + 96)

        vertices, faces, face_texture_id, face_uv_corners, face_crop_size = _read_mesh_lod0(data, off + 224)

        part = RRFPart()
        part.index = p
        part.name = name
        part.pivot = (fixed_to_float(pivotX), fixed_to_float(pivotY), fixed_to_float(pivotZ))
        part.obj_attribut = objAttribut
        part.parent_no = parentNo if parentNo != 0xFFFFFFFF else None
        part.child_count = childCount
        part.child_array = childArray[:childCount]
        part.vertices = vertices
        part.faces = faces
        part.face_texture_id = face_texture_id
        part.face_uv_corners = face_uv_corners
        part.face_crop_size = face_crop_size
        parts.append(part)

    return parts


def read_rrf_raw(filepath):
    """Raw file bytes, for use with the surgical-patch functions below - not a full
    editable in-memory reconstruction the way read_rrf() gives for import.

    Unlike .TLB (a simple fixed-size array - see write_tlb_library()), .RRF's mesh/LOD
    data is a web of absolute in-file offsets, and several pieces of it (sortList,
    attribVList, LOD levels above 0, the embedded placeholder texture block) aren't
    understood well enough yet to safely reconstruct a whole file from scratch without
    real risk of silently corrupting something. Patching known fields directly in an
    exact copy of the original file sidesteps that entirely: everything not explicitly
    touched is guaranteed byte-identical, with no need to understand or rebuild the rest
    of the format first. A full "rebuild an arbitrary model from scratch" .RRF writer
    would be a separate, bigger undertaking - this covers targeted edits to an existing,
    already-valid file.
    """
    with open(filepath, "rb") as f:
        return bytearray(f.read())


def write_rrf_raw(filepath, data):
    with open(filepath, "wb") as f:
        f.write(data)


def _mesh_record_offset(part_index, lod):
    return HEADER_SIZE + part_index * PART_SIZE + 224 + lod * MESH_SIZE


def _face_record_offset(data, part_index, lod, face_index):
    """Locates one face record's absolute byte offset in a raw .RRF buffer - re-reads the
    mesh record's own faceCount/faceList fields directly from the file every time (never
    assumed or cached from a prior read_rrf() call), so this stays correct even if data
    has already been patched by an earlier call in the same session."""
    mesh_off = _mesh_record_offset(part_index, lod)
    faceCount, faceList_off = struct.unpack_from("<II", data, mesh_off + 4)
    if not (0 <= face_index < faceCount):
        raise IndexError(
            f"face_index {face_index} out of range (faceCount={faceCount}) "
            f"for part {part_index} LOD {lod}"
        )
    return faceList_off + face_index * FACE_SIZE


def read_face_texture_id(data, part_index, lod, face_index):
    """Reads a face's resolved texture id straight from a raw buffer, the same way
    _read_mesh_lod0() does - used to verify patch_face_texture_id() actually took effect,
    not used by the importer itself (which works from read_rrf()'s parsed RRFPart data)."""
    off = _face_record_offset(data, part_index, lod, face_index)
    textureOfset, = struct.unpack_from("<I", data, off + 12)
    return textureOfset & 0x7FFFFFFF


def read_face_record(data, part_index, lod, face_index):
    """Returns one face's raw 24 bytes, for carrying forward through a rebuild - see
    repack_existing_face_record()."""
    off = _face_record_offset(data, part_index, lod, face_index)
    return bytes(data[off:off + FACE_SIZE])


def read_stored_normal(data, list_offset, index):
    """Reads one 16.16 unit-vector normal (rrVertex: 3 x int32) out of a
    faceNormList/vertexNormList at its raw file offset."""
    x, y, z = struct.unpack_from("<iii", data, list_offset + index * 12)
    return (x / 65536.0, y / 65536.0, z / 65536.0)


def read_face_material_info(data, part_index, lod, face_index):
    """Reads one face's raw materialInfo word. Needed whenever a part's mesh region is
    rebuilt, since that repacks every face and would otherwise drop the real value -
    see _pack_face_record()'s own note for what is encoded in it."""
    off = _face_record_offset(data, part_index, lod, face_index)
    return struct.unpack_from("<I", data, off + 20)[0]


def patch_face_texture_id(data, part_index, lod, face_index, new_texture_id):
    """Overwrites one face's textureOfset field in place (RRF_FORMAT.md) to point at a
    different .TLB entry id - the top bit stays set (marking it as a library-entry
    reference, the same convention _read_mesh_lod0() checks) with the new 31-bit id below
    it. This is the whole "repoint a face at a new/different texture entry" operation the
    "detach face from shared cell" feature (see TODO.md) needs on the .RRF side, paired
    with append_tlb_entry() on the .TLB side.

    Leaves every other byte in the file untouched - including this exact face's own
    v1/v2/v3/textureHalf UV corner bytes, which stay valid unchanged as long as the new
    .TLB entry has the same crop size as the old one, since those corners are pixel
    offsets *within* whichever entry is assigned, not absolute atlas coordinates."""
    if not (0 <= new_texture_id < 0x80000000):
        raise ValueError(f"texture id {new_texture_id} doesn't fit in textureOfset's 31 usable bits")
    off = _face_record_offset(data, part_index, lod, face_index)
    struct.pack_into("<I", data, off + 12, 0x80000000 | new_texture_id)


def read_face_corners(data, part_index, lod, face_index):
    """Reads a face's current v1/v2/v3/textureHalf UV corner bytes straight from a raw
    buffer, via the same fields and decode as _corner_xy() - used to verify
    patch_face_corners() actually took effect. Returns (v1_xy, v2_xy, v3_xy) for a
    triangle, or (v1_xy, v2_xy, v3_xy, textureHalf_xy) for a quad."""
    off = _face_record_offset(data, part_index, lod, face_index)
    v1, v2, v3, _textureOfset, textureHalf, materialInfo = struct.unpack_from("<IIIIII", data, off)
    corners = [_corner_xy(v1), _corner_xy(v2), _corner_xy(v3)]
    if materialInfo & MAT_QUAD:
        corners.append(_corner_xy(textureHalf))
    return tuple(corners)


def patch_face_corners(data, part_index, lod, face_index, min_x, min_y, max_x, max_y):
    """Overwrites one face's v1/v2/v3/textureHalf UV corner bytes in place to crop a
    specific (min_x,min_y)-(max_x,max_y) rectangle - pixel offsets within whichever .TLB
    entry the face is assigned to, each 0-255 (RRF_FORMAT.md's per-face crop cap) -
    instead of the "no crop data, use the entry's full rectangle" all-zero fallback every
    prior writer in this project used (see PAINT_AND_EXPORT_SCOPING.md Scenario B). This
    is the "corners from real UV coordinates" piece that was missing.

    Corner-to-field assignment confirmed two independent ways: this project's own
    read-side (_corner_xy(), itself sourced from the real game's Rrdwire.c
    rrSetTexture), and separately from real community source - Aldo/Brit44's own
    RRF-writing code, shared on the private PEDG forum (2026-07-07, "UV stile artwork"
    thread) - which packs corners in exactly this same pattern:
    v1=top-right (max_x,min_y), v2=top-left (min_x,min_y), v3=bottom-left (min_x,max_y),
    textureHalf=bottom-right (max_x,max_y). textureHalf only exists/is used for quads
    (MAT_QUAD set in materialInfo); triangles only ever use v1/v2/v3.

    Only rewrites the upper 16 bits of each vertex field (the packed corner bytes) - the
    lower 16 bits (the actual mesh vertex index) are read back and preserved unchanged,
    the same way patch_face_texture_id() preserves textureOfset's own unrelated bits."""
    if not all(0 <= v <= 255 for v in (min_x, min_y, max_x, max_y)):
        raise ValueError(f"corner values must fit in a byte (0-255): got {(min_x, min_y, max_x, max_y)}")
    off = _face_record_offset(data, part_index, lod, face_index)
    v1, v2, v3, _textureOfset, textureHalf, materialInfo = struct.unpack_from("<IIIIII", data, off)
    is_quad = bool(materialInfo & MAT_QUAD)

    def _pack(field, x, y):
        return (field & 0xFFFF) | (y << 24) | (x << 16)

    # ORIGIN + SIZE, not four positions. rrSetTexture() (Rrdwire.c) writes
    #   xStart = X-1 (when X != 0), xSize = sx-1
    #   v1 = (yStart<<24)|(xSize <<16)      v2 = (yStart<<24)|(xStart<<16)
    #   v3 = (ySize <<24)|(xStart<<16)      textureHalf = (ySize<<24)|(xSize<<16)
    # and rrUsedSelection() reads it straight back that way. Packing the right edge
    # (max_x) into v1 - which this function used to do - hands the engine a coordinate
    # where it expects a width, so a 50px face at x=200 claims a 200px crop and smears.
    # The two only coincide when a face starts at x=0, which is why it sometimes looked
    # right.
    x_start = max_x_local = 0
    x_start = (min_x - 1) if min_x else 0
    y_start = (min_y - 1) if min_y else 0
    x_size = (max_x - min_x + 1)
    y_size = (max_y - min_y + 1)
    x_size = (x_size - 1) if x_size else 0
    y_size = (y_size - 1) if y_size else 0

    struct.pack_into("<I", data, off + 0, _pack(v1, x_size, y_start))
    struct.pack_into("<I", data, off + 4, _pack(v2, x_start, y_start))
    struct.pack_into("<I", data, off + 8, _pack(v3, x_start, y_size))
    if is_quad:
        struct.pack_into("<I", data, off + 16, _pack(textureHalf, x_size, y_size))


def patch_face_corners_per_vertex(data, part_index, lod, face_index, v1_xy, v2_xy, v3_xy, texture_half_xy=None):
    """Writes each of a face's vertices its own independent (x,y) UV pixel offset,
    instead of patch_face_corners()'s single shared rectangle collapsed into a fixed
    v1=top-right/v2=top-left/v3=bottom-left/textureHalf=bottom-right pattern.

    Reconsidered 2026-07-08: that fixed pattern was only ever confirmed from one
    community writer's own code (Aldo/Brit44's, for the one case of assigning a plain
    axis-aligned rectangle) - not from the real engine itself. This project's own
    read-side, _corner_xy() (sourced from the real engine's Rrdwire.c), documents the
    field only as "a UV pixel offset within the assigned texture part" - a generic
    per-vertex coordinate with no named role. Forcing every face into the fixed-pattern
    rectangle (as apply_private_skin() did before this) measurably improved on preserving
    Smart UV Project's raw organic shape but still didn't fully resolve real ObjEdit
    rendering - consistent with the fixed-pattern collapse being the wrong model, not
    just an incomplete one. This function instead trusts each vertex's own real position
    directly, so two faces sharing a real mesh edge (e.g. a flat panel split into two
    triangles) naturally agree at that edge without needing any special-case pairing
    logic - they simply both get the shared vertex's one real value.

    texture_half_xy is written only if given (quads only, matching
    patch_face_corners()'s own quad-only convention for that field). Preserves the low
    16 bits (mesh vertex index) of each field unchanged, same as patch_face_corners()."""
    corners = [v1_xy, v2_xy, v3_xy] + ([texture_half_xy] if texture_half_xy is not None else [])
    if not all(0 <= v <= 255 for xy in corners for v in xy):
        raise ValueError(f"corner values must fit in a byte (0-255): got {corners}")
    off = _face_record_offset(data, part_index, lod, face_index)
    v1, v2, v3, _textureOfset, textureHalf, materialInfo = struct.unpack_from("<IIIIII", data, off)

    def _pack(field, xy):
        x, y = xy
        return (field & 0xFFFF) | (y << 24) | (x << 16)

    struct.pack_into("<I", data, off + 0, _pack(v1, v1_xy))
    struct.pack_into("<I", data, off + 4, _pack(v2, v2_xy))
    struct.pack_into("<I", data, off + 8, _pack(v3, v3_xy))
    if texture_half_xy is not None:
        struct.pack_into("<I", data, off + 16, _pack(textureHalf, texture_half_xy))


def flip_face_texture_orientation(data, part_index, lod, face_index):
    """Replicates the real engine's own "Flip" tool exactly (Rrdwire.c
    rrFlipObjFace()) - the mechanism behind the user-observed "right texture, wrong
    rotation" problem on faces the original artist flipped. Confirmed from source: for
    a quad, it swaps which VERTEX occupies the v2 vs textureHalf(v4) field - each
    field's own UV-corner bytes (the upper 16 bits) stay exactly where they are, only
    the vertex-index (lower 16 bits) moves between the two fields; for a triangle, it's
    v2/v3 instead. It also negates the face's stored normal at the same time (confirmed
    same function, same real source) - replicated here too, so the file stays exactly
    as internally consistent as any other real, tool-saved .RRF (see
    TEXTURE_ID_RESOLUTION.md's finding that a stored normal always matches whatever
    vertex order is currently in the file, flipped or not - there's no separate,
    independently-readable "was this flipped" flag anywhere, this operation is the only
    way to toggle it, both in the real tool and here)."""
    off = _face_record_offset(data, part_index, lod, face_index)
    v1, v2, v3, textureOfset, textureHalf, materialInfo = struct.unpack_from("<IIIIII", data, off)
    is_quad = bool(materialInfo & MAT_QUAD)

    if is_quad:
        v2_idx = v2 & 0xFFFF
        v4_idx = textureHalf & 0xFFFF
        struct.pack_into("<I", data, off + 4, (v2 & 0xFFFF0000) | v4_idx)
        struct.pack_into("<I", data, off + 16, (textureHalf & 0xFFFF0000) | v2_idx)
    else:
        v2_idx = v2 & 0xFFFF
        v3_idx = v3 & 0xFFFF
        struct.pack_into("<I", data, off + 4, (v2 & 0xFFFF0000) | v3_idx)
        struct.pack_into("<I", data, off + 8, (v3 & 0xFFFF0000) | v2_idx)

    mesh_off = _mesh_record_offset(part_index, lod)
    faceNormList_off, = struct.unpack_from("<I", data, mesh_off + 12)
    norm_off = faceNormList_off + face_index * 12
    nx, ny, nz = struct.unpack_from("<iii", data, norm_off)
    struct.pack_into("<iii", data, norm_off, -nx, -ny, -nz)


def _vertex_record_offset(data, part_index, lod, vertex_index):
    """Locates one vertex record's absolute byte offset in a raw .RRF buffer - re-reads
    the mesh record's own vertexCount/vertexList fields directly from the file every
    time (never assumed or cached from a prior read_rrf() call), the same way
    _face_record_offset() does, so this stays correct even if data has already been
    patched by an earlier call in the same session."""
    mesh_off = _mesh_record_offset(part_index, lod)
    vertexCount, vertexList_off = struct.unpack_from("<II", data, mesh_off + 16)
    if not (0 <= vertex_index < vertexCount):
        raise IndexError(
            f"vertex_index {vertex_index} out of range (vertexCount={vertexCount}) "
            f"for part {part_index} LOD {lod}"
        )
    return vertexList_off + vertex_index * VERTEX_SIZE


def read_vertex_position(data, part_index, lod, vertex_index):
    """Reads one vertex's raw (x, y, z) position straight from a raw buffer, the same way
    _read_mesh_lod0() does - used to verify patch_vertex_position() actually took effect,
    not used by the importer itself (which works from read_rrf()'s parsed RRFPart data)."""
    off = _vertex_record_offset(data, part_index, lod, vertex_index)
    x, y, z = struct.unpack_from("<iii", data, off)
    return (fixed_to_float(x), fixed_to_float(y), fixed_to_float(z))


def patch_vertex_position(data, part_index, lod, vertex_index, x, y, z):
    """Overwrites one vertex's raw position in place - Phase 1 of the .RRF geometry
    writer (see docs/RRF_WRITER_SCOPING.md): repositioning existing vertices without
    adding or removing any vertex, face, or part, so face count, vertex count, and every
    absolute offset elsewhere in the file stay exactly as they already are. Deliberately
    does not touch faceList/sortList/attribVList/any other part's data - the same
    "surgical patch on an exact copy" approach as patch_face_texture_id()/
    patch_face_corners(), just applied to the vertex-position field instead.

    x/y/z must already be in the file's own raw coordinate convention (world = raw vertex
    for the root part; world = raw vertex + pivot for every other part - see
    RRF_FORMAT.md) - this function has no notion of pivots or Blender object transforms
    at all, matching every other patch_*() function in this module. Converting a
    Blender-space vertex position into this raw value is the caller's job (see
    MESH_OT_pe_write_vertex_positions)."""
    off = _vertex_record_offset(data, part_index, lod, vertex_index)
    struct.pack_into(
        "<iii", data, off,
        float_to_fixed(x), float_to_fixed(y), float_to_fixed(z),
    )


def _face_centroid(face_verts, vertices):
    xs = [vertices[i][0] for i in face_verts]
    ys = [vertices[i][1] for i in face_verts]
    zs = [vertices[i][2] for i in face_verts]
    n = len(face_verts)
    return (sum(xs) / n, sum(ys) / n, sum(zs) / n)


def identity_sort_list(face_count):
    """The 8 sortList blocks as plain 0..n-1 order.

    The right default for a part with no authored ordering to carry forward. Established
    2026-08-12 by reading the real source: NOTHING in the engine or in ObjEdit ever
    generates a mesh sortList. The only writes are offset<->pointer conversion at load,
    and rrBspTreeEdit() (Rrdwire.c) - which despite its name builds no tree, it swaps one
    selected face one position earlier or later in the CURRENT view's block. The ordering
    in a real file is therefore hand-authored by the artist, nudge by nudge, and no
    algorithm can reproduce it. rrAddObject() imports a part from another file, carrying
    that file's ordering with it.

    Identity is not a guess: measured across 3,259 real parts (26,072 blocks), 6.1% of
    blocks ship as exactly this, and 5.1% of parts use one identical ordering for all 8
    octants. It is a shape real content genuinely takes, and draw order can then be tuned
    in ObjEdit's own Sort tool exactly as it always has been."""
    return [list(range(face_count)) for _ in range(8)]


def compute_sort_list(vertices, faces):
    """Regenerates all 8 sortList blocks (RRF_FORMAT.md) for a mesh - Phase 2 of the
    geometry writer (docs/RRF_WRITER_SCOPING.md), needed the moment a part's face count
    changes, since a stale/wrong-length sortList would corrupt the file.

    The direction/depth-sort recipe here is confirmed from the real engine source
    (rrobjpex\\Rrdraw.c's rrDirectionToSortListNo()/rrCalcSortDirection(), and the
    SORT_XSMALL/SORT_YSMALL/SORT_ZSMALL constants also used in Tank.c): the 8 blocks are
    the 8 octants of 3D space, and - empirically confirmed against real sortList data on
    4 independent real parts across 2 different vehicles, Spearman's rho 0.85-0.985 on
    every block, always positive once this exact convention is used - block index bit 0/
    1/2 (X/Y/Z) = 1 means that axis's sort direction is positive, 0 means negative; each
    block orders its faces by ASCENDING face-centroid depth along that direction.

    This is empirically strong but not proven byte-exact against the original tool's own
    per-face depth metric (exact match runs ~7-19% per block on real files, vs. the ~1%
    random-chance baseline) - see RRF_WRITER_SCOPING.md for what this means in practice
    and why a real in-game/ObjEdit visual check still matters for anything that uses this.

    Returns a list of 8 lists, each a permutation of 0..len(faces)-1."""
    centroids = [_face_centroid(f, vertices) for f in faces]
    blocks = []
    for block_index in range(8):
        dx = 1.0 if block_index & 1 else -1.0
        dy = 1.0 if block_index & 2 else -1.0
        dz = 1.0 if block_index & 4 else -1.0
        depths = [cx * dx + cy * dy + cz * dz for cx, cy, cz in centroids]
        order = sorted(range(len(faces)), key=lambda i: depths[i])
        blocks.append(order)
    return blocks


def _region_size(faceCount, vertexCount):
    """Total contiguous byte size of one part's LOD0 mesh-data region (faceList +
    faceNormList + vertexList + vertexNormList + sortList + attribVList). Confirmed via
    real-file offset-gap analysis (2026-07-08): these 6 regions are laid out contiguously
    in exactly this order with zero padding between them, for every part checked across
    multiple real files - and each part's own region likewise follows the previous part's
    with no gap, all the way up to the embedded placeholder texture block at the end of
    the file. faceNormList/vertexNormList entries are 12 bytes each (same 3x-int32
    convention as vertexList) - also confirmed the same way, never previously measured."""
    face_list_size = faceCount * FACE_SIZE
    face_norm_size = faceCount * 12
    vertex_list_size = vertexCount * VERTEX_SIZE
    vertex_norm_size = vertexCount * 12
    sort_list_size = faceCount * 8 * 2
    attrib_count = vertexCount + (vertexCount % 2)
    attrib_size = attrib_count * 2
    return face_list_size + face_norm_size + vertex_list_size + vertex_norm_size + sort_list_size + attrib_size


def _pack_face_record(vertex_indices, texture_id, corners, material_info=None):
    """Packs one face record (24 bytes).

    `material_info` carries the face's own real materialInfo through unchanged, and MUST
    be supplied for any face that already existed - it encodes the shading mode, the
    texture mode, and the bits 8-15 crop-size nibbles that decide how much of the
    assigned .TLB entry the face actually samples. Omitting it (None) falls back to
    materialInfo=0x9 (0x19 for quads), a real, common value that is only appropriate for
    a genuinely NEW face with no prior value to preserve.

    Real bug this parameter exists to fix (found 2026-08-12 via an ObjEdit crash on the
    first added-face test): rebuild_part_mesh_region() repacks every face in the part,
    not just changed ones, so hardcoding materialInfo here silently rewrote it on all of
    them. Measured on a real PantherG hull - 29 distinct values across 328 faces,
    encoding 27 different crop sizes from 16x16 up to 128x96, collapsed to a single
    16x16. That affected the shipped delete-faces operator too, not only new work.
    Corner-role packing matches patch_face_corners() (v1=top-right, v2=top-left,
    v3=bottom-left, textureHalf=bottom-right for quads).

    Only supports textured faces - Phase 2 v1 doesn't write genuinely non-textured/
    solid-shaded new content (see docs/RRF_WRITER_SCOPING.md)."""
    is_quad = len(vertex_indices) == 4
    v1_idx, v2_idx, v3_idx = vertex_indices[0], vertex_indices[1], vertex_indices[2]
    v4_idx = vertex_indices[3] if is_quad else 0

    def _pack(idx, xy):
        x, y = xy
        return (idx & 0xFFFF) | (y << 24) | (x << 16)

    v1 = _pack(v1_idx, corners[0])
    v2 = _pack(v2_idx, corners[1])
    v3 = _pack(v3_idx, corners[2])
    textureHalf = _pack(v4_idx, corners[3]) if is_quad else 0
    textureOfset = 0x80000000 | (texture_id & 0x7FFFFFFF)
    if material_info is None:
        materialInfo = 0x9 | (MAT_QUAD if is_quad else 0)
    else:
        # Keep the face's own real materialInfo, but force MAT_QUAD to agree with the
        # vertex count actually being written - the rest of the value (shading mode,
        # texture mode, and the bits 8-15 crop-size nibbles) is carried through
        # untouched.
        materialInfo = (material_info | MAT_QUAD) if is_quad else (material_info & ~MAT_QUAD)
    return struct.pack("<IIIIII", v1, v2, v3, textureOfset, textureHalf, materialInfo & 0xFFFFFFFF)


def compute_normals(vertices, faces):
    """Geometric face + smooth vertex normals as 16.16-ready unit vectors.

    Confirmed encoding (measured on a real PantherG.RRF, 2026-08-12): both
    faceNormList and vertexNormList hold rrVertex triples of 16.16 fixed point, and
    every real entry sampled had magnitude exactly 1.0000 - they are unit vectors, and
    the engine reads them (Rrdwire.c's own flip tool negates them in place). Writing
    zeros there, as this module used to, leaves a degenerate zero-length normal.

    Face normal = normalized cross product of two edges. Vertex normal = normalized sum
    of the normals of the faces touching that vertex, which is what real files look
    like (adjacent faces sharing smoothly varying vertex normals). Degenerate faces -
    real content contains some - fall back to (0, 0, 1) rather than dividing by zero.

    Only for genuinely NEW geometry: existing elements should always carry their real
    original values forward instead, since those are the artist's own (possibly
    deliberately non-geometric) normals."""
    face_normals = []
    vertex_accum = [[0.0, 0.0, 0.0] for _ in vertices]
    for f in faces:
        a, b, c = vertices[f[0]], vertices[f[1]], vertices[f[2]]
        ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
        nx = uy * vz - uz * vy
        ny = uz * vx - ux * vz
        nz = ux * vy - uy * vx
        mag = (nx * nx + ny * ny + nz * nz) ** 0.5
        if mag < 1e-12:
            nx, ny, nz = 0.0, 0.0, 1.0
        else:
            nx, ny, nz = nx / mag, ny / mag, nz / mag
        face_normals.append((nx, ny, nz))
        for vi in f:
            vertex_accum[vi][0] += nx
            vertex_accum[vi][1] += ny
            vertex_accum[vi][2] += nz

    vertex_normals = []
    for ax, ay, az in vertex_accum:
        mag = (ax * ax + ay * ay + az * az) ** 0.5
        if mag < 1e-12:
            vertex_normals.append((0.0, 0.0, 1.0))
        else:
            vertex_normals.append((ax / mag, ay / mag, az / mag))
    return face_normals, vertex_normals


def read_sort_list(data, part_index, lod=0):
    """Reads a part's 8 sortList blocks as lists of raw uint16 entries.

    Entries are NOT plain face indices: bit 15 (0x8000) is a "skip this face" flag the
    real draw loop tests before using the rest as an index (Rrdraw.c: `if(
    faceOrderList[faceNo]&0x8000 ) continue;`). Raw values are returned with the flag
    intact so it can be carried through rather than silently dropped."""
    mesh_off = _mesh_record_offset(part_index, lod)
    faceCount, = struct.unpack_from("<I", data, mesh_off + 4)
    sortList_off, = struct.unpack_from("<I", data, mesh_off + 28)
    blocks = []
    for b in range(8):
        blocks.append(list(struct.unpack_from(f"<{faceCount}H", data, sortList_off + b * faceCount * 2)))
    return blocks


def derive_sort_list(orig_blocks, face_orig_to_new, new_face_count, new_face_neighbours=None):
    """Derives the 8 sortList blocks from the part's OWN existing ones, instead of
    regenerating an ordering from scratch.

    Why this exists (2026-08-12): compute_sort_list()'s closed-form recipe reproduces
    the real ordering in only 7-11 of 328 positions per block on a real PantherG hull -
    the correlation it was validated against (Spearman rho 0.85-0.99) measured trend,
    not position. The engine never rebuilds this list itself (rrObjSave() writes the
    object verbatim, and rrobjpex only ever reads it), so a real file's ordering is
    authored data, and carrying it forward is strictly better than regenerating an
    ordering that demonstrably does not match.

    Honest limit on the evidence: a regenerated sortList was NOT shown to break anything
    in the real tool. It was suspected during a long ObjEdit crash hunt the same day, but
    every crash in that hunt turned out to be environmental - the test model (PantherG)
    could not be loaded in that ObjEdit setup even as a byte-identical copy of a file
    that was never modified. Deriving is preferred on the principle of not inventing data
    this project cannot verify, not because regenerating was proven harmful.

    Each original block is walked in its own order; surviving faces are emitted in that
    same relative order under their new indices, and the 0x8000 skip flag is preserved
    per entry. Deleted faces simply drop out.

    `new_face_neighbours` optionally maps a brand-new face index to an existing face it
    shares an edge with; the new face is then placed directly after that neighbour, so it
    inherits a real authored position rather than an invented one. Faces with no known
    neighbour are appended at the end of each block (drawn last). Appending is a genuine
    choice, not a derived fact - it is the safe default for a painter-style order, and
    the only part of this function that isn't taken straight from the file.

    Returns 8 lists of exactly `new_face_count` entries."""
    if new_face_neighbours is None:
        new_face_neighbours = {}
    mapped = set(v for v in face_orig_to_new.values() if v is not None)
    brand_new = [i for i in range(new_face_count) if i not in mapped]
    after = {}
    for nf in brand_new:
        nb = new_face_neighbours.get(nf)
        if nb is not None:
            after.setdefault(nb, []).append(nf)
    unplaced = [nf for nf in brand_new if new_face_neighbours.get(nf) is None]

    blocks = []
    for blk in orig_blocks:
        out = []
        for entry in blk:
            flag = entry & 0x8000
            orig_idx = entry & 0x7FFF
            new_idx = face_orig_to_new.get(orig_idx)
            if new_idx is None:
                continue
            out.append((new_idx & 0x7FFF) | flag)
            for nf in after.get(new_idx, ()):
                out.append(nf & 0x7FFF)
        out.extend(nf & 0x7FFF for nf in unplaced)
        if len(out) != new_face_count:
            raise ValueError(
                f"derived sort block has {len(out)} entries, expected {new_face_count} - "
                f"face_orig_to_new is inconsistent with the new face list"
            )
        blocks.append(out)
    return blocks


def repack_existing_face_record(orig_record, new_vertex_indices, new_corners=None):
    """Rebuilds one face record from the ORIGINAL 24 bytes, changing only what actually
    has to change: the low-16 vertex indices (which renumber whenever a part's vertex
    list is rebuilt), and optionally the packed UV corners.

    Preferred over building a record from scratch with _pack_face_record(). Rebuilding a
    part repacks every face, and reconstructing each field from parsed values has
    repeatedly turned out to invent data this project did not know it needed to keep.
    All three losses below were measured by byte-comparing a no-op rebuild against the
    original file, on a single real PantherG hull (2026-08-12) - they are real
    regardless of the crash hunt that prompted the comparison, which turned out to have
    an unrelated environmental cause:
      - materialInfo was hardcoded to 0x9/0x19, flattening 29 distinct real values and
        every per-face crop size to 16x16;
      - faceNormList/vertexNormList were zero-filled, destroying real unit normals;
      - textureHalf was forced to 0 on triangles, while 10 real triangles store 1.
    Each was a separate silent loss. Copying the original bytes forward makes the whole
    class of them impossible, including fields nobody has decoded yet, instead of
    fixing them one at a time as they are discovered.

    Triangles keep their textureHalf verbatim - only quads use it as a vertex slot."""
    v1, v2, v3, textureOfset, textureHalf, materialInfo = struct.unpack("<IIIIII", orig_record)
    is_quad = bool(materialInfo & MAT_QUAD)

    def _set_idx(field, idx):
        return (field & 0xFFFF0000) | (idx & 0xFFFF)

    def _set_corner(field, xy):
        x, y = xy
        return (field & 0x0000FFFF) | (y << 24) | (x << 16)

    v1 = _set_idx(v1, new_vertex_indices[0])
    v2 = _set_idx(v2, new_vertex_indices[1])
    v3 = _set_idx(v3, new_vertex_indices[2])
    if is_quad and len(new_vertex_indices) > 3:
        textureHalf = _set_idx(textureHalf, new_vertex_indices[3])
    if new_corners is not None:
        v1 = _set_corner(v1, new_corners[0])
        v2 = _set_corner(v2, new_corners[1])
        v3 = _set_corner(v3, new_corners[2])
        if is_quad:
            textureHalf = _set_corner(textureHalf, new_corners[3])
    return struct.pack("<IIIIII", v1, v2, v3, textureOfset, textureHalf, materialInfo)


# Collision-box field offsets within a 512-byte part record (Object.h):
#   name[12] @0, pivotX/Y/Z @12/16/20, boxRangeX[2] @24, boxRangeY[2] @32,
#   boxRangeZ[2] @40, boxPosX[4] @48, boxPosY[4] @64, objAttribut @80, maxVertex @84
PART_BOX_RANGE_X = 24
PART_BOX_RANGE_Y = 32
PART_BOX_RANGE_Z = 40
PART_BOX_POS_X = 48
PART_BOX_POS_Y = 64


PART_OBJ_ATTRIBUT = 80   # offset of objAttribut within a 512-byte part record


def obj_attribut_type(value):
    """The part-type id in objAttribut's low byte."""
    return value & 0xFF


def obj_attribut_set_type(value, type_id):
    """Replaces the type in objAttribut's low byte, preserving every other bit
    (including OBJ_ATTRIB_HIDE and whatever else a real file carries there)."""
    return (value & ~0xFF) | (type_id & 0xFF)


def read_part_attribute(data, part_index):
    """Reads a part's raw objAttribut word."""
    off = HEADER_SIZE + part_index * PART_SIZE + PART_OBJ_ATTRIBUT
    return struct.unpack_from("<I", data, off)[0]


def patch_part_attribute(data, part_index, value):
    """Writes a part's objAttribut word. Mutates `data` in place.

    Until this existed the plugin wrote exactly one field of the 512-byte part record
    (maxVertex) plus the collision box, so gameplay tags could be read but never set -
    which meant a model edited in Blender could look right and still not work in game."""
    off = HEADER_SIZE + part_index * PART_SIZE + PART_OBJ_ATTRIBUT
    struct.pack_into("<I", data, off, value & 0xFFFFFFFF)


def parse_obj_attribut_property(raw, fallback=0):
    """Reads the pe_obj_attribut custom property back to an int.

    Stamped at import as a hex string ("0x3") so it is readable in Blender's UI, but a
    user may equally have typed a plain decimal, so both are accepted. Anything
    unparseable falls back rather than corrupting the part record."""
    if raw is None:
        return fallback
    if isinstance(raw, int):
        return raw & 0xFFFFFFFF
    try:
        return int(str(raw).strip(), 0) & 0xFFFFFFFF
    except (TypeError, ValueError):
        return fallback


def compute_part_bounding(vertices):
    """Reproduces the engine's own rrDoGenBounding() (Rrdwire.c), which is what
    ObjEdit's Bounding Box > Gen button calls.

    It scans the part's LOD0 vertices for min/max per axis and stores an axis-aligned
    range per axis plus the four corners of the XY rectangle:

        boxRangeX = [minX, maxX]      boxPosX[0..3] = maxX, minX, maxX, minX
        boxRangeY = [minY, maxY]      boxPosY[0..3] = maxY, maxY, minY, minY
        boxRangeZ = [minZ, maxZ]

    boxPos is held as four points rather than an extent so the box can be ROTATED
    independently of the mesh (rrRotateObjectBounding), with boxRange remaining the
    axis-aligned bound. Values are in the file's own raw coordinate convention, the same
    space as the vertex list.

    Returns (range_x, range_y, range_z, pos_x, pos_y) as tuples of raw ints."""
    if not vertices:
        raise ValueError("cannot compute a bounding box for a part with no vertices")
    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    zs = [v[2] for v in vertices]
    min_x, max_x = float_to_fixed(min(xs)), float_to_fixed(max(xs))
    min_y, max_y = float_to_fixed(min(ys)), float_to_fixed(max(ys))
    min_z, max_z = float_to_fixed(min(zs)), float_to_fixed(max(zs))
    return (
        (min_x, max_x),
        (min_y, max_y),
        (min_z, max_z),
        (max_x, min_x, max_x, min_x),
        (max_y, max_y, min_y, min_y),
    )


def read_part_bounding(data, part_index):
    """Reads a part's stored collision box back as
    (range_x, range_y, range_z, pos_x, pos_y)."""
    off = HEADER_SIZE + part_index * PART_SIZE
    return (
        struct.unpack_from("<ii", data, off + PART_BOX_RANGE_X),
        struct.unpack_from("<ii", data, off + PART_BOX_RANGE_Y),
        struct.unpack_from("<ii", data, off + PART_BOX_RANGE_Z),
        struct.unpack_from("<iiii", data, off + PART_BOX_POS_X),
        struct.unpack_from("<iiii", data, off + PART_BOX_POS_Y),
    )


def part_bounding_is_generated(data, part_index, vertices):
    """True if the part's stored box is exactly what rrDoGenBounding() would produce for
    `vertices` - i.e. it was auto-generated from the mesh and nothing has customised it.

    This distinction is the engine's own, not an invention: object.c notes that if the
    box "does not match the extents of the model, then we can assume that the maker has
    a specific size in mind", and treats such a box as deliberate. Regenerating one of
    those would silently discard a real authoring decision."""
    try:
        expected = compute_part_bounding(vertices)
    except ValueError:
        return False
    return read_part_bounding(data, part_index) == expected


def patch_part_bounding(data, part_index, vertices):
    """Writes a freshly generated collision box for `vertices` into the part record,
    exactly as ObjEdit's Gen button would. Mutates `data` in place."""
    range_x, range_y, range_z, pos_x, pos_y = compute_part_bounding(vertices)
    off = HEADER_SIZE + part_index * PART_SIZE
    struct.pack_into("<ii", data, off + PART_BOX_RANGE_X, *range_x)
    struct.pack_into("<ii", data, off + PART_BOX_RANGE_Y, *range_y)
    struct.pack_into("<ii", data, off + PART_BOX_RANGE_Z, *range_z)
    struct.pack_into("<iiii", data, off + PART_BOX_POS_X, *pos_x)
    struct.pack_into("<iiii", data, off + PART_BOX_POS_Y, *pos_y)


def part_bounding_contains(data, part_index, vertices):
    """True if the part's stored collision box still encloses every vertex.

    Worth checking after a geometry edit even when the box is left alone: only 32.9% of
    real parts carry a box matching their own mesh extent (measured across 1,656 parts),
    and neither the whole-vehicle extent nor own-plus-children explains the rest - 0% for
    both. Those boxes were set deliberately, via ObjEdit's Gen/Rotate/MatchParent/
    MatchMain/MatchTurret tools, so they are preserved rather than regenerated. But a
    preserved box can then fail to contain newly added geometry, and the user should hear
    about that instead of discovering it as odd collision in game."""
    if not vertices:
        return True
    range_x, range_y, range_z, _px, _py = read_part_bounding(data, part_index)
    for axis, (lo, hi) in enumerate((range_x, range_y, range_z)):
        for v in vertices:
            raw = float_to_fixed(v[axis])
            if raw < lo or raw > hi:
                return False
    return True


def rebuild_part_mesh_region(data, part_index, new_vertices, new_faces, new_texture_ids, new_corners, new_attrib_v, new_material_info=None, new_face_normals=None, new_vertex_normals=None, new_face_records=None, new_sort_blocks=None, update_bounding=True):
    """Rebuilds one part's entire LOD0 mesh-data region to reflect a new vertex/face
    count, and shifts every later part's mesh-record offsets (all 8 LOD slots, all 6
    offset fields each - real files always duplicate LOD0's fields identically across all
    8 slots) by the resulting size delta. This is Phase 2 of the geometry writer (see
    docs/RRF_WRITER_SCOPING.md) - the first operation in this project that resizes
    anything, rather than patching a fixed-size field in place.

    new_vertices: list of (x, y, z) raw (file-convention) floats.
    new_faces: list of vertex-index tuples (3 for a triangle, 4 for a quad) into
    new_vertices.
    new_texture_ids / new_corners: parallel lists, one per new_faces entry.
    new_attrib_v: list of ints, one per new_vertices entry (attribVList tag) - callers
    should carry forward real values for surviving vertices and default new ones to 0
    (see docs/RRF_WRITER_SCOPING.md's attribVList findings).
    new_material_info: optional list parallel to new_faces holding each face's real
    materialInfo. Callers MUST pass the original value for every face that already
    existed - this whole region is repacked, so anything not supplied here is silently
    replaced with a default (see _pack_face_record). Use None per-entry only for
    genuinely new faces.

    Also maintains the two vertex-capacity fields that real files keep consistent and
    that this function previously left stale: the part's own `maxVertex` (always equal
    to its LOD0 vertexCount) and the header's `maxAllVertex` (the total across all
    parts, preserving any headroom the file already carried). See
    docs/ADD_FACES_SCOPING.md for the surveys behind both.

    Returns a new bytes object for the WHOLE file - the original buffer is untouched."""
    data = bytearray(data)

    maxLOD, transInfo, objCount, maxAllVertex, textureStart, textureLen = struct.unpack_from("<HHIIII", data, 0)

    part_off = HEADER_SIZE + part_index * PART_SIZE
    mesh_off = _mesh_record_offset(part_index, 0)
    (meshType, old_faceCount, old_faceList_off, old_faceNormList_off,
     old_vertexCount, old_vertexList_off, old_vertexNormList_off,
     old_sortList_off, old_attribVList_off) = struct.unpack_from("<IIIIIIIII", data, mesh_off)

    # Total vertex count across every part BEFORE this edit, needed to maintain the
    # header's maxAllVertex below. Surveyed across 7,418 real .RRF files: maxAllVertex
    # equals this sum exactly in 7,260 of them, matches the largest single part in
    # none, and in the remaining 158 sits slightly ABOVE the sum, never below - i.e.
    # it's a total allocation bound, and any headroom a file already carries is
    # legitimate and worth preserving rather than flattening to the exact sum.
    old_all_vertex_sum = 0
    for p in range(objCount):
        old_all_vertex_sum += struct.unpack_from("<I", data, _mesh_record_offset(p, 0) + 16)[0]
    existing_headroom = max(0, maxAllVertex - old_all_vertex_sum)

    # Whether the collision box is auto-generated has to be decided BEFORE the region is
    # rewritten - afterwards the mesh record already describes the new counts, and reading
    # the old vertices runs off the end. (Regression caught by the capacity tests.)
    bounding_was_generated = False
    if update_bounding:
        try:
            _old_verts = [read_vertex_position(data, part_index, 0, i)
                          for i in range(old_vertexCount)]
            bounding_was_generated = part_bounding_is_generated(data, part_index, _old_verts)
        except (IndexError, struct.error):
            bounding_was_generated = False

    old_region_start = old_faceList_off
    old_region_size = _region_size(old_faceCount, old_vertexCount)
    old_region_end = old_region_start + old_region_size

    new_faceCount = len(new_faces)
    new_vertexCount = len(new_vertices)
    new_region_size = _region_size(new_faceCount, new_vertexCount)
    delta = new_region_size - old_region_size

    # Prefer blocks derived from the part's own original ordering. compute_sort_list()
    # remains a fallback for callers with no original to derive from - it does not
    # reproduce real orderings (see derive_sort_list), though it has not been shown to
    # cause a real failure either.
    # No authored ordering to carry forward -> identity, which real content genuinely
    # uses (see identity_sort_list). compute_sort_list() is kept for callers that
    # explicitly want a depth-ordered guess, but it reproduces no real file and is not
    # the default any more.
    if new_sort_blocks is not None:
        if len(new_sort_blocks) != 8:
            raise ValueError(f"new_sort_blocks must have 8 blocks, got {len(new_sort_blocks)}")
        for bi, blk in enumerate(new_sort_blocks):
            if len(blk) != new_faceCount:
                raise ValueError(f"sort block {bi} has {len(blk)} entries, expected {new_faceCount}")
        sort_blocks = new_sort_blocks
    else:
        sort_blocks = identity_sort_list(new_faceCount)

    face_bytes = bytearray()
    mat_infos = list(new_material_info) if new_material_info is not None else [None] * len(new_faces)
    if len(mat_infos) != len(new_faces):
        raise ValueError(f"new_material_info has {len(mat_infos)} entries for {len(new_faces)} faces")
    orig_records = list(new_face_records) if new_face_records is not None else [None] * len(new_faces)
    if len(orig_records) != len(new_faces):
        raise ValueError(f"new_face_records has {len(orig_records)} entries for {len(new_faces)} faces")
    for face_verts, tex_id, corners, mat, orig in zip(new_faces, new_texture_ids, new_corners, mat_infos, orig_records):
        if orig is not None:
            # Existing face: carry its real bytes forward, remapping only the vertex
            # indices (and corners, if the caller recomputed them).
            face_bytes += repack_existing_face_record(orig, face_verts, new_corners=corners)
        else:
            face_bytes += _pack_face_record(face_verts, tex_id, corners, material_info=mat)
    # Normals: carry the real ones through when given, otherwise compute real geometric
    # ones. Previously both lists were zero-filled with the note "normals are
    # recalculated on import anyway" - true of THIS importer, but not of the game or
    # ObjEdit, which read these values (see compute_normals). Zeroing them destroyed
    # real data on every rebuild, including every delete performed to date.
    if new_face_normals is None or new_vertex_normals is None:
        calc_face_n, calc_vertex_n = compute_normals(new_vertices, new_faces)
        if new_face_normals is None:
            new_face_normals = calc_face_n
        if new_vertex_normals is None:
            new_vertex_normals = calc_vertex_n
    if len(new_face_normals) != new_faceCount:
        raise ValueError(f"new_face_normals has {len(new_face_normals)} entries for {new_faceCount} faces")
    if len(new_vertex_normals) != new_vertexCount:
        raise ValueError(f"new_vertex_normals has {len(new_vertex_normals)} entries for {new_vertexCount} vertices")
    face_norm_bytes = bytearray()
    for nx, ny, nz in new_face_normals:
        face_norm_bytes += struct.pack("<iii", float_to_fixed(nx), float_to_fixed(ny), float_to_fixed(nz))
    vertex_bytes = bytearray()
    for x, y, z in new_vertices:
        vertex_bytes += struct.pack("<iii", float_to_fixed(x), float_to_fixed(y), float_to_fixed(z))
    vertex_norm_bytes = bytearray()
    for nx, ny, nz in new_vertex_normals:
        vertex_norm_bytes += struct.pack("<iii", float_to_fixed(nx), float_to_fixed(ny), float_to_fixed(nz))
    sort_bytes = bytearray()
    for block in sort_blocks:
        sort_bytes += struct.pack(f"<{new_faceCount}H", *block)
    # attribVList is padded up to an even entry count. A caller may pass the already-
    # padded list (length == attrib_count) to preserve whatever the original held in
    # that trailing slot - real files do not always leave it zero, and zeroing it was
    # the last thing standing between a no-op rebuild and a byte-identical file.
    attrib_count = new_vertexCount + (new_vertexCount % 2)
    if len(new_attrib_v) == attrib_count:
        attrib_padded = list(new_attrib_v)
    else:
        attrib_padded = list(new_attrib_v) + [0] * (attrib_count - len(new_attrib_v))
    attrib_bytes = struct.pack(f"<{attrib_count}H", *attrib_padded)

    new_region = bytes(face_bytes) + bytes(face_norm_bytes) + bytes(vertex_bytes) + bytes(vertex_norm_bytes) + bytes(sort_bytes) + bytes(attrib_bytes)
    if len(new_region) != new_region_size:
        raise AssertionError(f"internal error: built {len(new_region)} bytes, expected {new_region_size}")

    # Splicing a differently-sized slice into a bytearray automatically resizes the
    # whole buffer and shifts everything after it - the placeholder texture block's
    # bytes move for free here, only the header's own record of where it starts (below)
    # needs updating.
    data[old_region_start:old_region_end] = new_region

    new_faceList_off = old_region_start
    new_faceNormList_off = new_faceList_off + new_faceCount * FACE_SIZE
    new_vertexList_off = new_faceNormList_off + new_faceCount * 12
    new_vertexNormList_off = new_vertexList_off + new_vertexCount * VERTEX_SIZE
    new_sortList_off = new_vertexNormList_off + new_vertexCount * 12
    new_attribVList_off = new_sortList_off + new_faceCount * 8 * 2

    for lod in range(8):
        lod_off = part_off + 224 + lod * MESH_SIZE
        struct.pack_into(
            "<IIIIIIIII", data, lod_off,
            meshType, new_faceCount, new_faceList_off, new_faceNormList_off,
            new_vertexCount, new_vertexList_off, new_vertexNormList_off,
            new_sortList_off, new_attribVList_off,
        )

    # Collision box. A PEDG modder put the requirement plainly - "when you add an object
    # to an rrf, you must also adjust the bounding box" - and a stale box no longer
    # contains the mesh after geometry changes. But it is only regenerated when the
    # stored box still matches what rrDoGenBounding() would produce for the OLD geometry:
    # object.c treats a box that does not match the model's extents as deliberate ("the
    # maker has a specific size in mind"), so a customised one is left alone.
    if update_bounding and new_vertices and bounding_was_generated:
        patch_part_bounding(data, part_index, new_vertices)

    # Per-part maxVertex duplicates that part's own LOD0 vertexCount - surveyed across
    # 33,023 real parts with zero mismatches, so this is an invariant, not a tendency.
    # It was previously left untouched here, which quietly broke that invariant on every
    # edit (a real 453->450 shrink left maxVertex still reading 453). Harmless while only
    # shrinking, since the stale value is then merely too large, but wrong in the unsafe
    # direction the moment a part grows.
    struct.pack_into("<I", data, part_off + 84, new_vertexCount)

    if delta != 0:
        for p in range(part_index + 1, objCount):
            other_part_off = HEADER_SIZE + p * PART_SIZE
            for lod in range(8):
                lod_off = other_part_off + 224 + lod * MESH_SIZE
                vals = list(struct.unpack_from("<IIIIIIIII", data, lod_off))
                vals[2] += delta  # faceList_off
                vals[3] += delta  # faceNormList_off
                vals[5] += delta  # vertexList_off
                vals[6] += delta  # vertexNormList_off
                vals[7] += delta  # sortList_off
                vals[8] += delta  # attribVList_off
                struct.pack_into("<IIIIIIIII", data, lod_off, *vals)

    # Header: textureStart moves with the region resize, and maxAllVertex has to track
    # the new total. Written unconditionally rather than only when delta != 0 - the
    # vertex count can change while the region size happens not to (e.g. faces removed
    # and vertices added in one edit), and maxAllVertex would then silently go stale.
    new_all_vertex_sum = old_all_vertex_sum - old_vertexCount + new_vertexCount
    new_maxAllVertex = new_all_vertex_sum + existing_headroom
    new_textureStart = textureStart + delta
    struct.pack_into("<HHIIII", data, 0, maxLOD, transInfo, objCount,
                     new_maxAllVertex, new_textureStart, textureLen)

    return bytes(data)


def _bbox(vertices):
    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    zs = [v[2] for v in vertices]
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


def detect_add_pivot_convention(parts):
    """Every non-root part's raw vertices are local to that part - world position = raw
    vertex + pivot, unconditionally, for every real vehicle and prop checked.

    This corrects two earlier, wrong versions of this function. The first assumed
    vehicles use "world = raw vertex" unmodified (no pivot add) based on early testing
    that never actually exercised the difference: several parts checked at the time
    (e.g. Tiger1's Turret/trackL/trackR) happen to have a pivot within a fraction of a
    unit of (0,0,0), so "add pivot" and "don't" render identically for them regardless
    of which is correct - the screenshots that seemed to confirm "no add" were actually
    uninformative, not supporting evidence.

    The second version tried to detect the convention per part (comparing how far each
    candidate placement oversoots the root part's own bounding box), after noticing
    Pz4H.RRF's 16 road wheels render stacked at the model's centre under a single
    file-wide "no add" vote. That per-part heuristic was itself wrong: it also flagged
    Pz4H.RRF's turret and Tiger1's hatch/radio/gun/coax MG as "no add", on the theory
    that a part cleanly nesting inside the root bbox without adding its pivot must
    already be in world-space. Rendered and visually checked (not just bbox math) -
    Pz4H.RRF's turret with "no add" is a flat slab fused into the hull roof; with "add
    pivot" it's an unmistakable, correctly elevated turret with mantlet and cupola.
    The bbox-overshoot signal is simply unreliable here: a part sitting correctly
    *above* the hull roof, *below* the hull belly, or spread along the hull sides
    routinely and legitimately falls outside the hull mesh's own narrow bounding box,
    which is exactly what the overshoot test penalizes.

    Every non-root part in every real file checked (Tiger1, Pz4H_3, Pz4H, Pz4H2,
    PantherG2, ISU-152, aaFlatcar) has a substantial, non-trivial pivot - consistent
    with a standard rigged-parts-hierarchy design (mesh authored local to its own pivot,
    placed by translating to that pivot), not a coincidence specific to one asset.

    Returns {part_index: True} for every non-root part that has vertex data. The root
    part is never included (nothing to nest it inside).
    """
    if not parts:
        return {}
    return {part.index: True for part in parts[1:] if part.vertices}


# Euclidean RGB distance (0-1 per channel, max ~1.73), measured in the LINEAR space an
# Image Texture node outputs - not in the 0-255 sRGB values the artwork is authored in.
# That distinction is the whole reason this value is not 0.05: real PE key pixels are
# commonly near-white rather than pure white (CustomA1's road-wheel entry 391 keys on
# (250,250,250)), and sRGB 250 is linear 0.9559, so its distance from white is 0.0764 -
# over a 0.05 threshold, leaving every road wheel sitting on an opaque white box.
# 0.12 covers keys down to about sRGB 245 while staying far from real paint: the lightest
# sand camo here is ~(210,190,150), a linear distance of ~0.93.
COLORKEY_DISTANCE_THRESHOLD = 0.12


def _build_material(root_name, image_path, tlb_filepath=None, tlb_confidence=None, use_colorkey=True, colorkey_color=(1.0, 1.0, 1.0)):
    """use_colorkey/colorkey_color: real 1999-era engines commonly render one reserved
    color as "don't draw this pixel" instead of storing real per-pixel alpha - confirmed
    on a real model (6pdr.RRF/Desert2.tlb): the wheel part's spoke-gap faces sample exact
    pure white (1.0,1.0,1.0) while every other sampled face on the same part samples
    normal metal/wheel tones, a clean signal this is a deliberate reserved color, not
    incidental. This isn't a fixed universal constant, though - the same convention on
    PP2-X-sourced content reportedly uses bright pink/magenta instead - so the key color
    is a real, per-import setting (default white, the confirmed real case), not hardcoded.
    Wires a distance-from-key-color test into the material's own Alpha input; set
    use_colorkey=False for content where the key color is itself meaningful paint (e.g. a
    blank paintable canvas that happens to start off white)."""
    image = bpy.data.images.load(image_path, check_existing=True)
    if tlb_filepath:
        # Lets face-level operators (e.g. "detach face from shared texture cell", see
        # TODO.md) find their way from a material's image back to the .TLB it came from,
        # without re-deriving it from the image filename (fragile - real files mix
        # .TLB/.tlb casing, and the _8.BMP/_24.BMP suffix-stripping isn't foolproof).
        image["pe_tlb_filepath"] = tlb_filepath
    if tlb_confidence:
        # How this .TLB was actually determined - "manual" (explicitly typed in),
        # "rri" (a real .RRI sidecar, the authoritative source), "auto_high"/"auto_low"
        # (auto-detect's own scoring - see _classify_tlb_confidence()). Inspectable
        # later in Blender's own UI, not just something that scrolled by in the import
        # report - auto-detect has repeatedly looked plausible and been wrong this
        # project's own testing (Psw232, twice), so this is worth being able to check
        # after the fact, not just at import time.
        image["pe_tlb_confidence"] = tlb_confidence
    material = bpy.data.materials.new(root_name + "_mat")
    material.use_nodes = True
    bsdf = material.node_tree.nodes.get("Principled BSDF")
    tex_node = material.node_tree.nodes.new("ShaderNodeTexImage")
    tex_node.image = image
    tex_node.interpolation = "Closest"  # this is 1999 paletted atlas art, keep it crisp
    if bsdf is not None:
        material.node_tree.links.new(tex_node.outputs["Color"], bsdf.inputs["Base Color"])
        if use_colorkey:
            distance_node = material.node_tree.nodes.new("ShaderNodeVectorMath")
            distance_node.operation = "DISTANCE"
            distance_node.inputs[1].default_value = tuple(colorkey_color)
            material.node_tree.links.new(tex_node.outputs["Color"], distance_node.inputs[0])

            threshold_node = material.node_tree.nodes.new("ShaderNodeMath")
            threshold_node.operation = "GREATER_THAN"
            threshold_node.inputs[1].default_value = COLORKEY_DISTANCE_THRESHOLD
            material.node_tree.links.new(distance_node.outputs["Value"], threshold_node.inputs[0])
            material.node_tree.links.new(threshold_node.outputs["Value"], bsdf.inputs["Alpha"])

            material.blend_method = "CLIP"
            material.alpha_threshold = 0.5
    # Blender's Texture Paint mode paints onto whichever Image Texture node is the node
    # tree's *active* node, not just any node carrying an image - left at the default (the
    # Material Output node the material starts with), Texture Paint has no canvas to paint
    # on at all (tool_settings.image_paint.canvas comes back None), so a real paint stroke
    # silently does nothing. Selecting and marking this node active is what makes painting
    # on the imported atlas actually work.
    for node in material.node_tree.nodes:
        node.select = False
    tex_node.select = True
    material.node_tree.nodes.active = tex_node
    return material


def _build_unresolved_material():
    """Bright magenta flag material for faces whose textureOfset doesn't match any entry
    in the given .TLB - some content packs bake a live HAL texture handle instead of a
    stable library ID into this field, which can't be resolved from the file after the
    fact (see project notes on the Ostpak texture-ID investigation). Magenta makes those
    faces impossible to miss in the viewport so they can be found and re-textured by hand."""
    material = bpy.data.materials.get("PE_UNRESOLVED_TEXTURE")
    if material is not None:
        return material
    material = bpy.data.materials.new("PE_UNRESOLVED_TEXTURE")
    material.use_nodes = True
    material.diffuse_color = (1.0, 0.0, 1.0, 1.0)
    bsdf = material.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = (1.0, 0.0, 1.0, 1.0)
    return material


def _recalculate_normals(mesh):
    """PE's renderer only enforces consistent winding for single-sided (non-MAT_TWOSIDE)
    faces (see the screen-space cross-product backface test in Rrdraw.c) - two-sided faces
    were never required to wind consistently since the game doesn't cull their backfaces
    either way. That leaves no single reliable "outward" convention to carry over from the
    file, so recalculate from the actual mesh shape instead of trusting stored winding.

    Real shipped content includes occasional degenerate faces - a repeated vertex index
    within the same face (confirmed on Psw232.RRF's "turretL" part: 8 of its 104 faces,
    e.g. one quad using vertex 46 twice). bmesh.ops.recalc_face_normals() hangs
    indefinitely if any of its input faces are degenerate this way - confirmed
    reproducible (not a one-off): turretL hangs every time, while turretR, an
    identically-sized part on the same model with no degenerate faces, completes
    instantly. Excluding just the degenerate faces from this call (not from the mesh
    itself) avoids the hang while leaving mesh.polygons' count and order completely
    untouched - critical since face_texture_id/face_uv_corners and the detach-face
    operator all index by original file face order, and mesh.validate() (which does drop
    these) would break that alignment."""
    bm = bmesh.new()
    bm.from_mesh(mesh)
    valid_faces = [f for f in bm.faces if len({v.index for v in f.verts}) == len(f.verts)]
    bmesh.ops.recalc_face_normals(bm, faces=valid_faces)
    bm.to_mesh(mesh)
    bm.free()


def build_blender_objects(parts, collection, root_name, slot_sources=None, rrf_filepath=None, tlb_confidence=None, use_colorkey=True, colorkey_color=(1.0, 1.0, 1.0)):
    """slot_sources: {slot_index: (tlb_parts, atlas_image_path, tlb_filepath)} or None for
    geometry-only import. A model can use several libraries at once (one per slot) - each
    gets its own material, built once here and shared across every part/mesh, since the
    same slot assignments apply model-wide.

    rrf_filepath (optional): stamped onto every created object as `pe_rrf_filepath`, so a
    face-level operator working on the resulting mesh can find its way back to the source
    .RRF - same purpose as `_build_material()`'s `pe_tlb_filepath` on the Image.

    tlb_confidence (optional): stamped onto every created Image as `pe_tlb_confidence` -
    see _build_material()'s docstring."""
    slot_to_parts = {}
    slot_to_material = {}
    atlas_path_to_material = {}
    unresolved_material = None

    if slot_sources:
        unresolved_material = _build_unresolved_material()
        for slot, (tlb_parts, atlas_image_path, tlb_filepath) in slot_sources.items():
            slot_to_parts[slot] = tlb_parts
            if not atlas_image_path:
                continue
            material = atlas_path_to_material.get(atlas_image_path)
            if material is None:
                label = os.path.splitext(os.path.basename(atlas_image_path))[0]
                material = _build_material(f"{root_name}_{label}", atlas_image_path, tlb_filepath, tlb_confidence, use_colorkey, colorkey_color)
                atlas_path_to_material[atlas_image_path] = material
            slot_to_material[slot] = material

    # Fixed material slot list, shared by every mesh: unique library materials + magenta flag.
    mesh_materials = list(atlas_path_to_material.values())
    if unresolved_material is not None:
        mesh_materials.append(unresolved_material)
    unresolved_slot = len(mesh_materials) - 1
    material_index_of = {mat: i for i, mat in enumerate(mesh_materials)}

    resolved_count = 0
    unresolved_count = 0

    # Two different vertex conventions show up in real shipped .RRF files, decided per
    # part - see detect_add_pivot_convention() for the full explanation. Root never needs
    # this (there's nothing to nest it inside) - only non-root parts.
    add_pivot_by_part = detect_add_pivot_convention(parts)

    objects = []
    for part in parts:
        type_id = part.obj_attribut & 0xFF
        hidden = bool(part.obj_attribut & OBJ_ATTRIB_HIDE)

        if part.faces:
            mesh = bpy.data.meshes.new(part.name)
            px, py, pz = part.pivot
            if add_pivot_by_part.get(part.index) and part.parent_no is not None:
                # Object origin still goes at the pivot (below), so keep vertices
                # part-local relative to it - equivalent to "world = raw + pivot".
                local_verts = list(part.vertices)
            else:
                local_verts = [(vx - px, vy - py, vz - pz) for vx, vy, vz in part.vertices]
            mesh.from_pydata(local_verts, [], part.faces)
            mesh.update()
            _recalculate_normals(mesh)

            # Tracks each vertex/face's original position in the file's own LOD0 arrays
            # - from_pydata() preserves the exact given order, so at this point these are
            # simply 0..count-1, but they matter once a later edit changes vertex/face
            # count (see MESH_OT_pe_delete_faces): a geometry-writing operator can look up
            # a surviving element's real original index (to carry over its texture/
            # attribVList data) even after Blender itself renumbers everything.
            face_index_attr = mesh.attributes.new(name="pe_face_index", type="INT", domain="FACE")
            for i in range(len(mesh.polygons)):
                face_index_attr.data[i].value = i
            vertex_index_attr = mesh.attributes.new(name="pe_vertex_index", type="INT", domain="POINT")
            for i in range(len(mesh.vertices)):
                vertex_index_attr.data[i].value = i

            # "This element came from the file" marker. pe_face_index/pe_vertex_index
            # alone cannot answer that: BMesh zero-initialises custom data on newly
            # created elements, so a face the user adds in Blender arrives claiming
            # index 0 - indistinguishable from real face 0. These start at 1 for
            # everything imported, so anything reading 0 is genuinely new. Required by
            # MESH_OT_pe_write_mesh to tell which elements have real file data to carry
            # forward and which have to be built from scratch.
            face_orig_attr = mesh.attributes.new(name="pe_face_orig", type="INT", domain="FACE")
            for i in range(len(mesh.polygons)):
                face_orig_attr.data[i].value = 1
            vertex_orig_attr = mesh.attributes.new(name="pe_vertex_orig", type="INT", domain="POINT")
            for i in range(len(mesh.vertices)):
                vertex_orig_attr.data[i].value = 1

            if slot_sources:
                uv_layer = mesh.uv_layers.new(name="UVMap")
                unresolved_attr = mesh.attributes.new(
                    name="pe_texture_unresolved", type="BOOLEAN", domain="FACE"
                )
                # Own material for flat-colour faces, so they are visibly distinct from
                # both textured and unresolved geometry rather than silently riding on the
                # atlas material.
                flat_mat = bpy.data.materials.get("PE_FLAT_COLOR")
                if flat_mat is None:
                    flat_mat = bpy.data.materials.new("PE_FLAT_COLOR")
                    flat_mat.use_nodes = True
                    _b = flat_mat.node_tree.nodes.get("Principled BSDF")
                    if _b is not None:
                        _b.inputs["Base Color"].default_value = (0.34, 0.34, 0.34, 1.0)
                # Build this mesh's slot list WITHOUT mutating the shared mesh_materials
                # list - doing so accumulated a duplicate PE_FLAT_COLOR per part.
                _mats = list(mesh_materials) + [flat_mat]
                for mat in _mats:
                    mesh.materials.append(mat)
                flat_index = len(_mats) - 1

                for poly in mesh.polygons:
                    corners = part.face_uv_corners[poly.index]
                    tex_id = part.face_texture_id[poly.index]
                    if tex_id is None:
                        # A FLAT-COLOUR face: textureOfset bit 31 is clear, so the field
                        # holds a colour rather than a texture id. 1,158 faces across a
                        # real install (1.38%) are like this.
                        #
                        # Skipping used to leave them on the shared atlas material with
                        # Blender's default 0-1 UVs, which samples the ENTIRE 256x4096
                        # atlas across one polygon - the whole texture appearing on a
                        # single gun-barrel face. They were not counted as unresolved
                        # either, so nothing flagged it: convincingly wrong rather than
                        # visibly unresolved, the recurring failure mode in this importer.
                        #
                        # Give them their own material and collapse their UVs so they can
                        # never sample the atlas. Decoding the actual PE colour out of the
                        # low bits is a separate job - see KNOWN_LIMITATIONS.md.
                        poly.material_index = flat_index
                        for loop_index in poly.loop_indices:
                            uv_layer.data[loop_index].uv = (0.0, 0.0)
                        continue
                    entry, slot = resolve_texture_id(tex_id, slot_to_parts) if corners is not None else (None, None)
                    material = slot_to_material.get(slot) if entry is not None else None
                    if entry is not None and material is not None:
                        resolved_count += 1
                        poly.material_index = material_index_of[material]
                        posX, posY, sizeX, sizeY = entry
                        # A face that was never individually cropped in the original tool
                        # has all corners at (0,0) - confirmed on real content (every one
                        # of a whole building's resolved faces, not just a rare one-off),
                        # too systematic to be a genuine "crop to one pixel" choice.
                        #
                        # This does NOT mean "use the entry's full allocated rectangle" -
                        # confirmed wrong via a live ObjEdit comparison on 88Pak43.RRF/
                        # Normandy1.tlb (real bug report: shield/barrel faces rendered
                        # stretched/smeared): entry 160 is allocated 32x32, but ObjEdit
                        # showed the actual face using only a 32x16 crop - the real used
                        # size comes from face_crop_size (materialInfo bits, see
                        # _read_mesh_lod0), confirmed against every distinct materialInfo
                        # value on that same part (always a clean 16px-multiple submultiple
                        # of the entry's own size - consistent with one entry commonly
                        # being shared by several faces, each using its own smaller
                        # sub-tile of it, not the whole thing every time).
                        if all(c == (0, 0) for c in corners):
                            # Crop rectangle for a face with no explicit corners: origin
                            # from textureOfset bits 16-23, size from materialInfo -
                            # exactly rrUsedSelection()'s own all-zero branch. The origin
                            # was previously assumed (0, 0), which is why faces sharing a
                            # large entry all sampled its top-left corner.
                            crop = part.face_crop_size[poly.index]
                            if crop:
                                crop_w, crop_h, start_x, start_y = crop[:4]
                            else:
                                crop_w, crop_h, start_x, start_y = sizeX, sizeY, 0, 0
                            start_x = min(start_x, max(sizeX - 1, 0))
                            start_y = min(start_y, max(sizeY - 1, 0))
                            crop_x = min(crop_w, sizeX - start_x)
                            crop_y = min(crop_h, sizeY - start_y)
                            # Span the FULL crop, edge to edge - not crop-1.
                            #
                            # rrUsedSelection() treats a face's rect as FStartX ..
                            # FStartX+FSizeX, i.e. covering FSizeX pixels. Subtracting 1
                            # here made the UV rect one pixel short, so every face sampled
                            # a slightly shrunken region: (crop-1)/crop, which is 3% on a
                            # 32px crop but 6.25% on a 16px one. Because the error scales
                            # with crop size it showed up as "the cells are not the same
                            # scale" when a labelled test grid was compared against
                            # ObjEdit's own render of the same model.
                            x0, y0 = start_x, start_y
                            x1, y1 = start_x + crop_x, start_y + crop_y
                            # Corner order for a face with NO stored corner data.
                            #
                            # v1 = top-LEFT, v2 = top-right, v3 = bottom-right,
                            # v4 = bottom-left. Note this is the horizontal MIRROR of the
                            # explicit-corner order below, where rrSetTexture() pins
                            # v1 = top-RIGHT. That is not an inconsistency to "fix": the
                            # all-zero path stores no corners at all, so its default is a
                            # convention living in OBJHALX5.dll, which has no source. It
                            # had to be measured.
                            #
                            # Measured against ObjEdit's own render, on two models that
                            # are 100% all-zero faces:
                            #   - Sdkfz184 road wheels: the baked-in shadow sits at 4
                            #     o'clock in ObjEdit. The un-mirrored order put it at 8.
                            #   - Italy Tiger turret number: reads "414" mirrored,
                            #     and reversed un-mirrored.
                            # Both flipped together, which is why this is one global
                            # convention rather than a per-face flag.
                            full_rect = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
                            corners = full_rect[:len(corners)]
                        else:
                            # Explicit corners are NOT four literal positions - they are a
                            # mix of origin and SIZE, written by rrSetTexture() (Rrdwire.c)
                            # into the UPPER 16 bits of each field, the low 16 being the
                            # vertex index:
                            #   v1 = idx | (yStart<<24) | (xSize <<16)
                            #   v2 = idx | (yStart<<24) | (xStart<<16)
                            #   v3 = idx | (ySize <<24) | (xStart<<16)
                            #   textureHalf = idx | (ySize<<24) | (xSize<<16)   [quads]
                            # rrUsedSelection() reads these back as v1 & 0xFF etc. only
                            # because its uvFace argument is already the >>16 form (see
                            # rrGetSelection: uvFace->v1 = viewF[v].v1 >> 16) - do not
                            # read the file's low bytes, which are vertex indices.
                            # _corner_xy() applies that >>16/>>24 shift, so the indices
                            # below land on the right values.
                            # Each is incremented when non-zero, matching how rrSetTexture()
                            # writes them (xStart = X-1, xSize = sx-1). Treating v1.x as a
                            # right-edge coordinate - which this importer did - puts the
                            # crop in the wrong place for every face that carries real
                            # corner data, and 69.9% of all textured faces on a real
                            # install do (5,198,380 of 7,437,702 across 928 models).
                            e_sx = corners[0][0]
                            e_sy = corners[2][1]
                            e_ox = corners[2][0]
                            e_oy = corners[0][1]
                            if e_sx: e_sx += 1
                            if e_sy: e_sy += 1
                            if e_ox: e_ox += 1
                            if e_oy: e_oy += 1
                            if e_sx and e_sy:
                                x0, y0 = e_ox, e_oy
                                x1, y1 = e_ox + e_sx - 1, e_oy + e_sy - 1
                                rect = [(x1, y0), (x0, y0), (x0, y1), (x1, y1)]
                                corners = rect[:len(corners)]
                        # Bind each corner to the FILE's vertex, not to the loop's
                        # position in the polygon.
                        #
                        # PE stores a face's texture ORIENTATION in its vertex order -
                        # rrRotateTexture() (Rrdwire.c) rotates a texture purely by
                        # permuting v1->v2->v3->v4, touching no UV value and no flag. So
                        # corner i belongs to file vertex i, permanently.
                        #
                        # Zipping corners onto poly.loop_indices positionally assumed
                        # Blender kept that order. It does not: _recalculate_normals()
                        # reverses the winding of any face whose normal disagreed with
                        # its neighbours - measured at 27 of 380 faces on Sdkfz184,
                        # concentrated in Main_Gun. A reversed winding mirrors the
                        # texture, and on striped camo a mirror reads as a 90 degree
                        # rotation, which is exactly what the user reported on the
                        # casemate and gun barrel.
                        file_face = part.faces[poly.index] if poly.index < len(part.faces) else ()
                        corner_of_vertex = {}
                        if len(file_face) == len(corners):
                            for vidx, xy in zip(file_face, corners):
                                corner_of_vertex.setdefault(vidx, xy)
                        for slot, loop_index in enumerate(poly.loop_indices):
                            vidx = mesh.loops[loop_index].vertex_index
                            # Fall back to positional order if this face repeats a vertex
                            # index (the dict cannot disambiguate those) or the counts
                            # disagree, which keeps degenerate faces behaving as before.
                            lx, ly = corner_of_vertex.get(vidx, corners[min(slot, len(corners) - 1)])
                            atlas_x = posX * 16 + lx
                            atlas_y = posY * 16 + ly
                            u = atlas_x / ATLAS_WIDTH
                            v = 1.0 - (atlas_y / ATLAS_HEIGHT)
                            uv_layer.data[loop_index].uv = (u, v)
                    else:
                        unresolved_count += 1
                        unresolved_attr.data[poly.index].value = True
                        poly.material_index = unresolved_slot

            obj = bpy.data.objects.new(part.name, mesh)
            obj.location = part.pivot
        else:
            obj = bpy.data.objects.new(part.name, None)
            obj.empty_display_size = 0.1
            obj.location = part.pivot

        obj["pe_part_index"] = part.index
        obj["pe_obj_attribut"] = hex(part.obj_attribut)
        obj["pe_type_id"] = type_id
        obj["pe_type_name"] = OBJ_TYPE_NAMES.get(type_id, "UNKNOWN")
        # Stamped separately from obj.location (even though they start out equal) so a
        # geometry-writing operator (MESH_OT_pe_write_vertex_positions) always has the
        # file's real pivot to convert with, even if the object itself gets moved in
        # Object Mode after import - obj.location can drift, this can't.
        obj["pe_pivot"] = part.pivot
        if rrf_filepath:
            obj["pe_rrf_filepath"] = rrf_filepath

        collection.objects.link(obj)
        # hide_set() needs the object linked into the view layer first, hence linking
        # before this rather than alongside the other obj[...] setup above.
        obj.hide_set(hidden)
        obj.hide_render = hidden
        objects.append(obj)

    root = parts[0] if parts else None
    for part, obj in zip(parts, objects):
        if part.parent_no is not None and 0 <= part.parent_no < len(objects):
            obj.parent = objects[part.parent_no]
            parent_part = parts[part.parent_no]
            if parent_part is root:
                # The root part's own pivot is the model's coordinate-frame anchor, not a
                # translation to compound into descendants - root's own mesh is always
                # placed as world = raw vertex (see the local_verts branch above), with no
                # pivot arithmetic involved at all. Its DIRECT children, though, still get
                # obj.location = their own pivot (an absolute Blender property that adds
                # into the hierarchy), so root's pivot must be explicitly cancelled here or
                # every direct child - turret, wheels, tracks, add-on kit - drifts by
                # root's own pivot value relative to the hull it's actually attached to.
                # Deeper descendants (turret's own children and beyond) must NOT get this
                # same cancellation - their parent's pivot is exactly the offset they need
                # summed in (see below).
                obj.matrix_parent_inverse = Matrix.Translation(parent_part.pivot).inverted()
            # else: non-root parent - no override, so Blender's default hierarchical
            # composition sums this part's pivot on top of its parent's (and so on up to,
            # but not including, root) exactly as intended. Corrects an earlier, wrong
            # reading of this format that cancelled every level's pivot uniformly
            # (believing pivots were root-absolute, not parent-relative deltas): that
            # seemed to fix Tiger1's gun barrel flying out under naive full summing, but
            # Tiger1's Turret pivot happens to be within a fraction of a unit of (0,0,0),
            # so canceling it or not renders identically there - not real evidence either
            # way. Pz4H.RRF's main_gun (parent "turret", pivot a substantial
            # (0, 1.45, 7.6)) exposed the actual bug: cancelling every level placed the
            # gun at hull-deck height, disconnected from the turret it's mounted in;
            # summing correctly (root cancelled once, everything past it left to sum
            # naturally) puts it exactly at turret height, protruding from the mantlet -
            # verified by rendering both models, including Tiger1's original 4-level
            # Kanone->Blende->turm->Tiger chain, which holds up fine under this rule too
            # (its "flying out" bug really was about needing root cancelled, just not
            # every level beyond it).

    return objects, resolved_count, unresolved_count


class IMPORT_OT_rrf(bpy.types.Operator, ImportHelper):
    bl_idname = "import_scene.pe_rrf"
    bl_label = "Import Panzer Elite Model (.rrf)"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".rrf"
    filter_glob: StringProperty(default="*.rrf;*.RRF", options={"HIDDEN"})

    tlb_filepath: StringProperty(
        name="Texture Library (.TLB)",
        description="Optional - the exact .TLB this model's textures were painted from. "
                    "Takes priority over everything below. If set, its matching "
                    "_24.BMP/_8.BMP atlas is used to build UVs and a material",
        subtype="FILE_PATH",
        default="",
    )

    use_rri: BoolProperty(
        name="Use .RRI Library List (if present)",
        description="A later ObjEdit build can save a companion .RRI file next to the "
                    ".RRF, listing the exact library loaded into each of the 16 texture "
                    "slots when the model was painted - the authoritative answer, no "
                    "guessing needed. Used automatically when found unless Texture "
                    "Library (.TLB) above is set",
        default=True,
    )

    tlb_search_folder: StringProperty(
        name="Auto-detect TLB in Folder (optional override)",
        description="Only needed if the automatic sibling-Texture-folder guess isn't "
                    "right for this install layout, or you want to point at a different "
                    "folder. Every .TLB directly in it (not subfolders) is scored by how "
                    "many of this model's texture IDs it resolves and the best match is "
                    "used. Leave blank to auto-search the model's own sibling \"Texture\" "
                    "folder (<install root>\\Texture\\, next to the .RRF's own pack "
                    "folder) - this already runs automatically with no input needed when "
                    "there's no .RRI (or Use .RRI is off) and Texture Library (.TLB) "
                    "above is blank",
        subtype="DIR_PATH",
        default="",
    )

    theatre: EnumProperty(
        name="Theatre",
        description="Same question the real ObjEdit asks when it opens a model - which "
                    "texture set does this belong to? Narrows auto-detect to just .TLB "
                    "files with that name prefix before scoring, instead of guessing "
                    "across every library in the folder. Ignored if Texture Library "
                    "(.TLB) above is set, or if a real .RRI is found and used",
        items=[
            ("AUTO", "Auto (no filter)", "Score every .TLB in the folder - the original, unfiltered auto-detect behavior"),
            ("DESERT", "Desert", "Only consider .TLB files named Desert*"),
            ("ITALY", "Italy", "Only consider .TLB files named Italy*"),
            ("NORMANDY", "Normandy", "Only consider .TLB files named Normandy*"),
            ("CUSTOM_A", "Custom A", "Only consider .TLB files named CustomA*"),
            ("CUSTOM_B", "Custom B", "Only consider .TLB files named CustomB*"),
            ("CUSTOM_C", "Custom C", "Only consider .TLB files named CustomC*"),
        ],
        default="AUTO",
    )

    use_colorkey: BoolProperty(
        name="Color-Key Transparency",
        description="Treat one reserved color as transparent instead of solid, matching "
                    "a real 1999-era engine convention - confirmed on a real model "
                    "(6pdr.RRF/Desert2.tlb) where wheel-spoke gaps sample exact pure "
                    "white while the rest of the same part samples normal paint colors. "
                    "Turn off for content where the key color is itself meaningful paint",
        default=True,
    )

    colorkey_color: FloatVectorProperty(
        name="Key Color",
        description="The reserved color treated as transparent when Color-Key "
                    "Transparency is on. Default (white) is the confirmed real "
                    "convention for base-game content - some other content (e.g. "
                    "PP2-X-sourced libraries) reportedly uses bright pink/magenta "
                    "instead, so this is a per-import setting, not a fixed constant",
        subtype="COLOR",
        size=3,
        default=(1.0, 1.0, 1.0),
        min=0.0,
        max=1.0,
    )

    apply_real_world_scale: BoolProperty(
        name="Apply Real-World Scale",
        description="Scale the imported model by the real PE-units-to-meters "
                    "conversion factor (0.15625, i.e. 64 PE units = 10 metres - taken "
                    "straight from the real engine source, see PE_TO_METERS_SCALE's "
                    "own header) - without this, a raw import comes out roughly 6-9x "
                    "too big on every axis compared to real-world/Godot scale. "
                    "Turn off only if you specifically want the model in PE's own raw "
                    "internal units",
        default=True,
    )

    snap_to_ground: BoolProperty(
        name="Snap to Ground (Z=0)",
        description="Shift the whole model up/down so its own lowest point sits "
                    "exactly at Z=0, matching the convention every other real vehicle "
                    "in this project already uses. Without this, a fresh import's "
                    "pivot can sit well off the model's own ground contact point "
                    "(confirmed on a real KV-2 import: 0.59m too high) - whatever "
                    "raw pivot the original PE artist happened to author it with, not "
                    "necessarily ground level",
        default=True,
    )

    flip_to_positive_y_forward: BoolProperty(
        name="Flip to +Y Forward",
        description="Rotate the model 180 degrees around Z so its gun/hull points "
                    "+Y instead of -Y. A raw import comes out facing -Y - confirmed "
                    "wrong against every real working vehicle already in this project "
                    "(KV-1 and Pz4H's own already-correct models both point +Y, "
                    "cross-checked independently). Turn off only if you specifically "
                    "want PE's own raw facing convention preserved",
        default=True,
    )

    def execute(self, context):
        try:
            parts = read_rrf(self.filepath)
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}

        slot_sources = None
        detect_msg = ""
        tlb_confidence = None
        low_confidence_warning = None

        if self.tlb_filepath:
            try:
                tlb_parts = read_tlb(self.tlb_filepath)
                atlas_image_path = find_atlas_image(self.tlb_filepath)
                if atlas_image_path is None:
                    self.report({"WARNING"}, "No matching _24.BMP/_8.BMP found next to the .TLB - importing geometry only")
                else:
                    slot_sources = {0: (tlb_parts, atlas_image_path, self.tlb_filepath)}
                    tlb_confidence = "manual"
            except Exception as e:
                self.report({"WARNING"}, f"Could not read .TLB ({e}) - importing geometry only")
        elif self.use_rri and find_rri_path(self.filepath, default_texture_folder(self.filepath)):
            rri_path = find_rri_path(self.filepath, default_texture_folder(self.filepath))
            try:
                rri_slots = read_rri(rri_path)
                slot_sources = resolve_rri_libraries(rri_slots, self.filepath)
                missing = len(rri_slots) - len(slot_sources)
                detect_msg = f" - used {os.path.basename(rri_path)} ({len(slot_sources)}/{len(rri_slots)} listed libraries found on disk)"
                if not slot_sources:
                    detect_msg += " (none resolved - importing geometry only)"
                    slot_sources = None
                else:
                    tlb_confidence = "rri"
                    # An .RRI can be structurally incapable of naming every slot a model
                    # uses: the 8- and 16-slot variants cannot express slots 16-31 at all,
                    # yet real models reference them (a Tiger1 here has 289 faces in slot
                    # 16 against a 16-slot RRI, which left 255 of them unresolved). Where
                    # the RRI is silent about a slot the faces genuinely use, infer just
                    # that slot from the texture folder instead of falling back to
                    # first-match-anywhere. The RRI still wins for every slot it names.
                    used_slots = set()
                    for _p in parts:
                        for _t in (_p.face_texture_id or []):
                            if _t is not None:
                                used_slots.add(decode_texture_offset(_t)[1])
                    unnamed = sorted(used_slots - set(slot_sources))
                    if unnamed:
                        folder = default_texture_folder(self.filepath)
                        if folder:
                            inferred, inf_report = assign_libraries_to_slots(folder, parts)
                            filled = []
                            for sl in unnamed:
                                if sl in inferred:
                                    slot_sources[sl] = inferred[sl]
                                    filled.append(sl)
                            if filled:
                                detect_msg += (" + inferred slot(s) %s the .RRI cannot name"
                                               % ", ".join(str(x) for x in filled))

                    # A named slot can still come up short. The .RRI names the right
                    # library for the slot, yet some of that slot's faces use ids the
                    # library does not contain - a real Normandy M4a3 has one such id (23)
                    # shared by 38 faces, which left them magenta while the model rendered
                    # correctly everywhere else.
                    #
                    # The game never hits this because it loads the theatre's libraries as
                    # a SET, so any of them can supply an id. Rather than widen every .RRI
                    # to list the whole set - which makes ObjEdit load libraries it does
                    # not need, and trip its own "Texture ID Too High!" check on REDUX
                    # libraries that contain ids above 2047 - keep the .RRI narrow and add
                    # the extra libraries here, parked at spare high keys where they can
                    # never be mistaken for a real slot. Resolution tries the face's own
                    # slot first, so the .RRI still wins wherever it can answer.
                    unresolved_ids = set()
                    for _p in parts:
                        for _t in (_p.face_texture_id or []):
                            if _t is None:
                                continue
                            _u, _sl, _pid = decode_texture_offset(_t)
                            src = slot_sources.get(_sl)
                            if src and _pid not in src[0]:
                                unresolved_ids.add(_pid)
                    if unresolved_ids:
                        folder = default_texture_folder(self.filepath)
                        if folder:
                            try:
                                extra, _rep = assign_libraries_to_slots(folder, parts)
                            except Exception:
                                extra = {}
                            spare = 1000
                            added = 0
                            still = set(unresolved_ids)
                            candidates = [entry for _sl, entry in sorted(extra.items())]
                            # assign_libraries_to_slots() only proposes ONE library per
                            # slot the model uses, so for a single-slot model it can hand
                            # back the very library that is missing the id. Widen the
                            # search to every .TLB in the folder - m4a3e2 and M4a3 are both
                            # short exactly one id (23), yet only M4a3 happened to get a
                            # usable proposal, which made the fallback look like it worked.
                            try:
                                for _name in sorted(os.listdir(folder)):
                                    if not _name.lower().endswith(".tlb"):
                                        continue
                                    _path = os.path.join(folder, _name)
                                    try:
                                        _parts_tlb = read_tlb(_path)
                                    except Exception:
                                        continue
                                    if still & set(_parts_tlb):
                                        candidates.append(
                                            (_parts_tlb, find_atlas_image(_path), _path))
                            except OSError:
                                pass
                            for entry in candidates:
                                if not entry or not entry[0]:
                                    continue
                                covers = still & set(entry[0])
                                if not covers:
                                    continue
                                if not entry[1]:
                                    continue  # no atlas bitmap - cannot paint from it
                                while spare in slot_sources:
                                    spare += 1
                                slot_sources[spare] = entry
                                spare += 1
                                added += 1
                                still -= covers
                                if not still:
                                    break
                            if added:
                                detect_msg += (" + %d fallback librar%s for %d id(s) the "
                                               "named library lacks"
                                               % (added, "y" if added == 1 else "ies",
                                                  len(unresolved_ids)))
            except Exception as e:
                self.report({"WARNING"}, f"Could not read .RRI ({e}) - falling back")

        if slot_sources is None and not self.tlb_filepath:
            search_folder = self.tlb_search_folder or default_texture_folder(self.filepath)
            auto_derived = not self.tlb_search_folder and search_folder is not None
            if search_folder:
                unique_ids = sorted({t for part in parts for t in part.face_texture_id if t is not None})
                name_prefix = THEATRE_PREFIXES.get(self.theatre)
                matches, confidence, confidence_reason = find_matching_tlbs(search_folder, unique_ids, name_prefix=name_prefix)
                origin_note = " (auto-found sibling Texture folder)" if auto_derived else ""
                theatre_note = f", theatre={self.theatre.replace('_', ' ').title()}" if name_prefix else ""
                if not matches:
                    detect_msg = f" - auto-detect{origin_note}{theatre_note} found no good TLB match among {len(unique_ids)} unique texture ID(s)"
                else:
                    # Assign libraries to the slots the faces actually name, rather than
                    # numbering the score-ordered matches 0,1,2... A face's slot is
                    # meaningless under that old numbering, so a multi-library model came
                    # in fully textured but with textures from the wrong libraries.
                    built, slot_report = assign_libraries_to_slots(
                        search_folder, parts, name_prefix=name_prefix)

                    # THEATRE RULE FIRST. The game does not search for libraries at all -
                    # a real install's Texture folder is numbered per theatre
                    # (Normandy1..6, Italy1..6, Desert1..8) and the game loads that set in
                    # order, so a face's slot is an index into it: slot N ->
                    # <Theatre>(N+1).TLB. Id-overlap scoring is actively misleading next to
                    # this, because many libraries share ids so a 100% score is not
                    # evidence: on a real Normandy M4a3 (slot 1) scoring picked Italy5.TLB
                    # at 100% and produced brown/white garbage, while the rule picks
                    # Normandy2.tlb at 98% and produces a correct Sherman. Scored matches
                    # are kept for any slot the rule cannot fill (buildings, for instance,
                    # reference libraries that are not in Texture/ at all).
                    theatre_for_path = name_prefix or theatre_prefix_from_path(self.filepath)
                    rule_used = {}
                    if theatre_for_path:
                        rule_built, rule_report = theatre_set_libraries(
                            search_folder, parts, theatre_for_path)
                        if rule_built:
                            for slot, entry in rule_built.items():
                                built[slot] = entry
                                rule_used[slot] = entry[2]
                            slot_report = list(slot_report) + list(rule_report)
                    skipped_no_atlas = []
                    # Per-slot assignment gets each face's OWN library right, but it picks
                    # only one library per used slot and so can cover fewer ids overall
                    # than the score-ranked list. Keep the score-ranked matches too, parked
                    # at spare high keys where they can never be mistaken for a real slot -
                    # resolution tries the face's own slot first and falls back to these,
                    # so coverage is never worse than before. (Caught by regression: Is2-0
                    # went from 0 to 422 unresolved faces with per-slot assignment alone.)
                    already = {id(v[0]) for v in built.values()}
                    spare = 1000
                    for path, tlb_parts, atlas_image_path, score in matches:
                        if atlas_image_path is None:
                            skipped_no_atlas.append(os.path.basename(path))
                            continue
                        if any(v[2] == path for v in built.values()):
                            continue
                        built[spare] = (tlb_parts, atlas_image_path, path)
                        spare += 1
                    if slot_report:
                        self.report({"INFO"}, "Library slots: " + "; ".join(slot_report[:8]))
                    names = ", ".join(os.path.basename(path) for path, *_ in matches)
                    detect_msg = f" - auto-detected {len(matches)} .TLB(s){origin_note}{theatre_note}: {names}"
                    # Report what was actually USED, not just what scoring shortlisted -
                    # naming only the scored matches was misleading once the theatre rule
                    # started overriding them (it reported "Italy5.TLB" on a model it had
                    # correctly painted from Normandy2.tlb).
                    if rule_used:
                        rule_names = ", ".join(
                            f"slot {s}={os.path.basename(p)}" for s, p in sorted(rule_used.items()))
                        detect_msg += f" | theatre rule ({theatre_for_path}) took precedence: {rule_names}"
                    if skipped_no_atlas:
                        self.report({"WARNING"}, f"No matching _24.BMP/_8.BMP for: {', '.join(skipped_no_atlas)} - those libraries skipped")
                    if built:
                        slot_sources = built
                        # _classify_tlb_confidence() always returns "low" for the pure
                        # auto-detect path (see its docstring - a clean-looking score
                        # has still been wrong in this project's own real testing, so
                        # auto-detect alone never earns "high" here; only a real .RRI
                        # or an explicit manual tlb_filepath does). Cross-check the top
                        # candidate against sibling theatre-variant copies of the same-
                        # named .RRF for extra context - reported neutrally (just the
                        # percentages), since a low-confidence score can come from a
                        # close runner-up within *this* folder rather than genuine
                        # cross-copy inconsistency, and the two aren't the same signal
                        # (confirmed on Pz4E: the cross-check came back a consistent
                        # 100%/100%, while the real reason for low confidence was five
                        # other libraries scoring 98% right behind the top pick within
                        # this one folder).
                        tlb_confidence = "auto_low"
                        top_path = matches[0][0]
                        cross = cross_check_tlb_across_variants(self.filepath, top_path)
                        cross_note = ""
                        if cross:
                            pct = [100 * r // t if t else 0 for _, r, t in cross]
                            spread = max(pct) - min(pct)
                            pct_text = ", ".join(f"{p}%" for p in pct)
                            consistency = "inconsistent" if spread > 20 else "consistent"
                            cross_note = f" (cross-checked against {len(cross)} sibling copy/copies: {pct_text} resolved - {consistency})"
                        low_confidence_warning = (
                            f"Auto-detect is NOT confident about '{os.path.basename(top_path)}' - "
                            f"{confidence_reason}{cross_note}. Verify against a real .RRI or in-game "
                            f"before trusting this texture (see TEXTURE_ID_RESOLUTION.md)."
                        )

        root_name = os.path.splitext(os.path.basename(self.filepath))[0]
        collection = bpy.data.collections.new(root_name)
        context.scene.collection.children.link(collection)

        objects, resolved_count, unresolved_count = build_blender_objects(
            parts, collection, root_name, slot_sources, rrf_filepath=self.filepath, tlb_confidence=tlb_confidence,
            use_colorkey=self.use_colorkey, colorkey_color=tuple(self.colorkey_color),
        )

        # Real scale + ground-snap fix (2026-08-07) - see apply_real_world_scale's/
        # snap_to_ground's own property headers and PE_TO_METERS_SCALE's own header
        # for the full real derivation/citations. Only root-level objects (parent is
        # None) need touching - Blender's transform hierarchy means every child
        # inherits its parent's world scale/position automatically, and glTF/Godot
        # both handle nested transforms natively (no need to bake anything into
        # individual child objects or their mesh data).
        #
        # Real bug found live and fixed here (not just in the scale value): some real
        # imports have MANY independent root objects, not one hull root with a clean
        # hierarchy - e.g. a real KV2-0 import has 12 roots (AAMG/Comander/radio/
        # Turret_MG1/etc, small detail parts each imported as their own unparented
        # object, still positioned at real raw-unit coordinates relative to the whole
        # vehicle - confirmed live: one sat 17 raw units from the hull root). Scaling
        # only .scale shrinks each part's OWN geometry correctly but leaves .location
        # untouched, so the part would render correctly-SIZED but still scattered far
        # from the hull instead of attached to it. Scaling .location by the same
        # factor too (every independent root implicitly shares the world origin as
        # their common pivot) shrinks the whole assembly together correctly.
        root_objects = [o for o in objects if o.parent is None]
        if self.apply_real_world_scale:
            for obj in root_objects:
                obj.scale = (PE_TO_METERS_SCALE, PE_TO_METERS_SCALE, PE_TO_METERS_SCALE)
                obj.location = (obj.location.x * PE_TO_METERS_SCALE,
                                 obj.location.y * PE_TO_METERS_SCALE,
                                 obj.location.z * PE_TO_METERS_SCALE)

        # Real facing fix (2026-08-07) - a raw import comes out with its gun/hull
        # pointing -Y, confirmed wrong against every real working vehicle already in
        # this project (KV-1's Kv176.glb and Pz4H's Pzr4h.glb both independently
        # checked - both point +Y). 180 degrees around Z transforms position
        # (x, y, z) -> (-x, -y, z) as well as rotating the object itself - same
        # "every independent root implicitly shares the world origin" reasoning as
        # the scale fix above, so this stays correct even for a model with more than
        # one true root.
        if self.flip_to_positive_y_forward:
            for obj in root_objects:
                obj.rotation_euler.z += math.pi
                obj.location.x = -obj.location.x
                obj.location.y = -obj.location.y

        if self.snap_to_ground:
            # Real bug found live: matrix_world isn't recomputed synchronously just
            # from setting .location/.scale above - reading it immediately afterward
            # (without this) returns the STALE pre-scale/pre-relocate transform, so
            # the ground-snap offset gets computed from the wrong (much larger, raw-
            # unit) bounding box - confirmed live: a real KV2 import came out with
            # lowest_z=3.5 instead of ~0 without this update.
            context.view_layer.update()
            corners = []
            for obj in objects:
                if obj.type == "MESH":
                    corners.extend(obj.matrix_world @ Vector(c) for c in obj.bound_box)
            if corners:
                lowest_z = min(c.z for c in corners)
                for obj in root_objects:
                    obj.location.z -= lowest_z

        msg = f"Imported {len(parts)} part(s) from {root_name}.rrf" + detect_msg
        if slot_sources is not None:
            msg += f" - {resolved_count} face(s) textured, {unresolved_count} unresolved"
        if low_confidence_warning:
            self.report({"WARNING"}, low_confidence_warning)
        if unresolved_count:
            msg += " (marked magenta / PE_UNRESOLVED_TEXTURE material - re-texture by hand)"
            self.report({"WARNING"}, msg)
        else:
            self.report({"INFO"}, msg)
        return {"FINISHED"}


class EXPORT_OT_rrf_model(bpy.types.Operator, ExportHelper):
    """Exports the selected Panzer Elite part(s) to a .RRF file.

    This writes to a NEW file, leaving the model you imported from untouched - it copies
    that source .RRF to the chosen path first, then applies each selected object's
    current Blender mesh into its own part. Geometry added or deleted in Blender is
    included (see MESH_OT_pe_write_mesh for exactly what is carried forward and what is
    built fresh).

    Real limitation, stated rather than hidden: this is not "save any Blender object as a
    .RRF". Every exported object must have been imported from a .RRF by this add-on -
    that source file supplies the part hierarchy, pivots, gameplay attributes and texture
    assignments that a mesh alone does not carry. Authoring a model from nothing is
    scoped in docs/AUTHORING_SCOPING.md and is not built. All selected objects must come
    from the SAME source file, since a .RRF holds one model's whole part hierarchy.

    The matching .TLB is not written here: an exported model keeps referring to whichever
    texture library it already used. To give a part its own paintable library, run
    "PE: Give This Part a Private Skin" before exporting."""

    bl_idname = "export_scene.pe_rrf_model"
    bl_label = "Export Panzer Elite Model (.rrf)"
    bl_options = {"REGISTER", "UNDO"}
    filename_ext = ".rrf"
    filter_glob: StringProperty(default="*.rrf;*.RRF", options={"HIDDEN"})

    selected_only: BoolProperty(
        name="Selected Objects Only",
        description="Export only selected parts. Turn off to export every part imported "
                    "from the same source file, whether selected or not - usually what "
                    "you want, since a .RRF holds the whole vehicle",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return any(o.type == "MESH" and "pe_rrf_filepath" in o for o in context.scene.objects)

    def execute(self, context):
        candidates = [o for o in context.scene.objects
                      if o.type == "MESH" and "pe_rrf_filepath" in o and "pe_part_index" in o]
        if self.selected_only:
            candidates = [o for o in candidates if o.select_get()]
        if not candidates:
            self.report({"ERROR"}, "No imported Panzer Elite parts to export")
            return {"CANCELLED"}

        sources = {o["pe_rrf_filepath"] for o in candidates}
        if len(sources) > 1:
            self.report({"ERROR"},
                        "Selected parts come from %d different .RRF files (%s) - a .RRF holds one "
                        "model's whole hierarchy, so they cannot be written to one file"
                        % (len(sources), ", ".join(sorted(os.path.basename(p) for p in sources))))
            return {"CANCELLED"}
        source = sources.pop()

        if os.path.abspath(source) == os.path.abspath(self.filepath):
            self.report({"ERROR"}, "Refusing to overwrite the source model - choose a different "
                                   "filename, or use the Edit Mode 'PE: Write Mesh to .RRF' "
                                   "operator to edit it in place")
            return {"CANCELLED"}

        try:
            rrf_data = read_rrf_raw(source)
        except OSError as e:
            self.report({"ERROR"}, "Could not read the source model %s: %s" % (source, e))
            return {"CANCELLED"}

        written, total_added, total_removed = 0, 0, 0
        stale_boxes = []
        for obj in sorted(candidates, key=lambda o: o["pe_part_index"]):
            bm = bmesh.new()
            try:
                bm.from_mesh(obj.data)
                bm.faces.ensure_lookup_table()
                bm.verts.ensure_lookup_table()
                rrf_data, stats = write_object_mesh_into_rrf(
                    rrf_data, obj, bm, obj["pe_part_index"])
            except (ValueError, struct.error) as e:
                self.report({"ERROR"}, str(e) + " - nothing written")
                return {"CANCELLED"}
            finally:
                bm.free()
            written += 1
            total_added += stats["added"]
            total_removed += stats["removed"]
            if stats.get("bounding_stale"):
                stale_boxes.append(obj.name)

        try:
            write_rrf_raw(self.filepath, rrf_data)
        except OSError as e:
            self.report({"ERROR"}, "Could not write %s: %s" % (self.filepath, e))
            return {"CANCELLED"}

        if stale_boxes:
            self.report({"WARNING"},
                        "%d part(s) have a custom collision box that no longer contains their "
                        "geometry (%s) - preserved rather than overwritten. Regenerate in ObjEdit "
                        "(Bounding Box > Gen) if the new shape should collide."
                        % (len(stale_boxes), ", ".join(stale_boxes[:4])))
        self.report({"INFO"},
                    "Exported %d part(s) (+%d face(s) added, -%d deleted) to %s - source model "
                    "untouched" % (written, total_added, total_removed,
                                   os.path.basename(self.filepath)))
        return {"FINISHED"}


def menu_func_import(self, context):
    self.layout.operator(IMPORT_OT_rrf.bl_idname, text="Panzer Elite Model (.rrf)")


class EXPORT_OT_rrf_atlas(bpy.types.Operator, ExportHelper):
    """Save a texture atlas Image back out as an 8-bit indexed .BMP the game actually
    reads.

    Covers "repaint existing regions" only (see docs/PAINT_AND_EXPORT_SCOPING.md in the
    project repo): this does NOT touch the .RRF or .TLB at all. An earlier version of
    this operator wrote a 24-bit "<name>_24.BMP" on the assumption the game's loader
    prefers it over the paletted "_8.BMP" fallback - confirmed wrong against a real
    running install, twice independently (see PAINT_AND_EXPORT_SCOPING.md): the game
    silently kept reading the original _8.BMP regardless, with no crash or error to
    suggest anything was even attempted. This writes the format confirmed to actually
    work instead - the repainted RGB pixels are quantized against the exact 256-color
    palette the model's real _8.BMP already uses (read fresh from that file, not
    reconstructed), so repainted colors land on their nearest available palette entry.
    That's an unavoidable consequence of the paletted format the game reads, not a bug
    here. Adding genuinely new texture regions (new UV layout, new .TLB entries) is a
    separate, bigger job - not covered here.
    """
    bl_idname = "export_scene.pe_rrf_atlas"
    bl_label = "Export Panzer Elite Texture Atlas (.bmp)"
    bl_options = {"REGISTER"}

    filename_ext = ".bmp"
    filter_glob: StringProperty(default="*.bmp", options={"HIDDEN"})

    # Operators can't register a PointerProperty straight to an ID datablock (Image), so
    # this is a plain name string with a proper search-dropdown drawn in draw() instead.
    image_name: StringProperty(
        name="Atlas Image",
        description="The texture atlas Image to save out - the one you were painting "
                    "on in Texture Paint. Every model sharing this atlas will see the "
                    "change once this file replaces the original <name>_8.BMP, so "
                    "double-check you're not overwriting an atlas other vehicles still "
                    "rely on unless that's what you intend",
    )

    def draw(self, context):
        self.layout.prop_search(self, "image_name", bpy.data, "images", text="Atlas Image")

    def invoke(self, context, event):
        if not self.image_name:
            active_mat = getattr(context.active_object, "active_material", None)
            if active_mat is not None and active_mat.use_nodes:
                for node in active_mat.node_tree.nodes:
                    if node.type == "TEX_IMAGE" and node.image is not None:
                        self.image_name = node.image.name
                        break
        if self.image_name:
            base = os.path.splitext(self.image_name)[0]
            if base.endswith("_24"):
                base = base[:-3]
            elif base.endswith("_8"):
                base = base[:-2]
            self.filepath = base + "_8.bmp"
        return super().invoke(context, event)

    def execute(self, context):
        image = bpy.data.images.get(self.image_name)
        if image is None:
            self.report({"ERROR"}, "No image selected - pick the atlas Image you painted on")
            return {"CANCELLED"}

        if tuple(image.size) != ATLAS_EXPECTED_SIZE:
            self.report(
                {"WARNING"},
                f"'{image.name}' is {image.size[0]}x{image.size[1]}, "
                f"not the expected {ATLAS_EXPECTED_SIZE[0]}x{ATLAS_EXPECTED_SIZE[1]} - "
                f"saving anyway, but the game may not read a resized atlas correctly",
            )

        tlb_filepath = image.get("pe_tlb_filepath")
        source_bmp8 = find_source_bmp8(tlb_filepath) if tlb_filepath else None
        if source_bmp8 is None:
            self.report(
                {"ERROR"},
                "Could not find the original _8.BMP to read its palette from (no "
                "pe_tlb_filepath recorded on this image, or no matching _8.BMP next to "
                "its .TLB) - quantizing needs that palette, so this can't proceed",
            )
            return {"CANCELLED"}

        try:
            palette = read_bmp8_palette(source_bmp8)
        except Exception as e:
            self.report({"ERROR"}, f"Could not read palette from '{source_bmp8}': {e}")
            return {"CANCELLED"}

        import numpy as np
        w, h = image.size
        pixels = np.empty(w * h * 4, dtype=np.float32)
        image.pixels.foreach_get(pixels)
        pixels = pixels.reshape(h, w, 4)
        rgb = np.clip(pixels[:, :, :3] * 255.0 + 0.5, 0, 255).astype(np.uint8)
        indices = quantize_to_palette(rgb, palette)

        filepath = self.filepath
        if not filepath.lower().endswith(".bmp"):
            filepath += ".bmp"
        write_bmp8(filepath, indices, palette)

        self.report(
            {"INFO"},
            f"Saved '{image.name}' ({w}x{h}) as an 8-bit indexed BMP to {filepath}, "
            f"quantized against {os.path.basename(source_bmp8)}'s palette - place it "
            f"next to the .TLB as <name>_8.BMP for the game to pick it up",
        )
        return {"FINISHED"}


def menu_func_export(self, context):
    self.layout.operator(EXPORT_OT_rrf_atlas.bl_idname, text="Panzer Elite Texture Atlas (.bmp)")
    self.layout.operator(EXPORT_OT_rrf_model.bl_idname, text="Panzer Elite Model (.rrf)")


def _backup_once(filepath):
    """Copies filepath to filepath+'.bak' the first time this is called for it in a
    session where no .bak already exists - a one-time safety net before an operator
    writes over a real .RRF/.TLB in place, without repeatedly clobbering the backup on
    every subsequent edit (it should always reflect the state before ANY of this
    session's changes, not a rolling backup)."""
    backup_path = filepath + ".bak"
    if not os.path.isfile(backup_path):
        shutil.copy2(filepath, backup_path)


def _copy_atlas_region(image, old_posX, old_posY, new_posX, new_posY, sizeX, sizeY):
    """Copies a sizeX x sizeY pixel block within an atlas Image from one tile-grid
    position to another, byte-for-byte - used when detaching a face onto a freshly
    allocated .TLB entry, so the new cell starts out looking identical to the old one
    (only actually changes once repainted).

    Blender's own Image.pixels array is stored bottom-up (index 0 = image's bottom row),
    while posX/posY and the UV math in build_blender_objects() use a top-down "atlas_y"
    convention (see its `v = 1.0 - atlas_y / ATLAS_HEIGHT`) - each row is converted
    between the two independently here via `h - 1 - atlas_y`, so this is correct
    regardless of how far the block moves or in which direction."""
    import numpy as np

    w, h = image.size
    pixels = np.empty(w * h * 4, dtype=np.float32)
    image.pixels.foreach_get(pixels)
    pixels = pixels.reshape(h, w, 4)

    for dy in range(sizeY):
        src_row = h - 1 - (old_posY * ATLAS_TILE_SIZE + dy)
        dst_row = h - 1 - (new_posY * ATLAS_TILE_SIZE + dy)
        src_col = old_posX * ATLAS_TILE_SIZE
        dst_col = new_posX * ATLAS_TILE_SIZE
        pixels[dst_row, dst_col:dst_col + sizeX, :] = pixels[src_row, src_col:src_col + sizeX, :]

    image.pixels.foreach_set(pixels.reshape(-1))
    image.update()


class MESH_OT_pe_detach_face_texture(bpy.types.Operator):
    """Gives the selected face(s) their own private copy of the shared texture cell they
    currently point at, so repainting them no longer also repaints every other face that
    happens to share the same .TLB entry - the "detach face from shared texture cell"
    feature from TODO.md, wiring together find_free_atlas_space(), append_tlb_entry(),
    and patch_face_texture_id().

    Writes directly to the model's .RRF and whichever .TLB library the selected face(s)
    resolved through, with a one-time .bak backup made automatically before the first
    edit to either file this session (see _backup_once()) - this is a real, hard-to-
    reverse-by-hand edit to the actual asset files, not just an in-memory Blender change.
    """
    bl_idname = "mesh.pe_detach_face_texture"
    bl_label = "PE: Detach Face From Shared Texture Cell"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (
            obj is not None
            and obj.type == "MESH"
            and obj.mode == "EDIT"
            and "pe_rrf_filepath" in obj
            and "pe_part_index" in obj
        )

    def execute(self, context):
        obj = context.active_object
        mesh = obj.data
        rrf_filepath = obj["pe_rrf_filepath"]
        part_index = obj["pe_part_index"]

        bm = bmesh.from_edit_mesh(mesh)
        selected_faces = [f for f in bm.faces if f.select]
        if not selected_faces:
            self.report({"WARNING"}, "No faces selected")
            return {"CANCELLED"}

        uv_layer = bm.loops.layers.uv.active
        if uv_layer is None:
            self.report({"ERROR"}, "Mesh has no UV layer - nothing to detach")
            return {"CANCELLED"}

        try:
            rrf_data = read_rrf_raw(rrf_filepath)
        except OSError as e:
            self.report({"ERROR"}, f"Could not read {rrf_filepath}: {e}")
            return {"CANCELLED"}

        tlb_cache = {}   # tlb_filepath -> TLBLibrary, loaded once and written once at the end
        tlb_dirty = set()
        detached_count = 0
        skipped_count = 0

        for face in selected_faces:
            face_index = face.index
            try:
                old_texture_id = read_face_texture_id(rrf_data, part_index, 0, face_index)
            except (IndexError, struct.error):
                skipped_count += 1
                continue

            material_index = face.material_index
            if material_index >= len(mesh.materials) or mesh.materials[material_index] is None:
                skipped_count += 1
                continue
            material = mesh.materials[material_index]
            image = next(
                (n.image for n in material.node_tree.nodes if n.type == "TEX_IMAGE" and n.image is not None),
                None,
            ) if material.use_nodes else None
            if image is None or "pe_tlb_filepath" not in image:
                self.report({"WARNING"}, f"Face {face_index}: material has no traceable .TLB source - skipped")
                skipped_count += 1
                continue
            tlb_filepath = image["pe_tlb_filepath"]

            library = tlb_cache.get(tlb_filepath)
            if library is None:
                try:
                    library = read_tlb_library(tlb_filepath)
                except (OSError, ValueError) as e:
                    self.report({"WARNING"}, f"Face {face_index}: could not read {tlb_filepath}: {e}")
                    skipped_count += 1
                    continue
                tlb_cache[tlb_filepath] = library

            old_entry_id = old_texture_id % TLB_MAX_PARTS
            old_entry = next((e for e in library.entries if e.id == old_entry_id), None)
            if old_entry is None:
                self.report({"WARNING"}, f"Face {face_index}: texture id {old_texture_id} doesn't resolve to any entry in {tlb_filepath} - skipped")
                skipped_count += 1
                continue

            free = find_free_atlas_space(library, old_entry.sizeX, old_entry.sizeY)
            if free is None:
                self.report({"WARNING"}, f"Face {face_index}: no free {old_entry.sizeX}x{old_entry.sizeY} space left in {tlb_filepath} - skipped")
                skipped_count += 1
                continue
            new_posX, new_posY = free

            new_id = append_tlb_entry(
                library, sizeX=old_entry.sizeX, sizeY=old_entry.sizeY,
                posX=new_posX, posY=new_posY, cutX=old_entry.cutX, cutY=old_entry.cutY,
                filename=old_entry.filename,
            )
            tlb_dirty.add(tlb_filepath)

            _copy_atlas_region(image, old_entry.posX, old_entry.posY, new_posX, new_posY, old_entry.sizeX, old_entry.sizeY)

            patch_face_texture_id(rrf_data, part_index, 0, face_index, new_id)

            # Only this face's UV needs shifting to the new cell - the pixel offsets
            # *within* the cell (what the corners actually encode, see RRF_FORMAT.md)
            # don't change, only the cell's own base position does.
            delta_u = (new_posX - old_entry.posX) * ATLAS_TILE_SIZE / ATLAS_WIDTH
            delta_v = -(new_posY - old_entry.posY) * ATLAS_TILE_SIZE / ATLAS_HEIGHT
            for loop in face.loops:
                uv = loop[uv_layer].uv
                loop[uv_layer].uv = (uv.x + delta_u, uv.y + delta_v)

            detached_count += 1

        if detached_count:
            _backup_once(rrf_filepath)
            write_rrf_raw(rrf_filepath, rrf_data)
            for dirty_path in tlb_dirty:
                _backup_once(dirty_path)
                write_tlb_library(dirty_path, tlb_cache[dirty_path])
            bmesh.update_edit_mesh(mesh)

        msg = f"Detached {detached_count} face(s) onto their own texture cell(s)"
        if skipped_count:
            msg += f", skipped {skipped_count}"
        if detached_count:
            self.report({"INFO"}, msg)
            return {"FINISHED"}
        self.report({"WARNING"}, msg or "Nothing detached")
        return {"CANCELLED"}


class MESH_OT_pe_set_face_crop(bpy.types.Operator):
    """Writes the selected face(s)' *current* Blender UV position back into the .RRF as
    real per-face crop corners (patch_face_corners()), instead of the all-zero "use the
    whole entry" fallback every face starts with. Move/scale a face's UV within its
    assigned texture cell in Blender's own UV editor, then run this to persist that exact
    crop back to the file - the write-side counterpart to how the importer builds UVs
    from corners in the first place (build_blender_objects()'s atlas_x/atlas_y <-> u/v
    transform, inverted here).

    Only repositions the crop *within* the face's already-assigned .TLB entry - it does
    not reassign which entry/library a face uses (see MESH_OT_pe_detach_face_texture for
    that). Does not support non-rectangular UV shapes: whatever shape the face's UV loops
    describe, only their axis-aligned bounding rectangle is written, since the file
    format only ever stores one rectangle per face (RRF_FORMAT.md) - a face UV'd as a
    rotated or non-rectangular shape in Blender will be cropped to its bounding box, not
    reproduced exactly.
    """
    bl_idname = "mesh.pe_set_face_crop"
    bl_label = "PE: Write Face Crop From UV"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (
            obj is not None
            and obj.type == "MESH"
            and obj.mode == "EDIT"
            and "pe_rrf_filepath" in obj
            and "pe_part_index" in obj
        )

    def execute(self, context):
        obj = context.active_object
        mesh = obj.data
        rrf_filepath = obj["pe_rrf_filepath"]
        part_index = obj["pe_part_index"]

        bm = bmesh.from_edit_mesh(mesh)
        selected_faces = [f for f in bm.faces if f.select]
        if not selected_faces:
            self.report({"WARNING"}, "No faces selected")
            return {"CANCELLED"}

        uv_layer = bm.loops.layers.uv.active
        if uv_layer is None:
            self.report({"ERROR"}, "Mesh has no UV layer - nothing to write")
            return {"CANCELLED"}

        try:
            rrf_data = read_rrf_raw(rrf_filepath)
        except OSError as e:
            self.report({"ERROR"}, f"Could not read {rrf_filepath}: {e}")
            return {"CANCELLED"}

        tlb_cache = {}
        updated_count = 0
        skipped_count = 0

        for face in selected_faces:
            face_index = face.index
            try:
                texture_id = read_face_texture_id(rrf_data, part_index, 0, face_index)
            except (IndexError, struct.error):
                skipped_count += 1
                continue

            material_index = face.material_index
            if material_index >= len(mesh.materials) or mesh.materials[material_index] is None:
                skipped_count += 1
                continue
            material = mesh.materials[material_index]
            image = next(
                (n.image for n in material.node_tree.nodes if n.type == "TEX_IMAGE" and n.image is not None),
                None,
            ) if material.use_nodes else None
            if image is None or "pe_tlb_filepath" not in image:
                self.report({"WARNING"}, f"Face {face_index}: material has no traceable .TLB source - skipped")
                skipped_count += 1
                continue
            tlb_filepath = image["pe_tlb_filepath"]

            library = tlb_cache.get(tlb_filepath)
            if library is None:
                try:
                    library = read_tlb_library(tlb_filepath)
                except (OSError, ValueError) as e:
                    self.report({"WARNING"}, f"Face {face_index}: could not read {tlb_filepath}: {e}")
                    skipped_count += 1
                    continue
                tlb_cache[tlb_filepath] = library

            entry_id = texture_id % TLB_MAX_PARTS
            entry = next((e for e in library.entries if e.id == entry_id), None)
            if entry is None:
                self.report({"WARNING"}, f"Face {face_index}: texture id {texture_id} doesn't resolve to any entry in {tlb_filepath} - skipped")
                skipped_count += 1
                continue

            # Invert the same atlas_x/atlas_y <-> u/v transform build_blender_objects()
            # uses to place UVs from corners in the first place.
            xs, ys = [], []
            for loop in face.loops:
                u, v = loop[uv_layer].uv
                atlas_x = u * ATLAS_WIDTH
                atlas_y = (1.0 - v) * ATLAS_HEIGHT
                lx = atlas_x - entry.posX * ATLAS_TILE_SIZE
                ly = atlas_y - entry.posY * ATLAS_TILE_SIZE
                xs.append(max(0, min(255, round(lx))))
                ys.append(max(0, min(255, round(ly))))

            patch_face_corners(rrf_data, part_index, 0, face_index, min(xs), min(ys), max(xs), max(ys))
            updated_count += 1

        if updated_count:
            _backup_once(rrf_filepath)
            write_rrf_raw(rrf_filepath, rrf_data)

        msg = f"Wrote crop for {updated_count} face(s) from their current UV"
        if skipped_count:
            msg += f", skipped {skipped_count}"
        if updated_count:
            self.report({"INFO"}, msg)
            return {"FINISHED"}
        self.report({"WARNING"}, msg or "Nothing updated")
        return {"CANCELLED"}


class MESH_OT_pe_flip_face_texture(bpy.types.Operator):
    """Replicates the real ObjEdit's own per-face "Flip" tool - real content routinely
    has faces the original artist flipped this way, and there's no independently-
    readable flag anywhere in the file that says so (confirmed from the real engine
    source, see flip_face_texture_orientation()'s docstring) - so a face reconstructed
    from an all-zero-corner fallback (see docs/TEXTURE_ID_RESOLUTION.md) can come out
    with the right texture content but a mirrored/rotated orientation, with no way to
    detect that automatically at import time. This lets you fix it by hand, directly in
    Blender, the same way you'd use ObjEdit's own Flip button - no round-trip through
    ObjEdit needed.

    Swaps the UV of two of the face's own loops to preview the effect immediately
    (quad: loop 1 <-> loop 3; triangle: loop 1 <-> loop 2 - matching the real engine's
    v2<->textureHalf / v2<->v3 field swap exactly), then writes the same swap plus a
    face-normal negation directly to the .RRF, matching rrFlipObjFace() byte-for-byte
    so the file stays exactly as valid as any real tool-saved one."""

    bl_idname = "mesh.pe_flip_face_texture"
    bl_label = "PE: Flip Face Texture Orientation"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (
            obj is not None
            and obj.type == "MESH"
            and obj.mode == "EDIT"
            and "pe_rrf_filepath" in obj
            and "pe_part_index" in obj
        )

    def execute(self, context):
        obj = context.active_object
        mesh = obj.data
        rrf_filepath = obj["pe_rrf_filepath"]
        part_index = obj["pe_part_index"]

        bm = bmesh.from_edit_mesh(mesh)
        bm.faces.ensure_lookup_table()
        selected_faces = [f for f in bm.faces if f.select]
        if not selected_faces:
            self.report({"WARNING"}, "No faces selected")
            return {"CANCELLED"}

        uv_layer = bm.loops.layers.uv.active
        face_index_layer = bm.faces.layers.int.get("pe_face_index")
        if uv_layer is None or face_index_layer is None:
            self.report(
                {"ERROR"},
                "This mesh has no UV layer or no pe_face_index data - re-import with "
                "this plugin version before using this operator.",
            )
            return {"CANCELLED"}

        try:
            rrf_data = bytearray(read_rrf_raw(rrf_filepath))
        except OSError as e:
            self.report({"ERROR"}, f"Could not read {rrf_filepath}: {e}")
            return {"CANCELLED"}

        flipped_count = 0
        skipped_count = 0
        for face in selected_faces:
            loops = list(face.loops)
            if len(loops) == 4:
                a, b = 1, 3
            elif len(loops) == 3:
                a, b = 1, 2
            else:
                skipped_count += 1
                continue

            orig_face_index = face[face_index_layer]
            try:
                flip_face_texture_orientation(rrf_data, part_index, 0, orig_face_index)
            except (IndexError, struct.error):
                skipped_count += 1
                continue

            loops[a][uv_layer].uv, loops[b][uv_layer].uv = loops[b][uv_layer].uv, loops[a][uv_layer].uv
            flipped_count += 1

        if flipped_count:
            _backup_once(rrf_filepath)
            write_rrf_raw(rrf_filepath, bytes(rrf_data))
            bmesh.update_edit_mesh(mesh)

        msg = f"Flipped texture orientation on {flipped_count} face(s)"
        if skipped_count:
            msg += f", skipped {skipped_count}"
        if flipped_count:
            self.report({"INFO"}, msg)
            return {"FINISHED"}
        self.report({"WARNING"}, msg or "Nothing flipped")
        return {"CANCELLED"}


class MESH_OT_pe_give_private_skin(bpy.types.Operator):
    """Moves the active mesh part onto a brand-new, dedicated .TLB atlas it doesn't
    share with anything else in the game, so it can be freely repainted without any risk
    of also changing some other vehicle/object that happens to use the same shared
    library - the "give a whole vehicle its own private, freely-paintable skin" feature
    scoped in TODO.md.

    Requires a real UV unwrap already applied to the mesh (Smart UV Project is the
    intended one - this operator packs whatever islands are already there, it does not
    unwrap for you). Detects those UV islands, sizes each proportional to its own UV
    footprint, and packs them into a fresh empty atlas (plan_private_skin()); every face
    gets a new .TLB entry sized to fit its own island and a real per-face crop computed
    from its actual UV position (apply_private_skin() -> patch_face_corners()), not the
    old all-zero/full-rectangle placeholder every prior writer used.

    Writes a new dedicated `<name>_private.TLB` and a blank `<name>_private_8.BMP`
    (borrowing this part's own current real palette, so the blank canvas starts from
    genuine tank-paint colors rather than a guess) alongside the original .RRF, updates
    the .RRF itself in place (with the usual automatic .bak backup), and assigns the mesh
    a new material pointed at the fresh blank image so it's immediately ready to paint in
    Blender's Texture Paint mode - no re-import needed.

    Scope: one mesh part (one Blender object) at a time, matching how this project's
    models are actually structured (one object per .RRF part) - run it again on each
    part of a vehicle you want to give a full, all-over private skin. Does not attempt
    island-level UV unwrapping itself, and (like every writer in this project) doesn't
    touch any other object/file beyond the one it's run on.
    """
    bl_idname = "mesh.pe_give_private_skin"
    bl_label = "PE: Give This Part a Private Skin"
    bl_options = {"REGISTER", "UNDO"}

    per_face: BoolProperty(
        name="Rectangle Per Face",
        description="Give every face its own atlas rectangle instead of sharing one per UV "
                    "island. Islands overlap once faces are snapped to rectangles (which "
                    "the format requires), so painting one face bleeds into its neighbour. "
                    "Per-face costs atlas space and gives up continuous seams, and is how "
                    "stock PE content is authored",
        default=False,
    )

    budget_fraction: FloatProperty(
        name="Atlas Budget",
        description="How much of the 256x4096 atlas this part may claim. Leave at 0.6 for "
                    "a single part. When several parts will be MERGED into one library "
                    "later, divide by the number of parts (e.g. 0.12 for five) - otherwise "
                    "each part sizes its islands as if it owned the whole atlas and the "
                    "merge runs out of space",
        default=0.6, min=0.01, max=0.95,
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (
            obj is not None
            and obj.type == "MESH"
            and obj.mode == "EDIT"
            and "pe_rrf_filepath" in obj
            and "pe_part_index" in obj
            and obj.data.uv_layers.active is not None
        )

    def execute(self, context):
        obj = context.active_object
        mesh = obj.data
        rrf_filepath = obj["pe_rrf_filepath"]
        part_index = obj["pe_part_index"]

        try:
            import numpy as np
        except ImportError:
            self.report({"ERROR"}, "numpy not available - can't write the new atlas bitmap")
            return {"CANCELLED"}

        bm = bmesh.from_edit_mesh(mesh)
        uv_layer = bm.loops.layers.uv.active
        if uv_layer is None:
            self.report({"ERROR"}, "Mesh has no UV layer - unwrap it first (e.g. Smart UV Project)")
            return {"CANCELLED"}

        plans, size_warnings = plan_private_skin(bm, uv_layer,
                                                budget_fraction=self.budget_fraction,
                                                per_face=self.per_face)
        if not plans:
            self.report({"WARNING"}, "No faces to give a private skin")
            return {"CANCELLED"}

        try:
            rrf_data = read_rrf_raw(rrf_filepath)
        except OSError as e:
            self.report({"ERROR"}, f"Could not read {rrf_filepath}: {e}")
            return {"CANCELLED"}

        # Borrow a real palette from whatever this part's faces were using before this
        # operation, so the blank canvas starts from real tank-paint colors, not a guess.
        palette = None
        for mat in mesh.materials:
            if mat is None or not mat.use_nodes:
                continue
            image = next(
                (n.image for n in mat.node_tree.nodes if n.type == "TEX_IMAGE" and n.image is not None),
                None,
            )
            if image is not None and "pe_tlb_filepath" in image:
                source_bmp = find_source_bmp8(image["pe_tlb_filepath"])
                if source_bmp:
                    try:
                        palette = read_bmp8_palette(source_bmp)
                    except (OSError, ValueError):
                        palette = None
                    if palette:
                        break
        # No source BMP to borrow from (a part whose faces were never resolved, or a
        # freshly built one). Fall back to a real library's palette from the model's own
        # texture folder rather than greyscale - a greyscale palette makes every painted
        # colour come out grey in game, since the game reads the paletted 8-bit bitmap.
        palette_block = None
        if palette is None:
            texture_folder = default_texture_folder(rrf_filepath)
            palette_block, pal_src = find_theatre_palette(texture_folder)
            if palette_block is not None:
                palette = tlb_palette_to_rgb(palette_block)
                self.report({"INFO"}, "Borrowed a palette from " + os.path.basename(pal_src))
            else:
                palette = [(i, i, i) for i in range(256)]
                self.report({"WARNING"}, "No real palette found in the texture folder - falling "
                                         "back to greyscale, so painted colours will not survive")
        if palette_block is None:
            palette_block = rgb_to_tlb_palette(palette)

        # Give the new library the same palette as the bitmap beside it. Previously this
        # was left as 2048 zero bytes, so every private-skin .TLB shipped with an
        # all-black palette that disagreed with its own _8.BMP.
        library = new_tlb_library(palette=palette_block)
        try:
            updated = apply_private_skin(rrf_data, part_index, bm, uv_layer, plans, library)
        except ValueError as e:
            self.report({"ERROR"}, f"Packing failed: {e}")
            return {"CANCELLED"}
        bmesh.update_edit_mesh(mesh)

        base = os.path.splitext(rrf_filepath)[0]
        safe_name = "".join(c if c.isalnum() else "_" for c in mesh.name)
        tlb_path = f"{base}_{safe_name}_private.TLB"
        bmp_path = os.path.splitext(tlb_path)[0] + "_8.BMP"
        blank = np.zeros((ATLAS_HEIGHT, ATLAS_WIDTH), dtype=np.uint8)

        _backup_once(rrf_filepath)
        write_rrf_raw(rrf_filepath, rrf_data)
        write_tlb_library(tlb_path, library)
        write_bmp8(bmp_path, blank, palette)

        # A brand new, blank paint canvas has no real color-key content yet - applying
        # the same-color-is-transparent rule here would just punch the whole blank
        # canvas full of holes the moment it's created, not a real texture to preserve.
        new_material = _build_material(safe_name + "_private", bmp_path, tlb_filepath=tlb_path, tlb_confidence="manual", use_colorkey=False)
        mesh.materials.append(new_material)
        new_material_index = len(mesh.materials) - 1
        bm.faces.ensure_lookup_table()
        for plan in plans:
            for face_index in plan["faces"]:
                bm.faces[face_index].material_index = new_material_index
        bmesh.update_edit_mesh(mesh)

        self.report(
            {"INFO"},
            f"Gave {updated} face(s) a private skin across {len(library.entries)} island(s): "
            f"{os.path.basename(tlb_path)}" + (f" ({len(size_warnings)} island(s) clamped to max size)" if size_warnings else ""),
        )
        return {"FINISHED"}


def write_object_mesh_into_rrf(rrf_data, obj, bm, part_index):
    """Applies one Blender object's current mesh to `part_index` of an in-memory .RRF,
    returning (new_rrf_data, stats). Shared by the Edit Mode operator
    (MESH_OT_pe_write_mesh) and the File > Export path (EXPORT_OT_rrf_model) so both
    behave identically - the only difference between them is where the bmesh comes from
    and which file the result is written to.

    Raises ValueError with a user-facing message rather than writing anything
    questionable. See MESH_OT_pe_write_mesh's docstring for the full behaviour: original
    elements keep their real file data, new faces inherit a real edge-neighbour's texture
    assignment, and the draw order is derived from the part's own sortList."""
    f_idx = bm.faces.layers.int.get("pe_face_index")
    v_idx = bm.verts.layers.int.get("pe_vertex_index")
    f_orig = bm.faces.layers.int.get("pe_face_orig")
    v_orig = bm.verts.layers.int.get("pe_vertex_orig")
    if f_idx is None or v_idx is None or f_orig is None or v_orig is None:
        raise ValueError("'%s' predates the pe_face_orig/pe_vertex_orig markers - re-import it "
                         "so new geometry can be told from original geometry" % obj.name)
    if not bm.faces:
        raise ValueError("'%s' has no faces - refusing to write an empty part" % obj.name)
    ngons = [f for f in bm.faces if len(f.verts) not in (3, 4)]
    if ngons:
        raise ValueError("'%s' has %d n-gon(s); this format stores only triangles and quads - "
                         "triangulate first" % (obj.name, len(ngons)))
    if len(bm.verts) > 0xFFFF:
        raise ValueError("'%s' has %d vertices, over this format's 16-bit per-face vertex "
                         "indexing limit (65535)" % (obj.name, len(bm.verts)))

    mesh_off = _mesh_record_offset(part_index, 0)
    (_mt, orig_face_count, _fl, old_faceNormList_off, orig_vertex_count,
     _vl, old_vertexNormList_off, _sl, old_attribVList_off) = struct.unpack_from(
        "<IIIIIIIII", rrf_data, mesh_off)

    # Kept so the collision-box check can tell "this edit broke it" from "it was already
    # like that" - see the bounding_stale note below.
    rrf_data_before = rrf_data
    old_vertices_before = [read_vertex_position(rrf_data, part_index, 0, i)
                           for i in range(orig_vertex_count)]

    is_root = part_index == 0
    pivot = obj.get("pe_pivot", (0.0, 0.0, 0.0))

    new_vertices, new_attrib_v, new_vertex_normals = [], [], []
    vert_slot = {}
    for i, v in enumerate(bm.verts):
        vert_slot[v.index] = i
        lx, ly, lz = v.co
        if is_root:
            new_vertices.append((lx + pivot[0], ly + pivot[1], lz + pivot[2]))
        else:
            new_vertices.append((lx, ly, lz))
        ov = v[v_idx]
        if v[v_orig] and 0 <= ov < orig_vertex_count:
            val, = struct.unpack_from("<H", rrf_data, old_attribVList_off + ov * 2)
            new_attrib_v.append(val)
            new_vertex_normals.append(read_stored_normal(rrf_data, old_vertexNormList_off, ov))
        else:
            new_attrib_v.append(0)
            new_vertex_normals.append(None)

    new_faces, new_tex, new_corners, new_mats = [], [], [], []
    new_records, new_face_normals = [], []
    face_orig_to_new, pending_neighbour = {}, {}
    orphan_count = 0
    for f in bm.faces:
        slot = len(new_faces)
        new_faces.append(tuple(vert_slot[v.index] for v in f.verts))
        of = f[f_idx]
        if f[f_orig] and 0 <= of < orig_face_count:
            new_records.append(read_face_record(rrf_data, part_index, 0, of))
            new_tex.append(read_face_texture_id(rrf_data, part_index, 0, of) or 0)
            new_corners.append(read_face_corners(rrf_data, part_index, 0, of))
            new_mats.append(read_face_material_info(rrf_data, part_index, 0, of))
            new_face_normals.append(read_stored_normal(rrf_data, old_faceNormList_off, of))
            face_orig_to_new[of] = slot
        else:
            donor = None
            for e in f.edges:
                for nf in e.link_faces:
                    if nf is not f and nf[f_orig] and 0 <= nf[f_idx] < orig_face_count:
                        donor = nf
                        break
                if donor is not None:
                    break
            if donor is None:
                orphan_count += 1
                new_records.append(None)
                new_tex.append(0)
                new_corners.append([(0, 0)] * 4)
                new_mats.append(None)
                new_face_normals.append(None)
            else:
                d = donor[f_idx]
                new_records.append(None)
                new_tex.append(read_face_texture_id(rrf_data, part_index, 0, d) or 0)
                new_corners.append(read_face_corners(rrf_data, part_index, 0, d))
                new_mats.append(read_face_material_info(rrf_data, part_index, 0, d))
                new_face_normals.append(None)
                pending_neighbour[slot] = d

    if orphan_count:
        raise ValueError("'%s' has %d new face(s) sharing no edge with any original face, so there "
                         "is no real texture assignment to inherit - attach them to existing "
                         "geometry, or give the part a private skin first" % (obj.name, orphan_count))

    calc_f, calc_v = compute_normals(new_vertices, new_faces)
    new_face_normals = [n if n is not None else calc_f[i] for i, n in enumerate(new_face_normals)]
    new_vertex_normals = [n if n is not None else calc_v[i] for i, n in enumerate(new_vertex_normals)]

    neighbours = {}
    for slot, orig_donor in pending_neighbour.items():
        ns = face_orig_to_new.get(orig_donor)
        if ns is not None:
            neighbours[slot] = ns

    orig_blocks = read_sort_list(rrf_data, part_index, 0)
    derived_sort = derive_sort_list(orig_blocks, face_orig_to_new, len(new_faces),
                                    new_face_neighbours=neighbours)

    new_data = rebuild_part_mesh_region(
        rrf_data, part_index, new_vertices, new_faces, new_tex, new_corners,
        new_attrib_v, new_material_info=new_mats,
        new_face_normals=new_face_normals, new_vertex_normals=new_vertex_normals,
        new_face_records=new_records, new_sort_blocks=derived_sort,
    )
    # Gameplay attributes: write back whatever the object carries, so a type changed in
    # Blender (or via MESH_OT_pe_set_part_attribute) actually reaches the file. Only
    # touched when the object really has the property, so an object that never had one
    # cannot silently zero a real part's tags.
    if "pe_obj_attribut" in obj:
        existing = read_part_attribute(new_data, part_index)
        wanted = parse_obj_attribut_property(obj["pe_obj_attribut"], fallback=existing)
        if wanted != existing:
            new_data = bytearray(new_data)
            patch_part_attribute(new_data, part_index, wanted)
            new_data = bytes(new_data)

    stats = {
        "vertices": len(new_vertices),
        "faces": len(new_faces),
        "added": len(new_faces) - len(face_orig_to_new),
        "removed": orig_face_count - len(face_orig_to_new),
        # Only flag a box this edit actually broke. Many real parts ship with a box that
        # already fails to contain their mesh (deliberately larger or smaller collision
        # volumes), and warning about those on every write - including a no-op one - is
        # noise the user cannot act on and did not cause.
        "bounding_stale": (part_bounding_contains(rrf_data_before, part_index, old_vertices_before)
                           and not part_bounding_contains(new_data, part_index, new_vertices)),
    }
    return new_data, stats


def patch_sort_list(data, part_index, lod, blocks):
    """Overwrites a part's 8 sortList blocks in place. Fixed-size region, so no rebuild
    is needed - the bytes are simply replaced.

    `blocks` must be 8 lists of exactly faceCount uint16 entries; the 0x8000 skip flag
    in an entry is written through untouched."""
    mesh_off = _mesh_record_offset(part_index, lod)
    faceCount, = struct.unpack_from("<I", data, mesh_off + 4)
    sortList_off, = struct.unpack_from("<I", data, mesh_off + 28)
    if len(blocks) != 8:
        raise ValueError("expected 8 sortList blocks, got %d" % len(blocks))
    for b, blk in enumerate(blocks):
        if len(blk) != faceCount:
            raise ValueError("sort block %d has %d entries, expected %d" % (b, len(blk), faceCount))
        struct.pack_into("<%dH" % faceCount, data, sortList_off + b * faceCount * 2,
                         *[v & 0xFFFF for v in blk])


def move_faces_in_sort_block(block, face_indices, later):
    """Moves the given faces one position through a single sortList block, mirroring the
    real tool exactly (rrBspTreeEdit in Rrdwire.c).

    ObjEdit walks the block and swaps each selected entry with its neighbour - forwards
    from index 1 when moving earlier, backwards from the end when moving later - so a run
    of selected faces shifts as a group without overtaking each other. Reproduced here
    step for step rather than reimplemented, since draw order is hand-authored and this
    is the only operation the original tool offers on it.

    Returns a new list; `block` is not modified."""
    out = list(block)
    targets = set(face_indices)
    if later:
        for i in range(len(out) - 2, -1, -1):
            if (out[i] & 0x7FFF) in targets:
                out[i + 1], out[i] = out[i], out[i + 1]
    else:
        for i in range(1, len(out)):
            if (out[i] & 0x7FFF) in targets:
                out[i - 1], out[i] = out[i], out[i - 1]
    return out


def validate_rrf(filepath):
    """Checks a .RRF for the problems that have actually bitten this project, and returns
    a list of (severity, part_index_or_None, message) - severity "ERROR" for something the
    engine can be expected to choke on, "WARNING" for something suspicious but survivable.

    Every check here corresponds to a real bug found during development, not a
    hypothetical:

    - maxAllVertex below the sum of vertex counts: Scene.c sizes the per-actor vertex
      buffers from it (vCount = obj->maxAllVertex), so too small means the transform
      writes past the allocation.
    - a part's maxVertex below its own vertexCount: the same buffer is carved up per part
      by maxVertex, so the part writes into the next part's slice.
    - sortList blocks that are not permutations of 0..faceCount-1: the draw loop indexes
      faces through them.
    - faces referencing vertices past vertexCount.
    - degenerate faces (a repeated vertex within one face): real shipped content contains
      these, so only a warning - but they once hung this importer's normal recalculation.
    - a collision box that does not contain its own mesh.
    - vertex counts near the format's 16-bit per-face index limit.
    """
    findings = []
    data = read_rrf_raw(filepath)
    maxLOD, transInfo, objCount, maxAllVertex, textureStart, textureLen = struct.unpack_from(
        "<HHIIII", data, 0)

    if not (0 < objCount < 4096):
        findings.append(("ERROR", None, "objCount is %d, which is not plausible" % objCount))
        return findings

    total_vertices = 0
    for p in range(objCount):
        mesh_off = _mesh_record_offset(p, 0)
        (_mt, faceCount, faceList_off, _fnl, vertexCount,
         _vl, _vnl, sortList_off, _avl) = struct.unpack_from("<IIIIIIIII", data, mesh_off)
        total_vertices += vertexCount
        part_off = HEADER_SIZE + p * PART_SIZE
        maxVertex = struct.unpack_from("<I", data, part_off + 84)[0]
        name = data[part_off:part_off + 12].split(b"\x00")[0].decode("latin-1", "replace")
        label = "part %d (%s)" % (p, name or "unnamed")

        if maxVertex < vertexCount:
            findings.append(("ERROR", p, "%s: maxVertex %d < vertexCount %d - this part will "
                                         "write past its slice of the shared vertex buffer"
                             % (label, maxVertex, vertexCount)))
        if vertexCount > 0xFFFF:
            findings.append(("ERROR", p, "%s: %d vertices exceeds the 16-bit per-face vertex "
                                         "index limit" % (label, vertexCount)))
        elif vertexCount > 0xF000:
            findings.append(("WARNING", p, "%s: %d vertices is close to the 65535 limit"
                             % (label, vertexCount)))

        if faceCount == 0 or vertexCount == 0:
            continue

        # sortList blocks must be permutations (the low 15 bits are the face index; bit 15
        # is the engine's own "skip this face" flag).
        # Two genuinely different failures here, worth separating: an entry pointing PAST
        # faceCount makes the draw loop index outside the face array
        # (ptrFaces[faceOrderList[faceNo]] in Rrdraw.c), while duplicates/omissions stay
        # in bounds and merely draw some faces twice and others never. Real shipped
        # content contains both - typically models whose faces were deleted without the
        # sortList being fully repaired - so calling everything an ERROR would cry wolf.
        try:
            out_of_range = dup_blocks = 0
            for b in range(8):
                blk = struct.unpack_from("<%dH" % faceCount, data, sortList_off + b * faceCount * 2)
                masked = [v & 0x7FFF for v in blk]
                if any(v >= faceCount for v in masked):
                    out_of_range += 1
                elif sorted(masked) != list(range(faceCount)):
                    dup_blocks += 1
            if out_of_range:
                findings.append(("ERROR", p, "%s: %d of 8 sortList blocks contain a face index past "
                                             "faceCount %d - the draw loop reads outside the face "
                                             "array" % (label, out_of_range, faceCount)))
            if dup_blocks:
                findings.append(("WARNING", p, "%s: %d of 8 sortList blocks repeat or omit faces - "
                                               "in bounds, but some faces draw twice and others "
                                               "never" % (label, dup_blocks)))
        except struct.error:
            findings.append(("ERROR", p, "%s: sortList runs past the end of the file" % label))

        # face vertex indices in range, and degenerate faces
        degenerate = 0
        try:
            for f in range(faceCount):
                off = faceList_off + f * FACE_SIZE
                v1, v2, v3, _to, th, mi = struct.unpack_from("<IIIIII", data, off)
                idx = [v1 & 0xFFFF, v2 & 0xFFFF, v3 & 0xFFFF]
                if mi & MAT_QUAD:
                    idx.append(th & 0xFFFF)
                if any(i >= vertexCount for i in idx):
                    findings.append(("ERROR", p, "%s: face %d references a vertex past "
                                                 "vertexCount %d" % (label, f, vertexCount)))
                    break
                if len(set(idx)) != len(idx):
                    degenerate += 1
        except struct.error:
            findings.append(("ERROR", p, "%s: faceList runs past the end of the file" % label))
        if degenerate:
            findings.append(("WARNING", p, "%s: %d degenerate face(s) (a repeated vertex within "
                                           "one face)" % (label, degenerate)))

        try:
            verts = [read_vertex_position(data, p, 0, i) for i in range(vertexCount)]
            if not part_bounding_contains(data, p, verts):
                findings.append(("WARNING", p, "%s: collision box does not contain its own mesh"
                                 % label))
        except (struct.error, IndexError):
            pass

    if maxAllVertex < total_vertices:
        findings.insert(0, ("ERROR", None,
                            "header maxAllVertex %d < the sum of all parts' vertexCount %d - the "
                            "engine sizes its vertex buffers from this"
                            % (maxAllVertex, total_vertices)))
    if textureStart + textureLen > len(data):
        findings.insert(0, ("ERROR", None, "textureStart+textureLen runs past the end of the file"))
    return findings


class MESH_OT_pe_remap_texture_library(bpy.types.Operator):
    """Repoints this model's faces from one texture-library slot to another - ObjEdit's
    ReNumTLB. Useful for moving a vehicle onto a different theatre's libraries without
    re-texturing it face by face.

    A face's textureOfset holds the library slot in bits 12-15 and the part id in bits
    0-11, with a 32-library extension where a part id above 2047 means slot+16 and
    id-2048. This changes only the slot; the part id and everything in the upper 16 bits
    are preserved, so each face keeps pointing at the same rectangle number in whichever
    library now occupies the target slot.

    Faces whose part id exceeds "Max Part ID" are left alone and reported rather than
    remapped - the original tool does the same, since the destination library may not have
    an entry that high and pointing a face at a rectangle that does not exist would be
    worse than leaving it. Raise the limit only if you know the target library is as
    large.

    Writes straight to the .RRF with the usual one-time .bak. Nothing about the geometry
    changes, so no rebuild happens. Re-import afterwards to see the result - the loaded
    materials still reference the old libraries."""

    bl_idname = "mesh.pe_remap_texture_library"
    bl_label = "PE: Remap Texture Library (slot -> slot)"
    bl_options = {"REGISTER", "UNDO"}

    old_slot: IntProperty(name="From Slot", description="Library slot to move faces off",
                          default=0, min=0, max=31)
    new_slot: IntProperty(name="To Slot", description="Library slot to move them onto",
                          default=1, min=0, max=31)
    max_id: IntProperty(name="Max Part ID",
                        description="Faces with a part id above this are left alone and "
                                    "reported, as the target library may not have an entry "
                                    "that high",
                        default=4095, min=0, max=4095)
    whole_model: BoolProperty(name="All Parts",
                              description="Apply to every part of the model, not just this "
                                          "one - a .RRF holds the whole vehicle",
                              default=True)

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "MESH" and "pe_rrf_filepath" in obj \
            and "pe_part_index" in obj

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        obj = context.active_object
        rrf_filepath = obj["pe_rrf_filepath"]
        if self.old_slot == self.new_slot:
            self.report({"WARNING"}, "From and To are the same slot - nothing to do")
            return {"CANCELLED"}
        try:
            data = bytearray(read_rrf_raw(rrf_filepath))
        except OSError as e:
            self.report({"ERROR"}, "Could not read %s: %s" % (rrf_filepath, e))
            return {"CANCELLED"}

        objCount = struct.unpack_from("<I", data, 4)[0]
        parts = range(objCount) if self.whole_model else [obj["pe_part_index"]]
        total_remapped = total_skipped = 0
        for p in parts:
            try:
                r, sk = remap_part_library(data, p, self.old_slot, self.new_slot, self.max_id)
            except (struct.error, IndexError):
                continue
            total_remapped += r
            total_skipped += sk

        if not total_remapped and not total_skipped:
            self.report({"WARNING"}, "No faces are using slot %d - nothing written"
                        % self.old_slot)
            return {"CANCELLED"}

        _backup_once(rrf_filepath)
        write_rrf_raw(rrf_filepath, bytes(data))
        msg = "Remapped %d face(s) from slot %d to %d in %s" % (
            total_remapped, self.old_slot, self.new_slot, os.path.basename(rrf_filepath))
        if total_skipped:
            msg += " - %d left alone (part id above %d)" % (total_skipped, self.max_id)
        msg += ". Re-import to see the change."
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class MESH_OT_pe_move_face_draw_order(bpy.types.Operator):
    """Moves the selected face(s) one step earlier or later in this part's draw order.

    A part stores 8 draw orders - one per viewing octant - and the engine picks whichever
    matches the current view direction. Nothing generates them: the only tool the original
    ObjEdit offers is exactly this one-step nudge (rrSetSortInfo -> rrBspTreeEdit), so a
    real model's ordering is hand-authored face by face. That is why this operator exists
    rather than a "recalculate draw order" button - there is no algorithm to recalculate
    it with.

    Use it when a face draws through something it should be behind. Later = drawn nearer
    the end = on top.

    By default all 8 octants are nudged together, which is predictable and is almost
    always what is wanted. ObjEdit instead edits only the octant matching its current
    view; a specific octant can be chosen here for that, indexed as
    bit0 = X>=0, bit1 = Y>=0, bit2 = Z>=0 in the part's own space (rrDirectionToSortListNo).
    Blender's viewport is deliberately NOT mapped onto that automatically - matching PE's
    matrix convention has not been verified, and guessing it would silently edit the wrong
    octant.

    Writes straight to the .RRF (with the usual one-time .bak); the sortList is a
    fixed-size region, so nothing is rebuilt."""

    bl_idname = "mesh.pe_move_face_draw_order"
    bl_label = "PE: Move Face(s) in Draw Order"
    bl_options = {"REGISTER", "UNDO"}

    later: BoolProperty(
        name="Draw Later (on top)",
        description="Move the selection towards the end of the draw order, so it draws "
                    "over things instead of under them",
        default=True,
    )
    all_octants: BoolProperty(
        name="All View Directions",
        description="Nudge the face in all 8 stored draw orders. Turn off to edit a "
                    "single octant, as ObjEdit does for its current view",
        default=True,
    )
    octant: IntProperty(
        name="Octant",
        description="Which of the 8 stored draw orders to edit "
                    "(bit0 = X>=0, bit1 = Y>=0, bit2 = Z>=0 in the part's own space)",
        default=0, min=0, max=7,
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (
            obj is not None
            and obj.type == "MESH"
            and obj.mode == "EDIT"
            and "pe_rrf_filepath" in obj
            and "pe_part_index" in obj
        )

    def execute(self, context):
        obj = context.active_object
        mesh = obj.data
        rrf_filepath = obj["pe_rrf_filepath"]
        part_index = obj["pe_part_index"]

        bm = bmesh.from_edit_mesh(mesh)
        bm.faces.ensure_lookup_table()
        f_idx = bm.faces.layers.int.get("pe_face_index")
        f_orig = bm.faces.layers.int.get("pe_face_orig")
        if f_idx is None:
            self.report({"ERROR"}, "This mesh has no pe_face_index data - re-import it")
            return {"CANCELLED"}

        selected = [f for f in bm.faces if f.select]
        if not selected:
            self.report({"WARNING"}, "No faces selected")
            return {"CANCELLED"}

        try:
            rrf_data = bytearray(read_rrf_raw(rrf_filepath))
        except OSError as e:
            self.report({"ERROR"}, "Could not read %s: %s" % (rrf_filepath, e))
            return {"CANCELLED"}

        mesh_off = _mesh_record_offset(part_index, 0)
        faceCount, = struct.unpack_from("<I", rrf_data, mesh_off + 4)

        targets, unwritten = [], 0
        for f in selected:
            if f_orig is not None and not f[f_orig]:
                unwritten += 1
                continue
            fi = f[f_idx]
            if 0 <= fi < faceCount:
                targets.append(fi)
            else:
                unwritten += 1
        if not targets:
            self.report({"ERROR"}, "None of the selected faces exist in the file yet - write "
                                   "the mesh first, then reorder")
            return {"CANCELLED"}

        blocks = read_sort_list(rrf_data, part_index, 0)
        which = range(8) if self.all_octants else [self.octant]
        for b in which:
            blocks[b] = move_faces_in_sort_block(blocks[b], targets, self.later)

        try:
            patch_sort_list(rrf_data, part_index, 0, blocks)
        except ValueError as e:
            self.report({"ERROR"}, "Could not write the draw order: %s" % e)
            return {"CANCELLED"}

        _backup_once(rrf_filepath)
        write_rrf_raw(rrf_filepath, bytes(rrf_data))

        msg = "Moved %d face(s) %s in %s" % (
            len(targets), "later" if self.later else "earlier",
            "all 8 view directions" if self.all_octants else "octant %d" % self.octant)
        if unwritten:
            msg += " (%d selected face(s) skipped - not in the file yet)" % unwritten
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class MESH_OT_pe_validate_model(bpy.types.Operator):
    """Checks the .RRF this object came from for the problems that actually break models,
    and reports them in the Info log.

    Not a general-purpose file linter - every check corresponds to a real bug found while
    building this add-on: capacity fields (maxVertex / maxAllVertex) too small for the
    geometry, which makes the engine write past its own buffers; sortList blocks that are
    not valid permutations, which the draw loop indexes through; faces referencing
    vertices that do not exist; degenerate faces; and a collision box that no longer
    contains its mesh.

    Run it after editing, before trusting a model in game - it catches in a second what
    otherwise shows up as an access violation with no explanation."""

    bl_idname = "mesh.pe_validate_model"
    bl_label = "PE: Validate Model (.RRF)"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "MESH" and "pe_rrf_filepath" in obj

    def execute(self, context):
        path = context.active_object["pe_rrf_filepath"]
        try:
            findings = validate_rrf(path)
        except (OSError, struct.error) as e:
            self.report({"ERROR"}, "Could not validate %s: %s" % (os.path.basename(path), e))
            return {"CANCELLED"}

        errors = [f for f in findings if f[0] == "ERROR"]
        warnings = [f for f in findings if f[0] == "WARNING"]
        for sev, _part, msg in findings[:40]:
            self.report({"ERROR"} if sev == "ERROR" else {"WARNING"}, msg)
        if not findings:
            self.report({"INFO"}, "%s: no problems found" % os.path.basename(path))
        else:
            self.report({"INFO"} if not errors else {"ERROR"},
                        "%s: %d error(s), %d warning(s)"
                        % (os.path.basename(path), len(errors), len(warnings)))
        return {"FINISHED"}


class MESH_OT_pe_set_part_attribute(bpy.types.Operator):
    """Sets this part's gameplay type and hide flag - the objAttribut word the game uses
    to decide what a part IS, not just how it looks.

    The low byte is the part type (TANK, TURM, KANNONE, MUZZLE, HATCH, the crew
    positions, the smoke/dust emitters...) from the real Rrattrib.h set; bit 31 is the
    hide flag. A turret that is not tagged TURM will render perfectly and still not
    traverse, so this matters for any model expected to work in game rather than just
    look right in a viewer.

    Edits the object's pe_obj_attribut property; the value reaches the file on the next
    write or export. Every other bit of the word is preserved - real parts carry more
    than just the type and hide flag, and this only replaces the fields it names."""

    bl_idname = "mesh.pe_set_part_attribute"
    bl_label = "PE: Set Part Type / Attributes"
    bl_options = {"REGISTER", "UNDO"}

    part_type: EnumProperty(
        name="Part Type",
        description="What this part is, as far as the game is concerned",
        items=lambda self, ctx: [
            (str(k), "%s (%d)" % (v, k), "OBJ_TYPE_%s" % v)
            for k, v in sorted(OBJ_TYPE_NAMES.items())
        ],
    )
    hidden: BoolProperty(
        name="Hidden",
        description="Set OBJ_ATTRIB_HIDE (bit 31) - the part exists but is not drawn",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "MESH" and "pe_part_index" in obj

    def invoke(self, context, event):
        obj = context.active_object
        current = parse_obj_attribut_property(obj.get("pe_obj_attribut"), 0)
        try:
            self.part_type = str(obj_attribut_type(current))
        except TypeError:
            pass
        self.hidden = bool(current & OBJ_ATTRIB_HIDE)
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        obj = context.active_object
        current = parse_obj_attribut_property(obj.get("pe_obj_attribut"), 0)
        value = obj_attribut_set_type(current, int(self.part_type))
        if self.hidden:
            value |= OBJ_ATTRIB_HIDE
        else:
            value &= ~OBJ_ATTRIB_HIDE
        value &= 0xFFFFFFFF
        obj["pe_obj_attribut"] = hex(value)
        obj["pe_type_name"] = OBJ_TYPE_NAMES.get(obj_attribut_type(value), "UNKNOWN")
        self.report({"INFO"},
                    "%s is now %s%s - write or export the model to save it to the .RRF"
                    % (obj.name, obj["pe_type_name"], " (hidden)" if self.hidden else ""))
        return {"FINISHED"}


class MESH_OT_pe_write_mesh(bpy.types.Operator):
    """Writes this part's CURRENT Blender mesh back to its .RRF - including faces and
    vertices added or deleted in Blender, not just moved ones. The nearest thing this
    project has to a model exporter.

    Scope, stated plainly: this rewrites one part of the .RRF the mesh was imported
    from. It is not "save an arbitrary Blender object as a new .RRF" - creating parts,
    hierarchy and attributes from nothing is a separate, unbuilt job (see
    docs/ADD_FACES_SCOPING.md, Phase 3).

    Everything that already existed keeps its real file data: each surviving face's raw
    24-byte record (texture id, crop corners, materialInfo) is carried forward with only
    its vertex indices remapped, and each surviving vertex keeps its own stored normal
    and attribVList tag. The draw order is derived from the part's own sortList rather
    than regenerated. Elements are identified as original via the pe_face_orig/
    pe_vertex_orig markers stamped at import - anything reading 0 was created in Blender.

    A genuinely NEW face has no texture data of its own, so it inherits the whole
    assignment (texture id, crop corners and materialInfo) from a face it shares an edge
    with - the same "borrow from a real neighbour" approach used elsewhere in this
    plugin, chosen over inventing values or allocating fresh atlas space. It therefore
    samples exactly the same texture rectangle as that neighbour; giving new geometry its
    own UV area is a separate job (MESH_OT_pe_give_private_skin already does that for a
    whole part). New faces with no original neighbour are refused rather than guessed at.

    New vertices get a computed geometric normal and a zero attribVList tag, matching the
    documented safe baseline for both.

    Refuses rather than risks the file when: the mesh has an n-gon (the format stores
    only triangles and quads), the part would exceed the format's 16-bit vertex indexing,
    every face has been deleted, or the mesh predates the pe_face_orig marker and so
    cannot be checked for new geometry. Makes the usual one-time .bak first."""

    bl_idname = "mesh.pe_write_mesh"
    bl_label = "PE: Write Mesh to .RRF (incl. added/deleted faces)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (
            obj is not None
            and obj.type == "MESH"
            and obj.mode == "EDIT"
            and "pe_rrf_filepath" in obj
            and "pe_part_index" in obj
        )

    def execute(self, context):
        obj = context.active_object
        mesh = obj.data
        rrf_filepath = obj["pe_rrf_filepath"]
        part_index = obj["pe_part_index"]

        bm = bmesh.from_edit_mesh(mesh)
        bm.faces.ensure_lookup_table()
        bm.verts.ensure_lookup_table()

        try:
            rrf_data = read_rrf_raw(rrf_filepath)
        except OSError as e:
            self.report({"ERROR"}, "Could not read " + rrf_filepath + ": " + str(e))
            return {"CANCELLED"}

        try:
            new_data, stats = write_object_mesh_into_rrf(rrf_data, obj, bm, part_index)
        except (ValueError, struct.error) as e:
            self.report({"ERROR"}, str(e) + " - nothing written")
            return {"CANCELLED"}

        _backup_once(rrf_filepath)
        write_rrf_raw(rrf_filepath, new_data)

        f_idx = bm.faces.layers.int.get("pe_face_index")
        v_idx = bm.verts.layers.int.get("pe_vertex_index")
        f_orig = bm.faces.layers.int.get("pe_face_orig")
        v_orig = bm.verts.layers.int.get("pe_vertex_orig")
        for i, v in enumerate(bm.verts):
            v[v_idx] = i
            v[v_orig] = 1
        for i, f in enumerate(bm.faces):
            f[f_idx] = i
            f[f_orig] = 1
        bmesh.update_edit_mesh(mesh)

        if stats.get("bounding_stale"):
            self.report({"WARNING"},
                        "This part has a custom collision box that no longer contains its "
                        "geometry - it was preserved rather than overwritten. Regenerate it in "
                        "ObjEdit (Bounding Box > Gen) if the new shape should collide.")
        self.report({"INFO"},
                    "Wrote part %d: %d vertex(es), %d face(s) (+%d new, -%d deleted) to %s"
                    % (part_index, stats["vertices"], stats["faces"], stats["added"],
                       stats["removed"], os.path.basename(rrf_filepath)))
        return {"FINISHED"}


class MESH_OT_pe_write_vertex_positions(bpy.types.Operator):
    """Writes the mesh's current vertex positions back into the .RRF - Phase 1 of the
    geometry writer (see docs/RRF_WRITER_SCOPING.md). Repositions existing vertices only:
    vertex count, face count, and every other part's data are left completely untouched,
    so none of the format's still-unconfirmed regions (sortList, attribVList, LOD>0) are
    touched or put at risk. Adding or removing vertices/faces in Edit Mode first is not
    supported - the operator refuses to run if Blender's own vertex count no longer
    matches the file's recorded count for this part.

    Converts each vertex from Blender's own local mesh-space convention back to the raw
    file value using the same convention build_blender_objects() applies on import (see
    RRF_FORMAT.md): the root part's mesh is stored in Blender as raw-minus-pivot (since
    its object.location already carries the pivot), so root vertices need the part's
    pivot added back before writing; every other part's mesh is stored in Blender
    identical to the raw file value, so no pivot arithmetic applies there at all. The
    pivot itself is read from the pe_pivot custom property stamped at import time, not
    from the object's own obj.location, so a later Object Mode move of the whole part
    can't silently corrupt the write.

    Moving the whole object's own transform (Object Mode translate/rotate/scale) is a
    separate concern this operator does not handle at all - only per-vertex positions
    edited in Edit Mode are written back; the part's own pivot/hierarchy placement in the
    file is left completely unchanged."""

    bl_idname = "mesh.pe_write_vertex_positions"
    bl_label = "PE: Write Vertex Positions"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (
            obj is not None
            and obj.type == "MESH"
            and obj.mode == "EDIT"
            and "pe_rrf_filepath" in obj
            and "pe_part_index" in obj
        )

    def execute(self, context):
        obj = context.active_object
        mesh = obj.data
        rrf_filepath = obj["pe_rrf_filepath"]
        part_index = obj["pe_part_index"]
        pivot = obj.get("pe_pivot", tuple(obj.location))
        is_root = (part_index == 0)

        bm = bmesh.from_edit_mesh(mesh)
        bm.verts.ensure_lookup_table()

        try:
            rrf_data = read_rrf_raw(rrf_filepath)
        except OSError as e:
            self.report({"ERROR"}, f"Could not read {rrf_filepath}: {e}")
            return {"CANCELLED"}

        try:
            mesh_off = _mesh_record_offset(part_index, 0)
            file_vertex_count, = struct.unpack_from("<I", rrf_data, mesh_off + 16)
        except struct.error as e:
            self.report({"ERROR"}, f"Could not read part {part_index}'s mesh record: {e}")
            return {"CANCELLED"}

        if len(bm.verts) != file_vertex_count:
            self.report(
                {"ERROR"},
                f"Vertex count changed ({len(bm.verts)} in Blender vs. {file_vertex_count} "
                f"in the file) - adding/removing vertices isn't supported by this operator yet.",
            )
            return {"CANCELLED"}

        updated_count = 0
        for vert in bm.verts:
            lx, ly, lz = vert.co
            if is_root:
                x, y, z = lx + pivot[0], ly + pivot[1], lz + pivot[2]
            else:
                x, y, z = lx, ly, lz
            patch_vertex_position(rrf_data, part_index, 0, vert.index, x, y, z)
            updated_count += 1

        _backup_once(rrf_filepath)
        write_rrf_raw(rrf_filepath, rrf_data)

        self.report(
            {"INFO"},
            f"Wrote {updated_count} vertex position(s) back to {os.path.basename(rrf_filepath)}",
        )
        return {"FINISHED"}


class MESH_OT_pe_delete_faces(bpy.types.Operator):
    """Deletes the selected face(s) - and any vertex left with no remaining faces - and
    writes the result back to the model's own .RRF. The first real "remove geometry"
    piece of Phase 2 of the geometry writer (see docs/RRF_WRITER_SCOPING.md); adding
    genuinely new faces is a separate, harder follow-on, since a brand new face has no
    existing texture assignment to fall back on the way every surviving face here does.

    Every REMAINING face keeps its exact original texture id and UV-crop corners, looked
    up from the file via each face's pe_face_index custom attribute (stamped at import
    time) - nothing about texturing needs inventing here, since this operator only ever
    removes content. Vertex positions for surviving vertices convert from Blender's local
    mesh-space back to the file's raw convention exactly like
    MESH_OT_pe_write_vertex_positions does (root part: add the pivot back; every other
    part: unchanged). Each surviving vertex's attribVList tag is carried over unchanged
    from the original file via its own pe_vertex_index custom attribute - never invented,
    since deleting never introduces a genuinely new vertex.

    sortList is fully regenerated for the new, smaller face count using
    compute_sort_list() - a real recipe confirmed from the actual engine source, but
    empirically strong (not proven byte-exact) - see RRF_WRITER_SCOPING.md. A real
    in-game/ObjEdit visual check of the result is recommended before trusting this on
    real work, the same way Phase 1 needed one.

    This resizes the part's whole mesh-data region and shifts every later part's mesh
    offsets in the file accordingly - the first operator in this project to do that."""

    bl_idname = "mesh.pe_delete_faces"
    bl_label = "PE: Delete Face(s) (write to .RRF)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (
            obj is not None
            and obj.type == "MESH"
            and obj.mode == "EDIT"
            and "pe_rrf_filepath" in obj
            and "pe_part_index" in obj
            and "pe_pivot" in obj
        )

    def execute(self, context):
        obj = context.active_object
        mesh = obj.data
        rrf_filepath = obj["pe_rrf_filepath"]
        part_index = obj["pe_part_index"]
        pivot = obj["pe_pivot"]
        is_root = (part_index == 0)

        bm = bmesh.from_edit_mesh(mesh)
        bm.faces.ensure_lookup_table()
        bm.verts.ensure_lookup_table()

        selected_faces = [f for f in bm.faces if f.select]
        if not selected_faces:
            self.report({"WARNING"}, "No faces selected")
            return {"CANCELLED"}

        face_index_layer = bm.faces.layers.int.get("pe_face_index")
        vertex_index_layer = bm.verts.layers.int.get("pe_vertex_index")
        if face_index_layer is None or vertex_index_layer is None:
            self.report(
                {"ERROR"},
                "This mesh has no pe_face_index/pe_vertex_index data - re-import with "
                "this plugin version before using this operator.",
            )
            return {"CANCELLED"}

        try:
            rrf_data = read_rrf_raw(rrf_filepath)
        except OSError as e:
            self.report({"ERROR"}, f"Could not read {rrf_filepath}: {e}")
            return {"CANCELLED"}

        deleted_count = len(selected_faces)
        bmesh.ops.delete(bm, geom=selected_faces, context='FACES_ONLY')
        bm.verts.ensure_lookup_table()
        orphan_verts = [v for v in bm.verts if not v.link_faces]
        if orphan_verts:
            bmesh.ops.delete(bm, geom=orphan_verts, context='VERTS')

        bm.verts.ensure_lookup_table()
        bm.faces.ensure_lookup_table()

        if not bm.faces:
            self.report({"ERROR"}, "That would delete every face in this part - refusing (a part needs at least one face)")
            return {"CANCELLED"}

        surviving_vertex_orig = [v[vertex_index_layer] for v in bm.verts]
        vertex_orig_to_new = {orig: new for new, orig in enumerate(surviving_vertex_orig)}

        new_vertices = []
        for v in bm.verts:
            lx, ly, lz = v.co
            if is_root:
                new_vertices.append((lx + pivot[0], ly + pivot[1], lz + pivot[2]))
            else:
                new_vertices.append((lx, ly, lz))

        new_faces = []
        new_texture_ids = []
        new_corners = []
        new_material_info = []
        new_face_normals = []
        new_face_records = []
        face_orig_to_new = {}
        skipped = 0
        for face in bm.faces:
            orig_face_index = face[face_index_layer]
            try:
                tex_id = read_face_texture_id(rrf_data, part_index, 0, orig_face_index)
                corners = read_face_corners(rrf_data, part_index, 0, orig_face_index)
                # Carried forward deliberately: rebuilding repacks every face, so a
                # surviving face that doesn't bring its own materialInfo would silently
                # lose its shading/texture mode and its crop-size nibbles.
                mat_info = read_face_material_info(rrf_data, part_index, 0, orig_face_index)
                face_normal = read_stored_normal(rrf_data, old_faceNormList_off, orig_face_index)
                face_record = read_face_record(rrf_data, part_index, 0, orig_face_index)
            except (IndexError, struct.error):
                skipped += 1
                continue
            orig_vert_indices = [v[vertex_index_layer] for v in face.verts]
            new_vert_indices = [vertex_orig_to_new[ov] for ov in orig_vert_indices]
            new_faces.append(tuple(new_vert_indices))
            new_texture_ids.append(tex_id)
            new_corners.append(corners)
            new_material_info.append(mat_info)
            new_face_normals.append(face_normal)
            new_face_records.append(face_record)
            face_orig_to_new[orig_face_index] = len(new_faces) - 1

        if skipped:
            self.report(
                {"ERROR"},
                f"{skipped} surviving face(s) had no readable original texture data - "
                f"aborting, nothing written.",
            )
            return {"CANCELLED"}

        mesh_off = _mesh_record_offset(part_index, 0)
        (_meshType, _old_faceCount, _old_faceList_off, old_faceNormList_off,
         _old_vertexCount, _old_vertexList_off, old_vertexNormList_off,
         _old_sortList_off, old_attribVList_off) = struct.unpack_from("<IIIIIIIII", rrf_data, mesh_off)
        new_attrib_v = []
        new_vertex_normals = []
        for orig_v in surviving_vertex_orig:
            val, = struct.unpack_from("<H", rrf_data, old_attribVList_off + orig_v * 2)
            new_attrib_v.append(val)
            # Same reasoning as attribVList: keep the artist's real normal rather than
            # recomputing a geometric one for a vertex that already existed.
            new_vertex_normals.append(read_stored_normal(rrf_data, old_vertexNormList_off, orig_v))

        # Carry the part's authored draw order forward rather than regenerating one -
        # a regenerated sortList crashes the real engine (see derive_sort_list).
        orig_sort_blocks = read_sort_list(rrf_data, part_index, 0)
        derived_sort = derive_sort_list(orig_sort_blocks, face_orig_to_new, len(new_faces))

        new_data = rebuild_part_mesh_region(
            rrf_data, part_index, new_vertices, new_faces, new_texture_ids, new_corners,
            new_attrib_v, new_material_info=new_material_info,
            new_face_normals=new_face_normals, new_vertex_normals=new_vertex_normals,
            new_face_records=new_face_records, new_sort_blocks=derived_sort
        )

        _backup_once(rrf_filepath)
        write_rrf_raw(rrf_filepath, new_data)

        # Re-stamp pe_face_index/pe_vertex_index on the surviving elements to match the
        # file's own new 0..count-1 numbering, so later edits (further deletes, or
        # MESH_OT_pe_write_vertex_positions) keep working against the file as it now is.
        for new_idx, v in enumerate(bm.verts):
            v[vertex_index_layer] = new_idx
        for new_idx, f in enumerate(bm.faces):
            f[face_index_layer] = new_idx
        bmesh.update_edit_mesh(mesh)

        self.report(
            {"INFO"},
            f"Deleted {deleted_count} face(s) ({len(orphan_verts)} orphaned vertex/vertices "
            f"removed), wrote {len(new_faces)} remaining face(s) back to {os.path.basename(rrf_filepath)}",
        )
        return {"FINISHED"}


def menu_func_detach_face(self, context):
    self.layout.operator(MESH_OT_pe_detach_face_texture.bl_idname, icon="TEXTURE")
    self.layout.operator(MESH_OT_pe_set_face_crop.bl_idname, icon="UV")
    self.layout.operator(MESH_OT_pe_flip_face_texture.bl_idname, icon="ARROW_LEFTRIGHT")
    self.layout.operator(MESH_OT_pe_give_private_skin.bl_idname, icon="IMAGE_PLANE")
    self.layout.operator(MESH_OT_pe_write_vertex_positions.bl_idname, icon="VERTEXSEL")
    self.layout.operator(MESH_OT_pe_delete_faces.bl_idname, icon="TRASH")
    self.layout.operator(MESH_OT_pe_set_part_attribute.bl_idname, icon="MODIFIER")
    self.layout.operator(MESH_OT_pe_remap_texture_library.bl_idname, icon="FILE_REFRESH")
    self.layout.operator(MESH_OT_pe_move_face_draw_order.bl_idname, icon="SORTSIZE")
    self.layout.operator(MESH_OT_pe_validate_model.bl_idname, icon="CHECKMARK")
    self.layout.operator(MESH_OT_pe_write_mesh.bl_idname, icon="EXPORT")


def register():
    bpy.utils.register_class(IMPORT_OT_rrf)
    bpy.utils.register_class(EXPORT_OT_rrf_atlas)
    bpy.utils.register_class(EXPORT_OT_rrf_model)
    bpy.utils.register_class(MESH_OT_pe_detach_face_texture)
    bpy.utils.register_class(MESH_OT_pe_set_face_crop)
    bpy.utils.register_class(MESH_OT_pe_flip_face_texture)
    bpy.utils.register_class(MESH_OT_pe_give_private_skin)
    bpy.utils.register_class(MESH_OT_pe_remap_texture_library)
    bpy.utils.register_class(MESH_OT_pe_move_face_draw_order)
    bpy.utils.register_class(MESH_OT_pe_validate_model)
    bpy.utils.register_class(MESH_OT_pe_set_part_attribute)
    bpy.utils.register_class(MESH_OT_pe_write_mesh)
    bpy.utils.register_class(MESH_OT_pe_write_vertex_positions)
    bpy.utils.register_class(MESH_OT_pe_delete_faces)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)
    bpy.types.VIEW3D_MT_edit_mesh_context_menu.append(menu_func_detach_face)


def unregister():
    bpy.types.VIEW3D_MT_edit_mesh_context_menu.remove(menu_func_detach_face)
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    bpy.utils.unregister_class(MESH_OT_pe_delete_faces)
    bpy.utils.unregister_class(MESH_OT_pe_remap_texture_library)
    bpy.utils.unregister_class(MESH_OT_pe_move_face_draw_order)
    bpy.utils.unregister_class(MESH_OT_pe_validate_model)
    bpy.utils.unregister_class(MESH_OT_pe_set_part_attribute)
    bpy.utils.unregister_class(MESH_OT_pe_write_mesh)
    bpy.utils.unregister_class(MESH_OT_pe_write_vertex_positions)
    bpy.utils.unregister_class(MESH_OT_pe_give_private_skin)
    bpy.utils.unregister_class(MESH_OT_pe_flip_face_texture)
    bpy.utils.unregister_class(MESH_OT_pe_set_face_crop)
    bpy.utils.unregister_class(MESH_OT_pe_detach_face_texture)
    bpy.utils.unregister_class(EXPORT_OT_rrf_model)
    bpy.utils.unregister_class(EXPORT_OT_rrf_atlas)
    bpy.utils.unregister_class(IMPORT_OT_rrf)


if __name__ == "__main__":
    register()
