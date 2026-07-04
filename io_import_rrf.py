bl_info = {
    "name": "Panzer Elite RRF Importer",
    "author": "Jeff",
    "version": (0, 3, 0),
    "blender": (3, 6, 0),
    "location": "File > Import > Panzer Elite Model (.rrf), File > Export > Panzer Elite Texture Atlas (.bmp)",
    "description": "Import Panzer Elite (1999) .RRF model files: geometry, part hierarchy, pivots, gameplay attribute tags, and (optionally) UVs/texture from a matching .TLB texture library. Export a repainted texture atlas back out for re-use in the game.",
    "category": "Import-Export",
}

import struct
import os
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
# slot=2 (8202 - 2*4096 = 10). Some older/heavily-edited faces instead carry a live HAL
# texture handle from a later tool version, which cannot be resolved from files at all -
# so this is a best-effort search, not guaranteed to resolve every face.
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
    """rrCoord/rrAngle are 32-bit 16.16 fixed point, never plain float (confirmed: __RRFLOAT__ is never defined anywhere in the source)."""
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


def resolve_texture_id(texture_id, slot_to_parts):
    """slot_to_parts: {slot_index: tlb_parts_dict}. Checks texture_id against exactly the
    slots present in the dict (in ascending slot order) and returns (entry, slot) for the
    first match, or (None, None) if it doesn't resolve against any of them - meaning it
    likely carries a live HAL texture handle instead (unrecoverable). For the single-.TLB
    and auto-detect paths, callers pass the same parts dict under every slot 0..MAX_LIBS-1
    (brute-force, matching the old behaviour); the .RRI path passes the exact slot->library
    mapping recorded by the tool itself, which is more precise."""
    for slot in sorted(slot_to_parts):
        candidate = texture_id - slot * TLB_MAX_PARTS
        if candidate < 0:
            continue
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


