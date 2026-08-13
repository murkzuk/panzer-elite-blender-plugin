from bpy import _Any
import sys, types
ops=_Any(); types_=_Any()
for _n in ("ops","types"):
    _m=types.ModuleType("bmesh."+_n); _m.__getattr__=lambda n: _Any(); sys.modules["bmesh."+_n]=_m
def new(*a,**k): return _Any()
def from_edit_mesh(*a,**k): return _Any()
def update_edit_mesh(*a,**k): return _Any()
