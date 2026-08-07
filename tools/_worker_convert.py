"""Blender-side worker for batch_import_gui.pyw - converts ONE .rrf file per
invocation (run as a fresh Blender subprocess per file, not a long-running loop -
see the GUI's own header for why: a real Panzer Elite RRF, Psw232.RRF, was found to
hang bmesh.ops.recalc_face_normals() indefinitely on degenerate geometry in one part
during this project's own real batch conversion work. That specific bug has since
been fixed in io_import_rrf.py itself, but a fresh-subprocess-per-file design stays
the right call for genuinely unattended batch work regardless - it's cheap insurance
against whatever the NEXT unknown edge case turns out to be, and it's exactly what
let today's real batch recover cleanly from that hang instead of losing 4+ hours to
it with no way to skip past the one bad file.

Usage: blender --background --python _worker_convert.py -- <rrf_path> <out_blend_path>
    <apply_scale:0|1> <snap_ground:0|1> <flip_forward:0|1> <wheel_cylinders:0|1>
    <do_uv_paint:0|1> <faction> <basecolor_png>

faction is one of: soviet, axis, western, skip, none ("none" = do_uv_paint is 0,
value ignored).
"""
import bpy
import bmesh
import math
import sys
import os
from mathutils import Vector, Matrix

ADDON_DIR = os.path.join(
    os.environ.get("APPDATA", ""), "Blender Foundation", "Blender", "5.1", "scripts", "addons"
)
sys.path.insert(0, ADDON_DIR)
import io_import_rrf
io_import_rrf.register()

# Same real reference swatches this project's own batch work validated - see
# faction_map.json's own header for the full real citation (KV1/Pz4H/Matilda glbs,
# cross-checked against L:\2025\Low Poly 2025\katusha's own separately-painted color).
FACTION_UV = {
    "soviet": (0.20635804533958435, 0.22212529182434082),
    "axis": (0.19790726900100708, 0.2366962432861328),
    "western": (0.13104063272476196, 0.20703136920928955),
}

# Real, deliberate exception to per-faction paint (2026-08-07): every real tank's
# tracks are dark grease/dirt-darkened metal regardless of hull paint color, not
# faction-colored. Confirmed via this project's own already-working reference
# models: KV176.glb's own trackL samples RGB(64,64,74) from the shared atlas vs.
# Pz4H's own different track UV sampling RGB(115,115,125) from the same atlas -
# real but inconsistent per-model values, not one true shared swatch already in
# the source data. Picked KV1's own (darker, more traditionally correct for a
# grease/dirt/shadow-darkened track) as the one canonical swatch applied to every
# vehicle's tracks going forward, any faction.
TRACK_UV = (0.19996348023414612, 0.23820853233337402)

WHEEL_SIDES = 16
ROLLER_SIDES = 12


def get_or_make_shared_material(basecolor_png_path):
    mat = bpy.data.materials.get("PE_SharedAtlas")
    if mat is not None:
        return mat
    mat = bpy.data.materials.new("PE_SharedAtlas")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    bsdf = nodes.get("Principled BSDF")
    tex_node = nodes.new("ShaderNodeTexImage")
    img = bpy.data.images.get(os.path.splitext(os.path.basename(basecolor_png_path))[0])
    if img is None:
        img = bpy.data.images.load(basecolor_png_path)
    tex_node.image = img
    tex_node.interpolation = "Closest"
    links.new(tex_node.outputs["Color"], bsdf.inputs["Base Color"])
    return mat