def find_best_tlb(folder, unique_texture_ids, min_score=8):
    """Scan every .TLB directly inside `folder` (not recursive) and score each by how many
    of unique_texture_ids resolve against it via resolve_texture_id(). There's no reliable
    metadata anywhere (checked the unit CSV database - it only has damage-decal filenames)
    linking a model to the library it was painted from, so this brute-force score is the
    practical substitute: unrelated libraries share a handful of common low IDs (generic
    materials like flat black/green), so min_score filters that noise floor out - real
    matches score well above it in every case checked so far.
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
        flat = {slot: tlb_parts for slot in range(MAX_LIBS)}
        score = 0
        for tex_id in unique_texture_ids:
            if resolve_texture_id(tex_id, flat)[0] is not None:
                score += 1
        if score > best_score:
            best_score, best_path, best_parts = score, path, tlb_parts

    if best_path is None or best_score < min_score:
        return None, None, None, best_score

    return best_path, best_parts, find_atlas_image(best_path), best_score


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
    pack layout differs. Returns {slot_index: (tlb_parts, atlas_image_path)} for whichever
    slots actually resolve to a real file on disk - slots that don't (moved/renamed/missing
    library) are silently dropped rather than failing the whole import.
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
                resolved[slot] = (tlb_parts, find_atlas_image(abs_path))
                break
    return resolved


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


def _build_material(root_name, image_path):
    image = bpy.data.images.load(image_path, check_existing=True)
    material = bpy.data.materials.new(root_name + "_mat")
    material.use_nodes = True
    bsdf = material.node_tree.nodes.get("Principled BSDF")
    tex_node = material.node_tree.nodes.new("ShaderNodeTexImage")
    tex_node.image = image
    tex_node.interpolation = "Closest"  # this is 1999 paletted atlas art, keep it crisp
    if bsdf is not None:
        material.node_tree.links.new(tex_node.outputs["Color"], bsdf.inputs["Base Color"])
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


def build_blender_objects(parts, collection, root_name, slot_sources=None):
    """slot_sources: {slot_index: (tlb_parts, atlas_image_path)} or None for geometry-only
    import. A model can use several libraries at once (one per slot) - each gets its own
    material, built once here and shared across every part/mesh, since the same slot
    assignments apply model-wide."""
    slot_to_parts = {}
    slot_to_material = {}
    atlas_path_to_material = {}
    unresolved_material = None

    if slot_sources:
        unresolved_material = _build_unresolved_material()
        for slot, (tlb_parts, atlas_image_path) in slot_sources.items():
            slot_to_parts[slot] = tlb_parts
            if not atlas_image_path:
                continue
            material = atlas_path_to_material.get(atlas_image_path)
            if material is None:
                label = os.path.splitext(os.path.basename(atlas_image_path))[0]
                material = _build_material(f"{root_name}_{label}", atlas_image_path)
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

    objects = []
    for part in parts:
        type_id = part.obj_attribut & 0xFF
        hidden = bool(part.obj_attribut & OBJ_ATTRIB_HIDE)

        if part.faces:
            mesh = bpy.data.meshes.new(part.name)
            # Confirmed empirically: every part's raw vertices are already centered on
            # that part's own local origin (bounding boxes are symmetric around 0,0,0
            # independent of pivot), so we offset by pivot purely to place the object's
            # origin at the pivot point for correct future rotation - the mesh itself
            # needs no other correction.
            px, py, pz = part.pivot
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

        collection.objects.link(obj)
        # hide_set() needs the object linked into the view layer first, hence linking
        # before this rather than alongside the other obj[...] setup above.
        obj.hide_set(hidden)
        obj.hide_render = hidden
        objects.append(obj)

    for part, obj in zip(parts, objects):
        if part.parent_no is not None and 0 <= part.parent_no < len(objects):
            parent_obj = objects[part.parent_no]
            obj.parent = parent_obj
            # Pivots are absolute (root-relative), not parent-relative deltas - confirmed
            # by the gun barrel ("Kanone", parent chain Kanone->Blende->turm->Tiger) landing
            # far outside the hull when Blender's default parenting summed every ancestor's
            # pivot on top of each other. Setting matrix_parent_inverse from the parent's
            # own pivot (computed directly, not from Blender's live matrix_world, to avoid
            # depsgraph staleness while parenting in a loop) cancels that accumulation so
            # obj.location can stay the part's own absolute pivot at every hierarchy depth.
            parent_pivot = parts[part.parent_no].pivot
            obj.matrix_parent_inverse = Matrix.Translation(parent_pivot).inverted()

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
        name="Auto-detect TLB in Folder (fallback)",
        description="Optional - point at a folder (e.g. the Texture folder) and every "
                    ".TLB in it (not subfolders) is scored by how many of this model's "
                    "texture IDs it resolves; the best match is used automatically. Only "
                    "used if there's no .RRI (or Use .RRI is off) and Texture Library "
                    "(.TLB) above is blank",
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
                    slot_sources = {slot: (tlb_parts, atlas_image_path) for slot in range(MAX_LIBS)}
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

        if slot_sources is None and not self.tlb_filepath and self.tlb_search_folder:
            unique_ids = sorted({t for part in parts for t in part.face_texture_id if t is not None})
            best_path, tlb_parts, atlas_image_path, score = find_best_tlb(self.tlb_search_folder, unique_ids)
            if best_path is None:
                detect_msg = f" - auto-detect found no good TLB match among {len(unique_ids)} unique texture ID(s)"
            else:
                detect_msg = f" - auto-detected {os.path.basename(best_path)} ({score}/{len(unique_ids)} unique IDs matched)"
                if atlas_image_path is None:
                    self.report({"WARNING"}, f"Best TLB match {best_path} has no matching _24.BMP/_8.BMP - importing geometry only")
                else:
                    slot_sources = {slot: (tlb_parts, atlas_image_path) for slot in range(MAX_LIBS)}

        root_name = os.path.splitext(os.path.basename(self.filepath))[0]
        collection = bpy.data.collections.new(root_name)
        context.scene.collection.children.link(collection)

        objects, resolved_count, unresolved_count = build_blender_objects(
            parts, collection, root_name, slot_sources
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


def register():
    bpy.utils.register_class(IMPORT_OT_rrf)
    bpy.utils.register_class(EXPORT_OT_rrf_atlas)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    bpy.utils.unregister_class(EXPORT_OT_rrf_atlas)
    bpy.utils.unregister_class(IMPORT_OT_rrf)


if __name__ == "__main__":
    register()
