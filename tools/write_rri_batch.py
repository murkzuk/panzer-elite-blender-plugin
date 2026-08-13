"""Write .RRI files across a folder using the THEATRE-SET rule, so ObjEdit stops showing
models untextured that the game renders perfectly.

Why this exists
---------------
The game never searches for texture libraries. A real install's Texture folder is numbered
per theatre - Normandy1..6, Italy1..6, Desert1..8 - and the game loads that set in order,
so a face's library slot is simply an index into it:

    slot N  ->  <Theatre>(N+1).TLB

ObjEdit does not do that. It wants an explicit .RRI, and almost none exist on disk, which
is why models that play fine load broken (often flat untextured) in the editor. Writing
the .RRI the game's rule implies fixes them in ObjEdit and in this project's Blender
importer alike.

Safety
------
- **Dry run by default.** Nothing is written without --write.
- **Never overwrites an existing .RRI** unless --force is passed; a real .RRI that shipped
  with a model is better evidence than any rule and is left alone.
- Only writes for models where every used slot resolves to a library that is actually on
  disk, unless --partial is passed.

Usage
-----
    python write_rri_batch.py "K:\\Panzer Elite\\Normandy_Obj"            # dry run
    python write_rri_batch.py "K:\\Panzer Elite\\Normandy_Obj" --write
    python write_rri_batch.py "K:\\Panzer Elite" --recursive --write

The theatre is taken from each model's own folder name, so --recursive handles a whole
install: Normandy_Obj models get Normandy libraries, Italy_Obj get Italy, and so on.
Folders that are not recognised theatre folders are skipped.
"""
import argparse
import importlib.util
import os
import sys

THEATRE_SET_RANGE = range(8)  # Desert goes to 8; Normandy/Italy to 6
PLUGIN = r"L:/2025/PE/PE SOURCE/BlenderRRFPlugin/io_import_rrf.py"


