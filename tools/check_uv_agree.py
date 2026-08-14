"""Do Blender's UVs and the ENGINE's reading of the same face agree?

Reads each face's rect out of the .RRF exactly as rrUsedSelection() does, maps it to the
corner each vertex should get (v1 top-right, v2 top-left, v3 bottom-left, v4 bottom-right),
and compares against the UVs the importer put in Blender.
"""
import bpy, sys, struct
MODEL = sys.argv[sys.argv.index("--")+1:][0]
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.preferences.addon_enable(module='io_import_rrf')
import io_import_rrf as rrf
bpy.ops.import_scene.pe_rrf(filepath=MODEL)
ATLAS_W, ATLAS_H = 256, 4096
lib = rrf.read_tlb(MODEL.replace(".RRF", ".TLB"))
d = rrf.read_rrf_raw(MODEL)
oc = struct.unpack_from("<I", d, 4)[0]
names = {}
for p in range(oc):
    raw = bytes(d[rrf.HEADER_SIZE + p*rrf.PART_SIZE:][:24])
    names[raw.split(b"\x00")[0].decode("mbcs","replace")] = p
agree = disagree = 0
for ob in bpy.data.objects:
    if ob.type != "MESH" or not ob.data.uv_layers: continue
    key = next((k for k in names if k and k in ob.name), None)
    if key is None: continue
    p = names[key]
    mo = rrf._mesh_record_offset(p, 0)
    fc, fl = struct.unpack_from("<II", d, mo+4)
    uv = ob.data.uv_layers[0].data
    for poly in ob.data.polygons:
        if poly.index >= fc: continue
        v1,v2,v3,to,th,mi = struct.unpack_from("<IIIIII", d, fl+poly.index*24)
        if not (to & 0x80000000): continue
        _u,s,pid = rrf.decode_texture_offset(to)
        e = lib.get(pid)
        if not e: continue
        px,py,esx,esy = e
        SizeX=(v1>>16)&0xFF;  SizeX+=1 if SizeX else 0
        SizeY=(v3>>24)&0xFF;  SizeY+=1 if SizeY else 0
        StartX=(v3>>16)&0xFF; StartX+=1 if StartX else 0
        StartY=(v1>>24)&0xFF; StartY+=1 if StartY else 0
        x0,y0 = px*16+StartX, py*16+StartY
        x1,y1 = x0+max(SizeX-1,0), y0+max(SizeY-1,0)
        # what Blender actually has
        us=[uv[li].uv[0]*ATLAS_W for li in poly.loop_indices]
        vs=[(1.0-uv[li].uv[1])*ATLAS_H for li in poly.loop_indices]
        bx0,bx1,by0,by1 = min(us),max(us),min(vs),max(vs)
        if abs(bx0-x0)<=1.5 and abs(bx1-x1)<=1.5 and abs(by0-y0)<=1.5 and abs(by1-y1)<=1.5:
            agree += 1
        else:
            disagree += 1
print("R: faces where Blender's UV rect matches the engine's: %d" % agree)
print("R: faces where they DISAGREE                          : %d" % disagree)
