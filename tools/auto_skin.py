"""Headless: unwrap every part of a model and give each its own private skin.

Settings are the ones established by measurement:
  Rotation Method  axis-aligned   (PE stores axis-aligned rectangles)
  Correct Aspect   OFF            (the repacker treats UV space as square and does its
                                   own conversion - correcting here applies it twice:
                                   38 clamped islands vs 13)
  Angle Limit      45 deg
  Island Margin    0              (apply_private_skin repacks with its own margin_px=2)

Each part ends up with its own <model>_<part>_private.TLB + _8.BMP, and the .RRF is
rewritten in place to point at them. Merging those into one library is a separate step.
"""
import bpy
import bmesh
import math
import sys

argv = sys.argv[sys.argv.index("--") + 1:]
MODEL = argv[0]
N_PARTS_HINT = float(argv[1]) if len(argv) > 1 else 5.0
BUDGET = max(0.01, min(0.95, 0.55 / N_PARTS_HINT))

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.preferences.addon_enable(module='io_import_rrf')
bpy.ops.import_scene.pe_rrf(filepath=MODEL)

meshes = [o for o in bpy.data.objects if o.type == "MESH" and len(o.data.polygons)]

# Split the atlas budget by each part's REAL SURFACE AREA, not equally.
#
# An equal split gives a 4-face machine gun the same share as a 106-face hull, so its
# tiny faces each land an enormous rectangle - measured at 27,989 texels per unit of area
# against the hull's ~150, a 600x density spread. Weighting by area keeps texel density
# roughly consistent across the whole vehicle.
areas = {}
for o in meshes:
    areas[o.name] = sum(p.area for p in o.data.polygons) or 1e-9
total_area = sum(areas.values())
TOTAL_BUDGET = 0.55
print("R: %d part(s), total atlas budget %.2f split by surface area" % (len(meshes), TOTAL_BUDGET))

for ob in meshes:
    bpy.ops.object.select_all(action='DESELECT')
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)

    if "pe_rrf_filepath" not in ob or "pe_part_index" not in ob:
        print("R:   %-12s SKIP (not a plugin-imported part)" % ob.name)
        continue

    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.smart_project(angle_limit=math.radians(45.0),
                             island_margin=0.0,
                             area_weight=0.0,
                             correct_aspect=False,
                             scale_to_bounds=False)
    try:
        # Each part may claim only its share of the atlas, so the five private skins
        # can later be merged into ONE library. The default 0.6 assumes the part owns the
        # whole atlas, which overflows the merge five times over.
        share = max(0.01, min(0.95, TOTAL_BUDGET * areas[ob.name] / total_area))
        res = bpy.ops.mesh.pe_give_private_skin(budget_fraction=share, per_face=True)
        status = str(res)
    except Exception as exc:
        status = "FAILED: %s" % exc
    bpy.ops.object.mode_set(mode='OBJECT')
    print("R:   %-12s polys=%-4d area=%8.1f budget=%.4f -> %s"
          % (ob.name, len(ob.data.polygons), areas[ob.name],
             TOTAL_BUDGET * areas[ob.name] / total_area, status))

print("R: done")
