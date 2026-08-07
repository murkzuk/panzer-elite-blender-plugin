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
    <apply_scale:0|1> <snap_ground:0|1> <do_uv_paint:0|1> <faction>

faction is one of: soviet, axis, western, skip, none ("none" = do_uv_paint is 0,
value ignored).
"""
import bpy
import sys
import os

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


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    src_path, out_path, apply_scale, snap_ground, do_uv_paint, faction, basecolor_png = argv[:7]
    apply_scale = apply_scale == "1"
    snap_ground = snap_ground == "1"
    do_uv_paint = do_uv_paint == "1"

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)

    result = bpy.ops.import_scene.pe_rrf(
        filepath=src_path,
        use_rri=True,
        apply_real_world_scale=apply_scale,
        snap_to_ground=snap_ground,
    )
    if result != {"FINISHED"}:
        raise RuntimeError(f"importer returned {result}, not FINISHED")

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

            if faction == "skip" or faction not in FACTION_UV:
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
    print(f"RESULT_OK objects={obj_count} painted={touched} faction={faction}")


main()
