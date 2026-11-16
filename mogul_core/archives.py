"""Archives inside downloads (rar, 7z, zip), used by `<media> validate`.

Knows which files belong together:
  NAME.part1.rar, NAME.part2.rar ...   (also part01 / part001)
  NAME.rar + NAME.r00, NAME.r01 ...    (old style; .s00 too)
  NAME.7z.001, NAME.7z.002 ...
  NAME.rar / NAME.7z / NAME.zip        (single file)

Extracts with whatever is installed:
  rar: unrar, else 7-Zip (7zz / 7z / 7za)
  7z:  7-Zip
  zip: Python's own zipfile (nothing to install)
Every format checks its own CRCs while extracting, so a clean extract is
also a second check of the data.
"""

import re
import shutil
import subprocess
import zipfile
from pathlib import Path

RAR_PART = re.compile(r"^(.*)\.part(\d+)\.rar$", re.IGNORECASE)
RAR_OLD_VOL = re.compile(r"^(.*)\.[rs]\d\d$", re.IGNORECASE)
SEVENZ_VOL = re.compile(r"^(.*)\.7z\.(\d{3})$", re.IGNORECASE)


def group_volumes(names):
    """Group archive file names into sets. Returns [(first, [all volumes])],
    first = the file to hand to the extractor. Non-archive names are ignored."""
    names = list(names)
    groups = {}          # key -> {"first": name, "vols": [...], "rank": n}

    def add(key, name, rank):
        g = groups.setdefault(key, {"first": None, "rank": None, "vols": []})
        g["vols"].append(name)
        if g["rank"] is None or rank < g["rank"]:
            g["first"], g["rank"] = name, rank

    for n in names:
        low = n.lower()
        m = RAR_PART.match(n)
        if m:
            add(("rarpart", m.group(1).lower()), n, int(m.group(2)))
            continue
        m = SEVENZ_VOL.match(n)
        if m:
            add(("7zvol", m.group(1).lower()), n, int(m.group(2)))
            continue
        m = RAR_OLD_VOL.match(n)
        if m:
            add(("rarold", m.group(1).lower()), n, 1)   # .r00 etc: never first
            continue
        if low.endswith(".rar"):
            add(("rarold", n[:-4].lower()), n, 0)        # NAME.rar is first
        elif low.endswith(".7z") or low.endswith(".zip"):
            add(("single", low), n, 0)
    # an old-style group with only .r00 files and no .rar can't be opened
    return [(g["first"], sorted(g["vols"])) for g in groups.values()
            if not (g["first"] and RAR_OLD_VOL.match(g["first"]))]


def is_first_volume(name):
    return any(first == name for first, _ in group_volumes([name]))


def find_tool(name):
    """Program to extract this archive with, or None."""
    low = name.lower()
    if low.endswith(".zip"):
        return "python-zipfile"
    sevenzip = (shutil.which("7zz") or shutil.which("7z") or shutil.which("7za"))
    if low.endswith(".rar"):
        return shutil.which("unrar") or sevenzip
    return sevenzip


def extract(archive, dest):
    """Extract archive into dest (made fresh). Returns None if every file came
    out and checked fine, otherwise a short reason. On failure dest is removed
    (the archive itself is never touched)."""
    archive, dest = Path(archive), Path(dest)
    tool = find_tool(archive.name)
    if not tool:
        return ("nothing to extract it with: sudo apt install unrar "
                "(or: sudo apt install 7zip)")
    dest.mkdir(parents=True, exist_ok=False)
    try:
        if tool == "python-zipfile":
            with zipfile.ZipFile(archive) as z:
                bad = z.testzip()
                if bad:
                    raise RuntimeError(f"zip CRC error in {bad}")
                z.extractall(dest)
            return None
        if Path(tool).name == "unrar":
            cmd = [tool, "x", "-o-", "-p-", "-y", "-idq", str(archive), str(dest) + "/"]
        else:
            cmd = [tool, "x", "-y", "-p-", f"-o{dest}", str(archive)]
        proc = subprocess.run(cmd, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if proc.returncode != 0:
            text = proc.stdout.decode("utf-8", "replace")
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            raise RuntimeError(f"{Path(tool).name} exit {proc.returncode}: "
                               + " | ".join(lines[-3:]))
        return None
    except Exception as e:          # noqa: BLE001 - any failure = not extracted
        shutil.rmtree(dest, ignore_errors=True)
        return str(e) or e.__class__.__name__
