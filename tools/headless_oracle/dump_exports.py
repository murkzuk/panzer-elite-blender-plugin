"""Dump the PE export table of a DLL. Pure stdlib so it runs under the 32-bit embed."""
import struct
import sys


def exports(path):
    d = open(path, "rb").read()
    pe = struct.unpack_from("<I", d, 0x3C)[0]
    assert d[pe:pe + 4] == b"PE\0\0", "not a PE file"
    machine = struct.unpack_from("<H", d, pe + 4)[0]
    nsec = struct.unpack_from("<H", d, pe + 6)[0]
    optsz = struct.unpack_from("<H", d, pe + 20)[0]
    opt = pe + 24
    magic = struct.unpack_from("<H", d, opt)[0]
    # export dir is data directory 0; it sits after the fixed part of the opt header
    ddoff = opt + (96 if magic == 0x10B else 112)
    exp_rva, exp_sz = struct.unpack_from("<II", d, ddoff)

    secs = []
    so = opt + optsz
    for i in range(nsec):
        s = so + i * 40
        name = d[s:s + 8].rstrip(b"\0").decode("latin-1")
        vsz, va, rsz, ra = struct.unpack_from("<IIII", d, s + 8)
        secs.append((name, va, vsz, ra, rsz))

    def r2o(rva):
        for name, va, vsz, ra, rsz in secs:
            if va <= rva < va + max(vsz, rsz):
                return ra + (rva - va)
        return None

    out = {"machine": machine, "bits": 32 if magic == 0x10B else 64, "names": []}
    if not exp_rva:
        return out
    e = r2o(exp_rva)
    ordbase = struct.unpack_from("<I", d, e + 16)[0]
    nfunc, nname = struct.unpack_from("<II", d, e + 20)
    afunc, aname, aord = struct.unpack_from("<III", d, e + 28)
    of, on, oo = r2o(afunc), r2o(aname), r2o(aord)
    for i in range(nname):
        nrva = struct.unpack_from("<I", d, on + i * 4)[0]
        no = r2o(nrva)
        end = d.index(b"\0", no)
        nm = d[no:end].decode("latin-1")
        idx = struct.unpack_from("<H", d, oo + i * 2)[0]
        frva = struct.unpack_from("<I", d, of + idx * 4)[0]
        out["names"].append((nm, idx + ordbase, frva))
    out["nfunc"] = nfunc
    return out


if __name__ == "__main__":
    for p in sys.argv[1:]:
        r = exports(p)
        print("=== %s  machine=0x%x %d-bit  %d exports" %
              (p.split("\\")[-1], r["machine"], r["bits"], len(r["names"])))
        for nm, o, rva in sorted(r["names"]):
            print("   %-34s ord=%-4d rva=0x%06x" % (nm, o, rva))
