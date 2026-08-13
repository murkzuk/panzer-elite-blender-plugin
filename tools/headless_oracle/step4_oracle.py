"""Step 4: THE ORACLE. Ask the engine, per face, for its own texture rect fields, and
diff them against what the file bytes say.

rrGetMaterialSelection() -> rrGetSelection() (Rrdwire.c:~860) computes, for a selected
face, exactly the values this project has been arguing about:

    xOfset     = (textureOfset >> 24) & 0xf
    yOfset     = (textureOfset >> 28) & 0xf
    *tInfo     = finalID | (yOfset << 28) | (xOfset << 24)
    *tInfo2    = (materialInfo & 0xff00) >> 8
    uvFace->v1 = v1 >> 16   (and v2, v3, v4 = textureHalf >> 16)

None of that needs a texture library loaded (only finalID does), so this runs on a bare
init. Read-only: the model is a copy and nothing is saved.

Usage: step4_oracle.py <model.rrf> [max_faces_per_part]
"""
import ctypes
import os
import struct
import sys

OE = r"M:\Users\jeff\Desktop\Old Desktop\OE_2"
MODEL = sys.argv[1]
PER_PART = int(sys.argv[2]) if len(sys.argv) > 2 else 6
MAXOBJ = 512
FACE_MODE = 0


class rrSelInfo(ctypes.Structure):
    _fields_ = [("modeType", ctypes.c_int32), ("objNo", ctypes.c_int32),
                ("nummer", ctypes.c_int32), ("textureId", ctypes.c_int32)]


class rrUVFace(ctypes.Structure):
    _fields_ = [("v1", ctypes.c_int32), ("v2", ctypes.c_int32),
                ("v3", ctypes.c_int32), ("v4", ctypes.c_int32)]


os.chdir(OE)
ctypes.WinDLL("kernel32").SetDllDirectoryW(OE)
dll = ctypes.WinDLL(os.path.join(OE, "rrobjx5.dll"))
for nm, res, args in (
        ("_rrInitRender", None, []),
        ("_rrSetRenderSize", None, [ctypes.c_int, ctypes.c_int]),
        ("_rrLoadGameMesh", None, [ctypes.c_char_p])):
    f = getattr(dll, nm); f.restype = res; f.argtypes = args
dll._rrInitRender()
dll._rrSetRenderSize(800, 600)
dll._rrLoadGameMesh(MODEL.encode("mbcs"))

names = ctypes.create_string_buffer(80 * MAXOBJ)
info = (rrSelInfo * (1024 * MAXOBJ))()
counts = (ctypes.c_int32 * MAXOBJ)()
atype = (ctypes.c_int32 * MAXOBJ)()
apara = (ctypes.c_int32 * MAXOBJ)()
ga = dll._rrGetAllObjects
ga.restype = ctypes.c_int32
ga.argtypes = [ctypes.c_char_p, ctypes.POINTER(rrSelInfo), ctypes.POINTER(ctypes.c_int32),
               ctypes.POINTER(ctypes.c_int32), ctypes.POINTER(ctypes.c_int32)]
nobj = ga(names, info, counts, atype, apara)

gm = dll._rrGetMaterialSelection
gm.restype = None
gm.argtypes = [ctypes.c_int32, ctypes.POINTER(rrSelInfo)] + \
              [ctypes.POINTER(ctypes.c_uint32)] * 5 + [ctypes.POINTER(rrUVFace)]

# --- the same faces, straight out of the file ---
raw = open(MODEL, "rb").read()


def file_face(part, fidx):
    """Locate a face record in the file. Header 20 + part array 512*N; the mesh region
    offset is read from the part entry itself rather than assumed."""
    hdr = 20
    ent = hdr + part * 512
    mesh = struct.unpack_from("<I", raw, ent + 224)[0]
    fc, fl = struct.unpack_from("<II", raw, mesh + 4)
    if fidx >= fc:
        return None
    return struct.unpack_from("<IIIIII", raw, fl + fidx * 24)


print("model: %s   parts: %d" % (os.path.basename(MODEL), nobj))
print()
hdr = ("%-4s %-4s | %-10s %-8s | %-9s %-9s %-9s %-9s | file textureOfset  materialInfo"
       % ("part", "face", "tInfo", "tInfo2", "uv.v1", "uv.v2", "uv.v3", "uv.v4"))
print(hdr)
print("-" * len(hdr))

mismatch = 0
checked = 0
for o in range(min(nobj, MAXOBJ)):
    n = counts[o]
    for f in range(min(n, PER_PART)):
        sel = (rrSelInfo * 1)()
        sel[0].modeType = FACE_MODE
        sel[0].objNo = o
        sel[0].nummer = f
        sel[0].textureId = 0
        mi = ctypes.c_uint32(0); col = ctypes.c_uint32(0); same = ctypes.c_uint32(0)
        t1 = ctypes.c_uint32(0); t2 = ctypes.c_uint32(0)
        uv = rrUVFace()
        gm(1, sel, ctypes.byref(mi), ctypes.byref(col), ctypes.byref(same),
           ctypes.byref(t1), ctypes.byref(t2), ctypes.byref(uv))

        ff = file_face(o, f)
        if ff is None:
            continue
        fv1, fv2, fv3, fto, fth, fmi = ff
        checked += 1
        ok = ((uv.v1 & 0xFFFF) == (fv1 >> 16) and (uv.v3 & 0xFFFF) == (fv3 >> 16))
        if not ok:
            mismatch += 1
        if o < 4 and f < 4:
            print("%-4d %-4d | 0x%08x %-8d | %-9d %-9d %-9d %-9d | 0x%08x  0x%08x %s"
                  % (o, f, t1.value, t2.value, uv.v1, uv.v2, uv.v3, uv.v4, fto, fmi,
                     "" if ok else "  <-- differs from file"))

print()
print("faces queried: %d   uv fields differing from a plain (field>>16) of the file: %d"
      % (checked, mismatch))
