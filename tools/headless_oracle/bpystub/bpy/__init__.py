"""Import-time stand-in for Blender's bpy, so io_import_rrf.py's pure binary-format
functions can be exercised outside Blender. bpy.types.* must yield real classes because
the addon subclasses them at module level."""
import sys as _sys, types as _t


class _Any:
    def __init__(self, *a, **k): pass
    def __call__(self, *a, **k): return _Any()
    def __getattr__(self, n): return _Any()
    def __setattr__(self, n, v): pass
    def __getitem__(self, k): return _Any()


class _TypesModule(_t.ModuleType):
    def __getattr__(self, n):
        c = type(n, (object,), {"bl_rna": None})
        setattr(self, n, c)
        return c


class _FuncModule(_t.ModuleType):
    def __getattr__(self, n):
        return lambda *a, **k: _Any()


types = _TypesModule("bpy.types"); _sys.modules["bpy.types"] = types
for _n in ("props", "utils", "ops", "app", "path"):
    _m = _FuncModule("bpy." + _n)
    _sys.modules["bpy." + _n] = _m
    globals()[_n] = _m
context = _Any()
data = _Any()
