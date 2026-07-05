bl_info = {
    "name": "Panzer Elite RRF Importer",
    "author": "Jeff",
    "version": (0, 4, 0),
    "blender": (3, 6, 0),
    "location": "File > Import > Panzer Elite Model (.rrf), File > Export > Panzer Elite Texture Atlas (.bmp), Edit Mode face context menu > PE: Detach Face From Shared Texture Cell",
    "description": "Import Panzer Elite (1999) .RRF model files: geometry, part hierarchy, pivots, gameplay attribute tags, and (optionally) UVs/texture from a matching .TLB texture library. Export a repainted texture atlas back out for re-use in the game, and detach individual faces from a shared texture cell onto their own independent copy.",
    "category": "Import-Export",
}

import struct
import os
import shutil
import bpy
import bmesh
from bpy_extras.io_utils import ImportHelper, ExportHelper
from bpy.props import StringProperty, BoolProperty
from mathutils import Matrix

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

# From Rrattrib.h - only the common/recognizable ones, for a readable custom property.
OBJ_TYPE_NAMES = {
    0: "HAUS", 1: "TREE", 2: "WALL", 3: "TANK", 4: "TURM", 5: "KANNONE", 6: "MUZZLE",
    7: "KETTENVERTEX", 8: "RADVERTEX", 9: "MG1", 10: "MG2", 11: "MG3", 12: "MG4",
    13: "HATCH", 91: "MANTLEXA", 92: "SCHUERZEN", 93: "HSCHUERZEN", 96: "RADIO",
    98: "PLATESTURRET", 99: "PLATESHULL", 102: "TRACKL", 103: "TRACKR", 106: "BARREL",
    114: "CREW_DRIVER", 115: "CREW_RADIOOP", 116: "CREW_GUNNER", 117: "CREW_LOADER",
    118: "CREW_COMMANDER", 120: "JUNK", 122: "HATCH2", 123: "CARGO",
    127: "PINE", 128: "PINE2", 129: "PALM", 130: "SIGN", 131: "BARE",
    135: "SOLID", 136: "SOLID_2", 255: "NULL",
}


def fixed_to_float(raw):
    """rrCoord/rrAngle are always 32-bit 16.16 fixed point, never plain float, in every file checked."""
    return raw / 65536.0


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


def new_tlb_library():
    """A blank TLBLibrary for building a .TLB from scratch (no existing file to base it
    on) - zero-filled palette/mat_pal/parts-array baseline, id counter starting at 0, no
    entries. Real .TLB files always have SOME palette data, but this project has no
    genuine "build a fresh library" use case yet (only modifying existing ones), so this
    is an honestly-blank starting point, not a claim about what a real fresh ObjEdit
    library's palette looks like."""
    library = TLBLibrary()
    library.lib_next_id = 0
    library.palette = bytes(2048)
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
    candidate = texture_id % TLB_MAX_PARTS
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


