"""Step 2: bring the engine up headlessly, as far as it will go.

Each stage prints BEFORE and AFTER so that if the process dies we know exactly which
call killed it. Read-only: nothing is written, and the model is a copy.

Stage order follows rrobjpex.c:
  rrInitRender()      -> rrInitRenderSystem(WIN_SYSTEM,"") : reads Setting.HAL, then
                         InitHalProcs("OBJHALX5.DLL"). Returns early if Setting.HAL is
                         missing, so this is safe to attempt.
  rrSetRenderSize()   -> may be needed before anything allocates buffers.
  rrLoadGameMesh(p)   -> loadTank(testScene, name); sets actTank[0], which every
                         selection query requires.
"""
import ctypes
import os
import sys

OE = r"M:\Users\jeff\Desktop\Old Desktop\OE_2"
STAGE = sys.argv[1] if len(sys.argv) > 1 else "init"
MODEL = sys.argv[2] if len(sys.argv) > 2 else None


def say(msg):
    print(msg, flush=True)


os.chdir(OE)
ctypes.WinDLL("kernel32").SetDllDirectoryW(OE)
say("cwd = %s" % os.getcwd())
say("Setting.HAL present: %s" % os.path.exists(os.path.join(OE, "Setting.HAL")))

dll = ctypes.WinDLL(os.path.join(OE, "rrobjx5.dll"))
say("loaded rrobjx5.dll")

if STAGE in ("init", "load"):
    fn = dll._rrInitRender
    fn.restype = None
    fn.argtypes = []
    say(">>> calling rrInitRender()")
    fn()
    say("<<< rrInitRender returned")

    # Did the HAL get pulled in?
    k32 = ctypes.WinDLL("kernel32")
    k32.GetModuleHandleW.restype = ctypes.c_void_p
    for m in ("OBJHALX5.DLL", "rrnop.dll", "OBJHAL3D.dll"):
        h = k32.GetModuleHandleW(m)
        say("    module %-14s loaded: %s" % (m, bool(h)))

if STAGE == "load":
    if not MODEL:
        say("no model given"); sys.exit(0)
    fn = dll._rrSetRenderSize
    fn.restype = None
    fn.argtypes = [ctypes.c_int, ctypes.c_int]
    say(">>> calling rrSetRenderSize(800,600)")
    fn(800, 600)
    say("<<< rrSetRenderSize returned")

    lg = dll._rrLoadGameMesh
    lg.restype = None
    lg.argtypes = [ctypes.c_char_p]
    say(">>> calling rrLoadGameMesh(%s)" % MODEL)
    lg(MODEL.encode("mbcs"))
    say("<<< rrLoadGameMesh returned")

say("STAGE %s COMPLETE" % STAGE)