def replace_with_cylinder(obj, n_sides):
    """Real per-object geometry analysis (not a hardcoded axis) - the existing flat
    wheel/roller polyface already has the CORRECT diameter and width (confirmed live
    on Tiger1's wheel_0: 20-sided outline, real hub/rim radius variation, genuine
    if thin ~0.71-unit local depth), so this measures those real dimensions from the
    object's own mesh data and builds a proper round N-sided cylinder to match - not
    inventing new proportions, just replacing the thin/faceted disc with a
    correctly-proportioned round one. Prototyped and visually verified on Tiger1
    (16/16 wheel_N objects) before being applied here to the full roster."""
    verts = [v.co for v in obj.data.vertices]
    xs = [v.x for v in verts]; ys = [v.y for v in verts]; zs = [v.z for v in verts]
    extents = {0: max(xs) - min(xs), 1: max(ys) - min(ys), 2: max(zs) - min(zs)}
    axle_axis = min(extents, key=extents.get)
    other_axes = [i for i in range(3) if i != axle_axis]

    # Real sanity check, corrected after a real false-positive rejection: Tiger1's
    # own "idler" is a compound multi-part object (X-extent 21.5 vs Y/Z ~3.8 - both
    # side idlers + connecting geometry, not one symmetric wheel), but an axle-vs-
    # radius thickness ratio check (the first version of this) ALSO wrongly rejected
    # Pz4H's real, legitimately thicker single wheel disc (axle 1.72 vs radius-plane
    # 3.23/3.23 - a real, valid wheel, just proportionally thicker than Tiger1's).
    # The real discriminating signal is whether the disc is actually circular: its
    # two radius-plane extents should be close to each other regardless of how deep
    # the axle direction is. Idler's own two candidate radius-plane axes were 21.5
    # vs 3.79 (wildly unequal, not a circle at all); every real wheel/roller tested
    # has them within a few percent of each other.
    radius_extents = [extents[i] for i in other_axes]
    larger, smaller = max(radius_extents), min(radius_extents)
    if smaller == 0 or larger / smaller > 1.3:
        raise ValueError(
            f"radius-plane extents {radius_extents} aren't close to equal - not a "
            f"circular wheel/roller disc"
        )

    center = Vector((sum(xs) / len(xs), sum(ys) / len(ys), sum(zs) / len(zs)))
    radius = max(
        math.hypot(v[other_axes[0]] - center[other_axes[0]], v[other_axes[1]] - center[other_axes[1]])
        for v in verts
    )
    axle_vals = [v[axle_axis] for v in verts]
    depth = max(axle_vals) - min(axle_vals)

    bm = bmesh.new()
    bmesh.ops.create_cone(
        bm, cap_ends=True, cap_tris=False, segments=n_sides,
        radius1=radius, radius2=radius, depth=depth,
    )
    if axle_axis == 0:
        bmesh.ops.rotate(bm, verts=bm.verts, cent=(0, 0, 0), matrix=Matrix.Rotation(math.radians(90), 3, 'Y'))
    elif axle_axis == 1:
        bmesh.ops.rotate(bm, verts=bm.verts, cent=(0, 0, 0), matrix=Matrix.Rotation(math.radians(90), 3, 'X'))
    bmesh.ops.translate(bm, verts=bm.verts, vec=center)

    new_mesh = bpy.data.meshes.new(obj.data.name + "_cyl")
    bm.to_mesh(new_mesh)
    bm.free()

    old_mesh = obj.data
    obj.data = new_mesh
    bpy.data.meshes.remove(old_mesh)


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    (src_path, out_path, apply_scale, snap_ground, flip_forward, wheel_cylinders,
     do_uv_paint, faction, basecolor_png) = argv[:9]
    apply_scale = apply_scale == "1"
    snap_ground = snap_ground == "1"
    flip_forward = flip_forward == "1"
    wheel_cylinders = wheel_cylinders == "1"
    do_uv_paint = do_uv_paint == "1"

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)

    result = bpy.ops.import_scene.pe_rrf(
        filepath=src_path,
        use_rri=True,
        apply_real_world_scale=apply_scale,
        snap_to_ground=snap_ground,
        flip_to_positive_y_forward=flip_forward,
    )
    if result != {"FINISHED"}:
        raise RuntimeError(f"importer returned {result}, not FINISHED")

    cyl_count = 0
    cyl_skipped = 0
    if wheel_cylinders:
        for obj in list(bpy.data.objects):
            if obj.type != "MESH":
                continue
            name_lower = obj.name.lower()
            if "wheel" in name_lower:
                n_sides = WHEEL_SIDES
            elif any(k in name_lower for k in ("roller", "cog")):
                n_sides = ROLLER_SIDES
            else:
                continue
            try:
                replace_with_cylinder(obj, n_sides)
                cyl_count += 1
            except ValueError:
                cyl_skipped += 1

    touched = 0
    if do_uv_paint and faction != "none":
        shared_mat = get_or_make_shared_material(basecolor_png) if os.path.exists(basecolor_png) else None
        for obj in list(bpy.data.objects):
            if obj.type != "MESH":
                continue
            bpy.context.view_layer.objects.active = obj
            obj.select_set(True)
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.mesh.select_all(action="SELECT")
            bpy.ops.uv.smart_project()
            bpy.ops.object.mode_set(mode="OBJECT")
            obj.select_set(False)

            uv_layer = obj.data.uv_layers.active
            if uv_layer is None or len(uv_layer.data) == 0:
                continue

            if "track" in obj.name.lower():
                target = TRACK_UV
            elif faction == "skip" or faction not in FACTION_UV:
                n = len(uv_layer.data)
                target = (sum(l.uv.x for l in uv_layer.data) / n,
                          sum(l.uv.y for l in uv_layer.data) / n)
            else:
                target = FACTION_UV[faction]
            for loop in uv_layer.data:
                loop.uv.x = target[0]
                loop.uv.y = target[1]

            if shared_mat is not None:
                obj.data.materials.clear()
                obj.data.materials.append(shared_mat)
            touched += 1

    obj_count = len(bpy.data.objects)
    bpy.ops.wm.save_as_mainfile(filepath=out_path)
    print(f"RESULT_OK objects={obj_count} painted={touched} cylinders={cyl_count} cyl_skipped={cyl_skipped} faction={faction}")


main()