def find_matching_tlbs(folder, unique_texture_ids, min_ratio=0.15, min_absolute=3):
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

    Returns a list of (path, tlb_parts, atlas_image_path, score) tuples, in the order
    libraries were added (best overall match first) - an empty list if nothing scores
    above the noise floor, same as find_best_tlb() returning (None, None, None, 0).
    """
    if not unique_texture_ids:
        return []

    try:
        entries = sorted(os.listdir(folder))
    except OSError:
        return []

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

    return result


def read_rri(filepath):
    """Parses the sidecar .RRI file a later ObjEdit build (Alan's export) writes next to a
    .RRF with the same base name. First 16*128 bytes are null-padded ASCII strings, one per
    library slot (0-15), naming the .TLB loaded into that slot when the model was painted -
    e.g. "texture\\CustomB1.TLB". This is the authoritative slot->library mapping (confirmed
    against a real model: slot assignments here matched exactly what a live paint-and-save
    test in the real ObjEdit produced). Empty slots are blank strings. Only 16 of the 32
    possible slots are recorded (slots 16-31 use a different composition scheme per
    ImageLibUnit.pas and aren't covered by this file format).
    Returns {slot_index: relative_path_string} for the non-empty slots.
    """
    with open(filepath, "rb") as f:
        data = f.read(16 * 128)

    slots = {}
    for slot in range(16):
        off = slot * 128
        raw = data[off:off + 128].split(b"\x00", 1)[0]
        text = raw.decode("latin-1", errors="replace").strip()
        if text:
            slots[slot] = text
    return slots


def find_rri_path(rrf_filepath):
    base = os.path.splitext(rrf_filepath)[0]
    for suffix in (".RRI", ".rri", ".RRi", ".rRI"):
        candidate = base + suffix
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
        else:
            face_texture_id.append(None)
            face_uv_corners.append(None)

    return vertices, faces, face_texture_id, face_uv_corners


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

        vertices, faces, face_texture_id, face_uv_corners = _read_mesh_lod0(data, off + 224)

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


def _build_material(root_name, image_path, tlb_filepath=None):
    image = bpy.data.images.load(image_path, check_existing=True)
    if tlb_filepath:
        # Lets face-level operators (e.g. "detach face from shared texture cell", see
        # TODO.md) find their way from a material's image back to the .TLB it came from,
        # without re-deriving it from the image filename (fragile - real files mix
        # .TLB/.tlb casing, and the _8.BMP/_24.BMP suffix-stripping isn't foolproof).
        image["pe_tlb_filepath"] = tlb_filepath
    material = bpy.data.materials.new(root_name + "_mat")
    material.use_nodes = True
    bsdf = material.node_tree.nodes.get("Principled BSDF")
    tex_node = material.node_tree.nodes.new("ShaderNodeTexImage")
    tex_node.image = image
    tex_node.interpolation = "Closest"  # this is 1999 paletted atlas art, keep it crisp
    if bsdf is not None:
        material.node_tree.links.new(tex_node.outputs["Color"], bsdf.inputs["Base Color"])
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
    file, so recalculate from the actual mesh shape instead of trusting stored winding."""
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(mesh)
    bm.free()


def build_blender_objects(parts, collection, root_name, slot_sources=None, rrf_filepath=None):
    """slot_sources: {slot_index: (tlb_parts, atlas_image_path, tlb_filepath)} or None for
    geometry-only import. A model can use several libraries at once (one per slot) - each
    gets its own material, built once here and shared across every part/mesh, since the
    same slot assignments apply model-wide.

    rrf_filepath (optional): stamped onto every created object as `pe_rrf_filepath`, so a
    face-level operator working on the resulting mesh can find its way back to the source
    .RRF - same purpose as `_build_material()`'s `pe_tlb_filepath` on the Image."""
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
                material = _build_material(f"{root_name}_{label}", atlas_image_path, tlb_filepath)
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

            if slot_sources:
                uv_layer = mesh.uv_layers.new(name="UVMap")
                unresolved_attr = mesh.attributes.new(
                    name="pe_texture_unresolved", type="BOOLEAN", domain="FACE"
                )
                for mat in mesh_materials:
                    mesh.materials.append(mat)

                for poly in mesh.polygons:
                    corners = part.face_uv_corners[poly.index]
                    tex_id = part.face_texture_id[poly.index]
                    if tex_id is None:
                        continue  # not meant to reference the shared TLB at all (solid-shaded, etc.)
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
                        # Falls back to the assigned entry's full rectangle instead of
                        # literally sampling one pixel, using the same per-corner role
                        # order confirmed via the live paint test (RRF_FORMAT.md): v1=
                        # top-right, v2=top-left, v3=bottom-left, v4=bottom-right (quads).
                        if all(c == (0, 0) for c in corners):
                            full_rect = [(sizeX - 1, 0), (0, 0), (0, sizeY - 1), (sizeX - 1, sizeY - 1)]
                            corners = full_rect[:len(corners)]
                        for loop_index, (lx, ly) in zip(poly.loop_indices, corners):
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

    def execute(self, context):
        try:
            parts = read_rrf(self.filepath)
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}

        slot_sources = None
        detect_msg = ""

        if self.tlb_filepath:
            try:
                tlb_parts = read_tlb(self.tlb_filepath)
                atlas_image_path = find_atlas_image(self.tlb_filepath)
                if atlas_image_path is None:
                    self.report({"WARNING"}, "No matching _24.BMP/_8.BMP found next to the .TLB - importing geometry only")
                else:
                    slot_sources = {0: (tlb_parts, atlas_image_path, self.tlb_filepath)}
            except Exception as e:
                self.report({"WARNING"}, f"Could not read .TLB ({e}) - importing geometry only")
        elif self.use_rri and find_rri_path(self.filepath):
            rri_path = find_rri_path(self.filepath)
            try:
                rri_slots = read_rri(rri_path)
                slot_sources = resolve_rri_libraries(rri_slots, self.filepath)
                missing = len(rri_slots) - len(slot_sources)
                detect_msg = f" - used {os.path.basename(rri_path)} ({len(slot_sources)}/{len(rri_slots)} listed libraries found on disk)"
                if not slot_sources:
                    detect_msg += " (none resolved - importing geometry only)"
                    slot_sources = None
            except Exception as e:
                self.report({"WARNING"}, f"Could not read .RRI ({e}) - falling back")

        if slot_sources is None and not self.tlb_filepath:
            search_folder = self.tlb_search_folder or default_texture_folder(self.filepath)
            auto_derived = not self.tlb_search_folder and search_folder is not None
            if search_folder:
                unique_ids = sorted({t for part in parts for t in part.face_texture_id if t is not None})
                matches = find_matching_tlbs(search_folder, unique_ids)
                origin_note = " (auto-found sibling Texture folder)" if auto_derived else ""
                if not matches:
                    detect_msg = f" - auto-detect{origin_note} found no good TLB match among {len(unique_ids)} unique texture ID(s)"
                else:
                    built = {}
                    skipped_no_atlas = []
                    for slot, (path, tlb_parts, atlas_image_path, score) in enumerate(matches):
                        if atlas_image_path is None:
                            skipped_no_atlas.append(os.path.basename(path))
                            continue
                        built[slot] = (tlb_parts, atlas_image_path, path)
                    names = ", ".join(os.path.basename(path) for path, *_ in matches)
                    detect_msg = f" - auto-detected {len(matches)} .TLB(s){origin_note}: {names}"
                    if skipped_no_atlas:
                        self.report({"WARNING"}, f"No matching _24.BMP/_8.BMP for: {', '.join(skipped_no_atlas)} - those libraries skipped")
                    if built:
                        slot_sources = built

        root_name = os.path.splitext(os.path.basename(self.filepath))[0]
        collection = bpy.data.collections.new(root_name)
        context.scene.collection.children.link(collection)

        objects, resolved_count, unresolved_count = build_blender_objects(
            parts, collection, root_name, slot_sources, rrf_filepath=self.filepath
        )

        msg = f"Imported {len(parts)} part(s) from {root_name}.rrf" + detect_msg
        if slot_sources is not None:
            msg += f" - {resolved_count} face(s) textured, {unresolved_count} unresolved"
        if unresolved_count:
            msg += " (marked magenta / PE_UNRESOLVED_TEXTURE material - re-texture by hand)"
            self.report({"WARNING"}, msg)
        else:
            self.report({"INFO"}, msg)
        return {"FINISHED"}


def menu_func_import(self, context):
    self.layout.operator(IMPORT_OT_rrf.bl_idname, text="Panzer Elite Model (.rrf)")


class EXPORT_OT_rrf_atlas(bpy.types.Operator, ExportHelper):
    """Save a texture atlas Image back out as a 24-bit .BMP the game can load.

    Covers "repaint existing regions" only (see docs/PAINT_AND_EXPORT_SCOPING.md in the
    project repo): this does NOT touch the .RRF or .TLB at all. The game's own loader
    prefers a "<name>_24.BMP" next to the .TLB over the paletted "_8.BMP" fallback, so
    dropping a repainted 24-bit atlas in with the matching filename is sufficient - no
    binary format writing needed for this case. Adding genuinely new texture regions
    (new UV layout, new .TLB entries) is a separate, bigger job - not covered here.
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
                    "change once this file replaces (or sits alongside) the original "
                    "<name>_24.BMP, so double-check you're not overwriting an atlas "
                    "other vehicles still rely on unless that's what you intend",
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
            self.filepath = os.path.splitext(self.image_name)[0] + ".bmp"
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

        image.filepath_raw = self.filepath
        image.file_format = "BMP"
        image.save()

        self.report(
            {"INFO"},
            f"Saved '{image.name}' ({image.size[0]}x{image.size[1]}) to {self.filepath} - "
            f"place it next to the .TLB as <name>_24.BMP for the game/ObjEdit to pick it up",
        )
        return {"FINISHED"}


def menu_func_export(self, context):
    self.layout.operator(EXPORT_OT_rrf_atlas.bl_idname, text="Panzer Elite Texture Atlas (.bmp)")


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


def menu_func_detach_face(self, context):
    self.layout.operator(MESH_OT_pe_detach_face_texture.bl_idname, icon="TEXTURE")


def register():
    bpy.utils.register_class(IMPORT_OT_rrf)
    bpy.utils.register_class(EXPORT_OT_rrf_atlas)
    bpy.utils.register_class(MESH_OT_pe_detach_face_texture)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)
    bpy.types.VIEW3D_MT_edit_mesh_context_menu.append(menu_func_detach_face)


def unregister():
    bpy.types.VIEW3D_MT_edit_mesh_context_menu.remove(menu_func_detach_face)
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    bpy.utils.unregister_class(MESH_OT_pe_detach_face_texture)
    bpy.utils.unregister_class(EXPORT_OT_rrf_atlas)
    bpy.utils.unregister_class(IMPORT_OT_rrf)


if __name__ == "__main__":
    register()
