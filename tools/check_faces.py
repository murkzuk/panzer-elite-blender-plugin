"""Quads vs triangles, and how planar/rectangular the quads are - a rectangle can only be
mapped onto a face without distortion if the face is itself a planar rectangle."""
import bpy, sys
from mathutils import Vector
MODEL = sys.argv[sys.argv.index("--")+1:][0]
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.preferences.addon_enable(module='io_import_rrf')
bpy.ops.import_scene.pe_rrf(filepath=MODEL)
tri=quad=other=0; rectish=0; skewed=[]
for ob in bpy.data.objects:
    if ob.type!="MESH": continue
    for poly in ob.data.polygons:
        n=len(poly.vertices)
        if n==3: tri+=1; continue
        if n!=4: other+=1; continue
        quad+=1
        vs=[ob.data.vertices[v].co for v in poly.vertices]
        # corner angles: a rectangle has four 90deg corners
        worst=0
        for i in range(4):
            a=(vs[(i-1)%4]-vs[i]).normalized()
            b=(vs[(i+1)%4]-vs[i]).normalized()
            import math
            ang=math.degrees(math.acos(max(-1,min(1,a.dot(b)))))
            worst=max(worst,abs(ang-90))
        if worst<=10: rectish+=1
        else: skewed.append((worst, ob.name, poly.index))
tot=tri+quad+other
print("R: faces %d   triangles %d (%.0f%%)   quads %d (%.0f%%)"%(tot,tri,100.0*tri/tot,quad,100.0*quad/tot))
if quad:
    print("R: quads within 10deg of a true rectangle: %d of %d (%.0f%%)"%(rectish,quad,100.0*rectish/quad))
skewed.sort(reverse=True)
for s in skewed[:4]:
    print("R:   worst quad %.0f deg off square  %s face %d"%s)