def load_plugin():
    """Import the add-on outside Blender, via the bpy stubs next to this script."""
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, "headless_oracle", "bpystub"),
                 os.path.join(here, "..", "tools", "headless_oracle", "bpystub")):
        if os.path.isdir(cand):
            sys.path.insert(0, cand)
            break
    spec = importlib.util.spec_from_file_location("io_import_rrf", PLUGIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def texture_folder_for(rrf_path, override=None):
    if override:
        return override
    parent = os.path.dirname(os.path.dirname(os.path.abspath(rrf_path)))
    for name in ("Texture", "texture", "TEXTURE"):
        cand = os.path.join(parent, name)
        if os.path.isdir(cand):
            return cand
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", help="folder of .RRF models (a theatre folder, or an install root with --recursive)")
    ap.add_argument("--write", action="store_true", help="actually write files (default is a dry run)")
    ap.add_argument("--force", action="store_true", help="overwrite an existing .RRI (NOT recommended - a real .RRI outranks the rule)")
    ap.add_argument("--full-set", action="store_true", help="list the whole theatre set rather than only the slots the model uses (makes ObjEdit load libraries it does not need, which can trip its 'Texture ID Too High!' check)")
    ap.add_argument("--absolute", action="store_true",
                    help=("write full absolute library paths instead of relative ones. "
                          "ObjEdit chdir()s to its OWN folder before resolving an .RRI, so "
                          "a relative path silently loads whatever library sits beside "
                          "ObjEdit - possibly a different edition of the same name. Real "
                          "shipped .RRI files use both styles."))
    ap.add_argument("--partial", action="store_true", help="write even when some slots have no library on disk")
    ap.add_argument("--recursive", action="store_true", help="walk subfolders, using each model's own folder to pick its theatre")
    ap.add_argument("--texture-folder", default=None, help="override the Texture folder")
    args = ap.parse_args()

    rrf = load_plugin()

    models = []
    if args.recursive:
        for root, _dirs, files in os.walk(args.folder):
            models += [os.path.join(root, f) for f in files if f.lower().endswith(".rrf")]
    else:
        models = [os.path.join(args.folder, f) for f in os.listdir(args.folder)
                  if f.lower().endswith(".rrf")]
    models.sort()

    written = skipped_existing = skipped_no_theatre = skipped_unresolved = failed = 0
    print("%s %d model(s)%s\n" % ("Scanning" if not args.write else "Writing for",
                                  len(models), "" if args.write else "  (DRY RUN - pass --write to apply)"))

    for m in models:
        theatre = rrf.theatre_prefix_from_path(m)
        if not theatre:
            skipped_no_theatre += 1
            continue
        rri_path = os.path.splitext(m)[0] + ".RRI"
        if os.path.exists(rri_path) and not args.force:
            skipped_existing += 1
            continue
        tex = texture_folder_for(m, args.texture_folder)
        if not tex:
            skipped_unresolved += 1
            continue
        try:
            parts = rrf.read_rrf(m)
            # Ask which slots are used with the SAME library-availability knowledge the
            # resolver uses. Computing it without that re-introduces the +2048 hack's
            # phantom slots (Desert 16/17/23) and skips models the resolver can handle.
            try:
                on_disk = {n.lower() for n in os.listdir(tex) if n.lower().endswith(".tlb")}
            except OSError:
                on_disk = set()
            avail = {i for i in range(32)
                     if ("%s%d.tlb" % (theatre, i + 1)).lower() in on_disk}
            used = sorted(rrf.slots_used_by(parts, available_slots=avail))
            built, _report = rrf.theatre_set_libraries(tex, parts, theatre)
        except Exception as exc:
            print("  FAIL  %-42s %s" % (os.path.basename(m), exc))
            failed += 1
            continue
        if not used:
            continue
        missing = [s for s in used if s not in built]
        if missing and not args.partial:
            print("  skip  %-42s slots %s have no %s library on disk"
                  % (os.path.basename(m), missing, theatre))
            skipped_unresolved += 1
            continue

        # List the WHOLE theatre set, not merely the slots this model happens to use.
        #
        # The game loads the theatre's libraries as a set, so any of them is available to
        # a face at runtime. Naming only the used slots makes the .RRI *narrower* than
        # reality, and because an .RRI is authoritative it then disables the importer's
        # fallback: a real Normandy M4a3 went from 0 unresolved faces to 38 that way. All
        # 38 wanted a single id (23) that Normandy2 lacks but the rest of the Normandy set
        # has. Listing the set fixes it, and gives ObjEdit everything the game would have.
        slots = {}
        for slot_idx in (range(len(THEATRE_SET_RANGE)) if args.full_set else ()):
            # Compare lowercase on BOTH sides - `theatre` is capitalised ("Normandy") and
            # real installs mix extension case (Normandy2.tlb / Normandy3.TLB).
            wanted = ("%s%d.tlb" % (theatre, slot_idx + 1)).lower()
            found = None
            try:
                for name in os.listdir(tex):
                    if name.lower() == wanted:
                        found = name
                        break
            except OSError:
                break
            if found:
                slots[slot_idx] = os.path.join("texture", found)
        # Guarantee the slots this model actually names are present even if the numbering
        # above missed them (e.g. a slot beyond the theatre's own numbered range).
        for s_i, entry in built.items():
            slots.setdefault(s_i, os.path.abspath(entry[2]) if args.absolute
                             else os.path.join("texture", os.path.basename(entry[2])))
        if not slots:
            skipped_unresolved += 1
            continue
        # VERIFY the rule's choice. The theatre rule is right for the overwhelming
        # majority (836 of 950 models land at 90-100% coverage), but it is a naming
        # convention, not a guarantee: 81 Desert models name a library holding NONE of
        # their ids - M3Gmc asks for 40 ids and Desert2 has 0 of them, so ObjEdit loads a
        # library with nothing in it and the model renders flat and untextured. An .RRI
        # that names the wrong library is no better than none at all.
        #
        # Where the rule's pick covers little, search every .TLB on disk and use whatever
        # actually covers the ids instead. For M3Gmc that finds M4.tlb at 57% - imperfect,
        # but the difference between a textured model and a green silhouette.
        need_by_slot = {}
        for _p in parts:
            for _t in (getattr(_p, "face_texture_id", None) or ()):
                if _t is None:
                    continue
                _u, _sl, _pid = rrf.decode_texture_offset(_t)
                need_by_slot.setdefault(_sl, set()).add(_pid)
        _libcache = {}

        def _ids(path):
            if path not in _libcache:
                try:
                    _libcache[path] = set(rrf.read_tlb(path))
                except Exception:
                    _libcache[path] = set()
            return _libcache[path]

        repaired = []
        unresolvable = []
        for _sl, _ids_needed in need_by_slot.items():
            chosen = slots.get(_sl)
            cov = 0.0
            if chosen:
                cpath = chosen if os.path.isabs(chosen) else os.path.join(tex, os.path.basename(chosen))
                if os.path.exists(cpath):
                    cov = len(_ids_needed & _ids(cpath)) / float(len(_ids_needed))
            if cov >= 0.5:
                continue
            best = (cov, None)
            try:
                for _n in sorted(os.listdir(tex)):
                    if not _n.lower().endswith(".tlb"):
                        continue
                    _p2 = os.path.join(tex, _n)
                    c = len(_ids_needed & _ids(_p2)) / float(len(_ids_needed))
                    if c > best[0]:
                        best = (c, _p2)
            except OSError:
                pass
            # DO NOT substitute a library merely because it shares ids.
            #
            # That is the false-friend failure this project already documented: many
            # libraries reuse the same id numbers for completely different artwork, so a
            # high overlap score is not evidence of the right library. Doing it here put
            # M4.tlb (57% overlap) on M3Gmc and painted a halftrack with cockpit
            # instrument dials - textured, plausible at a glance, and wrong.
            #
            # When the theatre rule's library genuinely does not hold the ids, the honest
            # outcome is NO .RRI: ObjEdit then says "No RRI file found" and shows the model
            # untextured, which is visibly unresolved rather than convincingly wrong.
            unresolvable.append("slot %d (%.0f%% in %s)"
                                % (_sl, cov * 100,
                                   os.path.basename(slots.get(_sl) or "-")))

        if unresolvable:
            print("  skip  %-42s no library on disk holds its ids: %s"
                  % (os.path.basename(m), "; ".join(unresolvable)))
            skipped_unresolved += 1
            continue
        desc = ", ".join("%d=%s" % (s, os.path.basename(p)) for s, p in sorted(slots.items()))
        print("  %s  %-42s %s" % ("WROTE" if args.write else " ok  ", os.path.basename(m), desc))
        if args.write:
            try:
                rrf.write_rri(rri_path, slots)
                written += 1
            except Exception as exc:
                print("        write failed: %s" % exc)
                failed += 1

    print("\n%s: %d | already had .RRI: %d | not a theatre folder: %d | unresolved: %d | failed: %d"
          % ("written" if args.write else "would write", written if args.write else
             len(models) - skipped_existing - skipped_no_theatre - skipped_unresolved - failed,
             skipped_existing, skipped_no_theatre, skipped_unresolved, failed))
    if not args.write:
        print("DRY RUN - nothing was written. Re-run with --write to apply.")


if __name__ == "__main__":
    main()
