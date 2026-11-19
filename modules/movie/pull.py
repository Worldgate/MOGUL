"""mogul.py movie pull: find movies by director, cast, crew or studio
(using the TMDB info saved by `movie tmdb`), and move them out of the
library into another folder.

Without --to it only LISTS what matches. With --to it moves each matching
movie folder there, keeping its place in the layout:

    <root>/1990-1999/Fight Club (1999) {tmdb-550}
 -> <to>/1990-1999/Fight Club (1999) {tmdb-550}

Examples:
  mogul.py movie pull --title "*making*" --genre Documentary
  mogul.py movie pull --genre Documentary --max-runtime 60 --to /media/Movies/pulled-docs
  mogul.py movie pull --director "Uwe Boll" --to /media/Movies/pulled --dry-run
  mogul.py movie pull --studio "*asylum*" --to /media/Movies/pulled

Matching:
  - Names are matched ignoring upper/lower case and accents
    ("penelope cruz" matches "Penélope Cruz").
  - * is a wildcard: --studio "*asylum*" matches "The Asylum".
  - The same option given twice = either one (--cast A --cast B: A or B).
  - Different options = all of them (--director A --studio B: both).

Never overwrites: if the spot in --to is taken, that movie is SKIPPED.
Never deletes: a move on the same drive is a rename; across drives the
folder is copied, every file's size checked, and only then the original
removed. The report has from -> to for every move, so they can be put back.
"""

import fnmatch
import os
import shutil
import sys
import time
import unicodedata

from mogul_core import store

from . import library


def fold(text):
    """Lowercase, no accents, so names compare loosely."""
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).casefold().strip()


def matches(patterns, names):
    """First name matching any pattern (wildcards allowed), or None."""
    for name in names:
        n = fold(name)
        for p in patterns:
            if fnmatch.fnmatchcase(n, fold(p)):
                return name
    return None


def check(data, args):
    """None if the movie doesn't match; otherwise a list of reasons."""
    t = data.get("tmdb", {})
    credits = t.get("credits", {})
    cast = sorted(credits.get("cast", []), key=lambda c: c.get("order", 999))
    if args.top:
        cast = cast[:args.top]
    crew = credits.get("crew", [])
    studios = [c.get("name", "") for c in t.get("production_companies", [])]
    reasons = []

    tests = [
        ("director", args.director, [c.get("name", "") for c in crew if c.get("job") == "Director"]),
        ("cast", args.cast, [c.get("name", "") for c in cast]),
        ("crew", args.crew, [c.get("name", "") for c in crew]),
        ("studio", args.studio, studios),
        ("genre", args.genre, [g.get("name", "") for g in t.get("genres", [])]),
        ("title", args.title, [x for x in (t.get("title"), t.get("original_title")) if x]),
    ]
    for label, patterns, names in tests:
        if patterns:
            hit = matches(patterns, names)
            if not hit:
                return None
            reasons.append(f"{label}: {hit}")
    if args.no_studio:
        if studios:
            return None
        reasons.append("no studio listed")
    if args.max_runtime is not None:
        runtime = t.get("runtime") or 0
        if not runtime or runtime > args.max_runtime:
            return None
        reasons.append(f"{runtime} min")
    if args.max_votes is not None:
        votes = t.get("vote_count") or 0
        if votes > args.max_votes:
            return None
        reasons.append(f"{votes} votes")
    return reasons


def tree_sizes(path):
    """{relative file path: size} for a folder (or a single file)."""
    if os.path.isfile(path):
        return {"": os.path.getsize(path)}
    out = {}
    for dirpath, _, files in os.walk(path):
        for f in files:
            full = os.path.join(dirpath, f)
            out[os.path.relpath(full, path)] = os.path.getsize(full)
    return out


