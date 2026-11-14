#!/usr/bin/env python3
"""
par2sort.py - Read .par2 sets, find the files they reference anywhere under a
search folder (recursively), and move them into a folder named after the set.

    Movie.Name.par2 / Movie.Name.vol000+01.par2 ...  ->  folder "Movie.Name"

Usage:
    python3 par2sort.py PAR2_PATH SEARCH_FOLDER [options]

    PAR2_PATH       a .par2 file, or a folder containing .par2 files
                    (all sets found in it are processed, sub folders included)
    SEARCH_FOLDER   where to look for the data files (sub folders included)

Options:
    --dest DIR      where to create the set folders
                    (default: the folder the .par2 file is in)
    --leave-par2    don't move the .par2 / .volXXX.par2 files (they move by default)
    --dry-run       show what would happen, move nothing
    --no-check      match on filename only (skip the size / first-16KB MD5 check)
    --exact-only    only move complete, matching files (leave partials behind)

Matching: an exact match has the same name, size and first-16KB MD5 (fast;
doesn't hash the whole file). If no exact match exists, a same-named partial or
damaged file is moved instead (flagged PARTIAL) so par2 can repair it. When
there are several partials, the one whose first 16KB is intact wins, then the
biggest. If par2 stored sub-paths (e.g. "CD1/file.bin"), that structure is
kept inside the set folder.
"""
import argparse
import hashlib
import os
import re
import shutil
import struct
import sys
from collections import defaultdict

MAGIC = b"PAR2\x00PKT"
T_FDESC = b"PAR 2.0\x00FileDesc"
T_UNI = b"PAR 2.0\x00UniFileN"
VOL_RE = re.compile(r"\.vol\d+[+-]\d+\.par2$", re.IGNORECASE)


# ---------------------------------------------------------------- par2 parse
def parse_par2(path):
    """Return {file_id: {name, size, md5_16k}} from one .par2 file."""
    files, uni = {}, {}
    with open(path, "rb") as f:
        data = f.read()
    pos = 0
    while True:
        pos = data.find(MAGIC, pos)
        if pos < 0 or pos + 64 > len(data):
            break
        (length,) = struct.unpack_from("<Q", data, pos + 8)
        if length < 64 or pos + length > len(data):
            pos += 8
            continue
        ptype = data[pos + 48:pos + 64]
        body = data[pos + 64:pos + length]
        if ptype == T_FDESC and len(body) >= 56:
            (size,) = struct.unpack_from("<Q", body, 48)
            files[body[0:16]] = {
                "name": body[56:].rstrip(b"\x00").decode("utf-8", errors="replace"),
                "size": size,
                "md5_16k": body[32:48],
            }
        elif ptype == T_UNI and len(body) > 16:
            raw = body[16:]
            if len(raw) % 2:
                raw = raw[:-1]
            uni[body[0:16]] = raw.decode("utf-16-le", errors="replace").rstrip("\x00")
        pos += length
    for fid, name in uni.items():
        if fid in files:
            files[fid]["name"] = name
    return files


def set_name(par2_path):
    base = os.path.basename(par2_path)
    base = VOL_RE.sub("", base)
    if base.lower().endswith(".par2"):
        base = base[:-5]
    return base


def find_par2_sets(path):
    """Group .par2 files into sets: {(dir, setname): [par2 paths]}."""
    sets = defaultdict(list)
    if os.path.isfile(path):
        d = os.path.dirname(os.path.abspath(path))
        name = set_name(path)
        # pull in the rest of the set sitting next to it
        for f in os.listdir(d):
            if f.lower().endswith(".par2") and set_name(f) == name:
                sets[(d, name)].append(os.path.join(d, f))
    else:
        for root, _, fnames in os.walk(path):
            for f in fnames:
                if f.lower().endswith(".par2"):
                    sets[(os.path.abspath(root), set_name(f))].append(os.path.join(root, f))
    return sets


