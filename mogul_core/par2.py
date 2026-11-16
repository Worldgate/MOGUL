"""par2 checking and repair, shared by `<media> validate` and tools/par2check.py.

Runs the par2 program (par2cmdline: sudo apt install par2) on each set and
reads its output. Results per set:
  OK          every file correct
  FIXED       was damaged/missing, repaired (par2 keeps the damaged copy as NAME.1)
  REPAIRABLE  damaged but fixable (only when checking without repairing)
  CAN'T FIX   not enough recovery data
  COVERED     this set alone couldn't fix it, but another set in the same
              folder checked the same files and they're good (e.g. a
              duplicate download "NAME (2).par2" with no .vol files of its own)
  ERROR       par2 itself failed
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

VOL_RE = re.compile(r"\.vol\d+[+-]\d+\.par2$", re.IGNORECASE)
TARGET_RE = re.compile(
    r'Target: "(.+?)" - (found|damaged|missing|is a match for "(.+?)")'
    r"(?:\. Found (\d+) of (\d+) data blocks)?")
MATCH_RE = re.compile(r'File: "(.+?)" - is a match for "(.+?)"')
NEED_RE = re.compile(r"You need (\d+) more recovery blocks")

ORDER = ["CAN'T FIX", "ERROR", "EXTRACT FAILED", "REPAIRABLE", "FIXED", "OK", "COVERED"]
GOOD = ("OK", "FIXED", "COVERED")


def find_program(name="par2"):
    return shutil.which(name)


def find_sets(top):
    """Every main .par2 under top (the .volNN+NN.par2 files are skipped:
    par2 loads those by itself)."""
    top = Path(top)
    sets = [p for p in top.rglob("*")
            if p.is_file() and p.suffix.lower() == ".par2"
            and not VOL_RE.search(p.name)]
    return sorted(sets, key=lambda p: str(p).lower())


def run_set(par2, set_file, verify_only=False, extras=False):
    """Verify (or repair) one set. Returns {status, files, detail}.
    files = {name: state before any repair}; state "found" means good."""
    set_file = Path(set_file)
    cmd = [par2, "v" if verify_only else "r", set_file.name]
    if extras:
        cmd += ["--"] + sorted(
            p.name for p in set_file.parent.iterdir()
            if p.is_file() and p.suffix.lower() != ".par2")
    try:
        proc = subprocess.run(cmd, cwd=set_file.parent,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except OSError as e:
        return {"status": "ERROR", "files": {}, "detail": str(e), "matched": []}
    text = proc.stdout.decode("utf-8", "replace")
    lines = [l.strip() for l in re.split(r"[\r\n]+", text) if l.strip()]

    files = {}          # first report per file = how it was BEFORE any repair
    for line in lines:
        m = TARGET_RE.search(line)
        if not m or m.group(1) in files:
            continue
        name, what = m.group(1), m.group(2)
        if what == "damaged":
            files[name] = f"damaged ({m.group(4)} of {m.group(5)} blocks good)"
        elif what.startswith("is a match"):
            files[name] = f"found under another name: {m.group(3)}"
        else:
            files[name] = what
    matched = []
    for line in lines:   # extra files that turned out to be a renamed copy
        m = MATCH_RE.search(line)
        if m:
            matched.append(m.group(1))
        if m and files.get(m.group(2)) == "missing":
            files[m.group(2)] = (f'missing, found as "{m.group(1)}"'
                                 + ("" if verify_only else " (renamed back)"))

    bad = any(v != "found" for v in files.values())
    need = next((m.group(1) for l in lines for m in [NEED_RE.search(l)] if m), None)
    code = proc.returncode
    detail = ""
    if code == 0:
        status = "FIXED" if bad else "OK"
    elif code == 1:
        status = "REPAIRABLE"
    elif code == 2:
        status = "CAN'T FIX"
        if need:
            detail = f"needs {need} more recovery blocks"
    else:
        status = "ERROR"
        detail = f"par2 exit code {code}: " + " | ".join(lines[-3:])
    return {"status": status, "files": files, "detail": detail, "matched": matched}


def mark_covered(results):
    """A set that couldn't fix its files on its own, when another set in the
    same folder checked the very same files and found (or made) them good,
    becomes COVERED. results: list of dicts with "path" (Path) + run_set()."""
    good = {}
    for r in results:
        if r["status"] in ("OK", "FIXED"):
            good.setdefault(r["path"].parent, set()).update(r["files"])
    for r in results:
        if (r["status"] in ("CAN'T FIX", "REPAIRABLE") and r["files"]
                and set(r["files"]) <= good.get(r["path"].parent, set())):
            r["status"] = "COVERED"
            r["detail"] = "another set in this folder checked these files: good"


def recheck(par2, set_file):
    """Verify a set again (after a repair). True if all files are correct."""
    set_file = Path(set_file)
    proc = subprocess.run([par2, "v", set_file.name], cwd=set_file.parent,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc.returncode == 0


def set_files(set_file):
    """The set's own par2 files: NAME.par2 + NAME.volNN+NN.par2."""
    set_file = Path(set_file)
    base = set_file.name[:-len(".par2")]
    vol = re.compile(re.escape(base) + r"\.vol\d+[+-]\d+\.par2$", re.IGNORECASE)
    return [p for p in set_file.parent.iterdir() if p.is_file() and (
        p.name.lower() == set_file.name.lower() or vol.match(p.name))]


def backups(set_file, files):
    """NAME.1, NAME.2 ... copies par2 kept of files it repaired."""
    folder = Path(set_file).parent
    out = []
    for name, state in files.items():
        if state != "found":
            for n in range(1, 10):
                b = folder / f"{name}.{n}"
                if b.is_file():
                    out.append(b)
    return out


def remove_set(set_file, files):
    """Delete a finished set's par2 files and par2's NAME.1 backups.
    Returns how many files were removed. Never touches anything else."""
    doomed = set_files(set_file) + backups(set_file, files)
    for p in doomed:
        if p.exists():
            p.unlink()
    return len(doomed)


def move_file(src, dest):
    """Move src to dest without overwriting. Same drive: a rename. Across
    drives: copy to dest.part, check the size, rename into place, then
    delete the source. Returns None on success, or a reason it didn't move."""
    src, dest = Path(src), Path(dest)
    if dest.exists():
        return "name already taken in the destination"
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(src, dest)
        return None
    except OSError:
        pass
    part = dest.with_name(dest.name + ".part")
    try:
        shutil.copy2(src, part)
        if part.stat().st_size != src.stat().st_size:
            part.unlink()
            return "copy came out a different size"
        if dest.exists():
            part.unlink()
            return "name already taken in the destination"
        os.rename(part, dest)
        src.unlink()
        return None
    except OSError as e:
        if part.exists():
            part.unlink()
        return f"move failed: {e}"


def remove_empty_folders(top):
    """Remove empty subfolders under top (top itself stays)."""
    top = Path(top)
    for d in sorted((p for p in top.rglob("*") if p.is_dir()),
                    key=lambda p: len(p.parts), reverse=True):
        try:
            d.rmdir()
        except OSError:
            pass