def move(src, dst):
    """Move a folder or file. Same drive: rename. Other drive: copy to
    dst + '.part', check every file's size, rename into place, then remove
    the original."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        os.rename(src, dst)
        return "renamed"
    except OSError as e:
        if e.errno != 18:  # 18 = EXDEV, different drive
            raise
    tmp = dst + ".part"
    if os.path.exists(tmp):
        raise OSError(f"leftover from an earlier try is in the way: {tmp}")
    try:
        if os.path.isdir(src):
            shutil.copytree(src, tmp)
        else:
            shutil.copy2(src, tmp)
        if tree_sizes(src) != tree_sizes(tmp):
            raise OSError("copy check failed (file sizes differ)")
        os.rename(tmp, dst)
    except BaseException:
        if os.path.isdir(tmp):
            shutil.rmtree(tmp, ignore_errors=True)
        elif os.path.exists(tmp):
            os.unlink(tmp)
        raise
    if os.path.isdir(src):
        shutil.rmtree(src)
    else:
        os.unlink(src)
    return "copied"


def run(args, config):
    if not any([args.director, args.cast, args.crew, args.studio, args.genre, args.title,
                args.no_studio, args.max_votes is not None, args.max_runtime is not None]):
        sys.exit("Say what to look for: --director, --cast, --crew, --studio, --genre, "
                 "--title, --no-studio, --max-runtime or --max-votes (see --help).")
    mcfg = config.section("movie")
    store_dir = config.resolve(mcfg.get("tmdb_store", "mogul_cache/tmdb/movie"))
    to = os.path.abspath(os.path.expanduser(args.to)) if args.to else None
    folders = library.roots(config, args.path)

    mode = "list only, add --to FOLDER to move" if not to else \
        ("DRY RUN, nothing will be moved" if args.dry_run else f"moving to {to}")
    print(f"MOGUL movie pull ({mode})")
    for f in folders:
        print(f"  reading names in {f} ...")
    if to:
        for f in folders:
            if os.path.commonpath([f, to]) == f:
                sys.exit(f"--to can't be inside the library folder ({f}).")

    units = library.find_units(folders)
    print(f"  movies:    {len(units)}")
    print()

    report = {"action": "movie pull", "started": store.now(), "dry_run": args.dry_run,
              "to": to, "filters": {k: v for k, v in vars(args).items()
                                    if k in ("director", "cast", "crew", "studio", "genre", "title",
                                             "top", "no_studio", "max_runtime", "max_votes")
                                    and v not in (None, False)},
              "matched": [], "moved": [], "skipped": [], "errors": [], "no_tmdb_data": []}
    cache = {}
    for tid, root, path in units:
        if tid not in cache:
            try:
                cache[tid] = store.load(os.path.join(store_dir, f"{tid}.json"))
            except (OSError, ValueError):
                cache[tid] = None
        data = cache[tid]
        rel = os.path.relpath(path, root)
        if data is None:
            report["no_tmdb_data"].append(rel)
            continue
        reasons = check(data, args)
        if reasons is None:
            continue
        why = "; ".join(reasons)
        report["matched"].append({"tmdb_id": tid, "path": rel, "why": why})
        if not to:
            print(f"MATCH  {rel}   [{why}]")
            continue
        dst = os.path.join(to, rel)
        if os.path.exists(dst) or os.path.exists(dst + ".part"):
            print(f"SKIP   {rel}   (already something at the destination)")
            report["skipped"].append({"from": path, "to": dst, "reason": "destination taken"})
            continue
        if args.dry_run:
            print(f"WOULD MOVE {rel}   [{why}]")
            continue
        try:
            how = move(path, dst)
        except OSError as e:
            print(f"ERROR  {rel}   {e}")
            report["errors"].append({"from": path, "to": dst, "error": str(e)})
            continue
        print(f"MOVED  {rel}   [{why}]" + ("  (copied to the other drive)" if how == "copied" else ""))
        report["moved"].append({"from": path, "to": dst, "tmdb_id": tid, "why": why})

    report["finished"] = store.now()
    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    rpath = os.path.join(config.reports, f"movie-pull_{stamp}{'_dry-run' if args.dry_run else ''}.json")
    store.save(rpath, report)

    print()
    print(f"Matched: {len(report['matched'])}"
          + (f"   moved: {len(report['moved'])}   skipped: {len(report['skipped'])}   "
             f"errors: {len(report['errors'])}" if to and not args.dry_run else ""))
    if report["no_tmdb_data"]:
        print(f"{len(report['no_tmdb_data'])} movie(s) have no saved TMDB info yet and "
              "weren't checked (run: mogul.py movie tmdb).")
    print(f"Report: {rpath}")
    return 1 if report["errors"] else 0