# ---------------------------------------------------------------- matching
def md5_16k(path):
    with open(path, "rb") as f:
        return hashlib.md5(f.read(16384)).digest()


def index_folder(folder):
    """{lowercase filename: [full paths]} for everything under folder."""
    idx = defaultdict(list)
    for root, _, fnames in os.walk(folder):
        for f in fnames:
            idx[f.lower()].append(os.path.join(root, f))
    return idx


def pick_match(entry, candidates, check, used):
    """Return (path, kind). kind = 'exact' or 'partial'.

    exact   = same name, same size, same first-16KB MD5
    partial = same name but size/hash differ (damaged or incomplete) - still
              worth moving so par2 can repair it. Best partial is the one whose
              first 16KB still matches, then the biggest.
    """
    best, best_key = None, None
    for c in candidates:
        if c in used:
            continue
        if not check:
            return c, "exact"
        try:
            size = os.path.getsize(c)
            head_ok = md5_16k(c) == entry["md5_16k"]
        except OSError:
            continue
        if size == entry["size"] and head_ok:
            return c, "exact"
        key = (head_ok, min(size, entry["size"]))
        if best_key is None or key > best_key:
            best, best_key = c, key
    if best:
        return best, "partial"
    return None, None


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Sort files into folders by .par2 set.")
    ap.add_argument("par2_path")
    ap.add_argument("search_folder")
    ap.add_argument("--dest")
    ap.add_argument("--leave-par2", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-check", action="store_true")
    ap.add_argument("--exact-only", action="store_true")
    a = ap.parse_args()

    if not os.path.isdir(a.search_folder):
        sys.exit(f"Search folder not found: {a.search_folder}")

    sets = find_par2_sets(a.par2_path)
    if not sets:
        sys.exit("No .par2 files found.")

    print(f"Indexing {a.search_folder} ...")
    idx = index_folder(a.search_folder)
    tag = "[DRY RUN] " if a.dry_run else ""
    used = set()
    tot_moved = tot_missing = tot_partial = 0

    for (pdir, name), par2s in sorted(sets.items()):
        entries = {}
        for p in sorted(par2s, key=os.path.getsize):  # smallest (index) first
            try:
                entries.update(parse_par2(p))
            except OSError as e:
                print(f"  ! can't read {p}: {e}")
            if entries:
                break
        if not entries:  # index file had nothing, try the rest
            for p in par2s:
                try:
                    entries.update(parse_par2(p))
                except OSError:
                    pass

        target = os.path.join(a.dest or pdir, name)
        print(f"\n== {name}  ({len(entries)} file(s)) -> {target}")
        if not entries:
            print("  ! no file list found in this set, skipped")
            continue

        moves = []
        for e in entries.values():
            rel = e["name"].replace("\\", "/")
            base = os.path.basename(rel)
            hit, kind = pick_match(e, idx.get(base.lower(), []), not a.no_check, used)
            if hit and kind == "partial" and a.exact_only:
                print(f"  - PARTIAL left behind (--exact-only)  {hit}")
                hit = None
            if hit:
                used.add(hit)
                if kind == "partial":
                    print(f"  ~ PARTIAL/DAMAGED (needs repair)  {hit}")
                    tot_partial += 1
                moves.append((hit, os.path.join(target, *rel.split("/"))))
            else:
                print(f"  - MISSING  {rel}")
                tot_missing += 1

        if not a.leave_par2:
            for p in par2s:
                moves.append((p, os.path.join(target, os.path.basename(p))))

        for src, dst in moves:
            if os.path.abspath(src) == os.path.abspath(dst):
                print(f"  = already there  {os.path.basename(dst)}")
                continue
            if os.path.exists(dst):
                print(f"  ! exists, skipped  {dst}")
                continue
            print(f"  {tag}move  {src}")
            if not a.dry_run:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.move(src, dst)
            tot_moved += 1

    print(f"\n{tag}Done. {tot_moved} moved ({tot_partial} partial/damaged), {tot_missing} missing.")


if __name__ == "__main__":
    main()

