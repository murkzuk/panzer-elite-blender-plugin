"""Step 3: prove the engine actually holds the mesh, by reading its own part names and
face counts back out via rrGetAllObjects().

If the names match what the .RRF file says, actTank[0] is populated and every selection
query - including rrGetUsedSelection - is now reachable.

Buffer sizes come from rrGetAllInfos() (Rrdwire.c):
   nameList        80 bytes per object
   infoBlockList   1024 rrSelInfo (16 bytes) per object
   countList       one int32 per object
"""
import ctypes
import os
import sys

OE = r"M:\Users\jeff\Desktop\Old Desktop\OE_2"
MODEL = sys.argv[1]
MAXOBJ = 512


class rrSelInfo(ctypes.Structure):
    _fields_ = [("modeType", ctypes.c_int32), ("objNo", ctypes.c_int32),
                ("nummer", ctypes.c_int32), ("textureId", ctypes.c_int32)]


os.chdir(OE)
ctypes.WinDLL("kernel32").SetDllDirectoryW(OE)
dll = ctypes.WinDLL(os.path.join(OE, "rrobjx5.dll"))

dll._rrInitRender.restype = None
dll._rrInitRender.argtypes = []
dll._rrInitRender()

dll._rrSetRenderSize.restype = None
dll._rrSetRenderSize.argtypes = [ctypes.c_int, ctypes.c_int]
dll._rrSetRenderSize(800, 600)

dll._rrLoadGameMesh.restype = None
dll._rrLoadGameMesh.argtypes = [ctypes.c_char_p]
dll._rrLoadGameMesh(MODEL.encode("mbcs"))
print("loaded:", MODEL, flush=True)

names = ctypes.create_string_buffer(80 * MAXOBJ)
info = (rrSelInfo * (1024 * MAXOBJ))()
counts = (ctypes.c_int32 * MAXOBJ)()
atype = (ctypes.c_int32 * MAXOBJ)()
apara = (ctypes.c_int32 * MAXOBJ)()

fn = dll._rrGetAllObjects
fn.restype = ctypes.c_int32
fn.argtypes = [ctypes.c_char_p, ctypes.POINTER(rrSelInfo), ctypes.POINTER(ctypes.c_int32),
               ctypes.POINTER(ctypes.c_int32), ctypes.POINTER(ctypes.c_int32)]
print(">>> rrGetAllObjects", flush=True)
n = fn(names, info, counts, atype, apara)
print("<<< returned %d" % n, flush=True)

shown = 0
for i in range(min(n if 0 < n < MAXOBJ else 0, MAXOBJ)):
    nm = bytes(names[80 * i:80 * i + 80]).split(b"\0")[0].decode("mbcs", "replace")
    print("   part %3d  %-24s faces=%-6d attr=0x%08x" % (i, nm, counts[i], atype[i]))
    shown += 1
    if shown >= 25:
        print("   ... (%d more)" % (n - 25))
        break
