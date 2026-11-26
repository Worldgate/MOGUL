"""mogul.py radarr watch / radarr update: follow actors, directors and
studios, and add their new movies to Radarr as they come out.

The list lives in [movie] radarr_watchlist (default
mogul_userdata/radarr/watchlist.toml). Edit it by hand or with:

  mogul.py radarr watch add --cast "Keanu Reeves"
  mogul.py radarr watch add --director "John Carpenter" --monitor
  mogul.py radarr watch add --studio "A24"
  mogul.py radarr watch list
  mogul.py radarr watch remove "Keanu Reeves"

Then, monthly (cron / systemd timer):

  mogul.py radarr update

which backs Radarr up first (radarr_backup_before_update), then runs
`radarr add` for every entry: same junk filter, same skips (already in
Radarr, on the exclusion list, already on disk). Movies that aren't out
yet are skipped until they are, so the run after a movie's release picks
it up. Each entry gets its own report, plus a summary:
mogul_reports/radarr-update_<date>_<time>.txt
"""

import argparse
import datetime
import json
import os
import time
import tomllib

from mogul_core import store, tmdb
from modules.movie.pull import fold

from . import add, backup

KINDS = ["cast", "director", "crew", "studio"]
OPTIONAL = ["top", "monitor", "root", "profile", "note"]

HEADER = """\
# MOGUL radarr watch list: who to check for new movies.
# `mogul.py radarr update` goes through every entry and adds their new
# movies to Radarr (junk filtered, see [movie] radarr_* in mogul.toml).
#
# Edit by hand or with `mogul.py radarr watch add / remove`.
# Sections: [[cast]] [[director]] [[crew]] [[studio]]. Per entry:
#   name          as on TMDB ("Keanu Reeves"); studios may use * ("Warner Bros*")
#   tmdb_id       filled in automatically the first time (people only)
#   top           actors: skip movies where they're billed lower than #N
#   monitor       true = add their movies MONITORED (Radarr downloads them)
#   root          Radarr root folder for this one's movies
#   profile       Radarr quality profile for this one's movies
#   note          your own note
#   added, last_checked, last_added   kept up to date by MOGUL
# Comments you add here are NOT kept when MOGUL rewrites the file;
# use note = "..." instead.
"""


def path_of(config):
    return config.resolve(config.section("movie").get("radarr_watchlist",
                                                      "mogul_userdata/radarr/watchlist.toml"))


def load(path):
    if not os.path.isfile(path):
        return {k: [] for k in KINDS}
    with open(path, "rb") as f:
        try:
            data = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise SystemExit(f"Can't read {path}: {e}\nFix the file by hand (it's plain text).")
    return {k: list(data.get(k, [])) for k in KINDS}


def value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return json.dumps(str(v), ensure_ascii=False)


def save(path, data):
    lines = [HEADER]
    order = ["name", "tmdb_id"] + OPTIONAL + ["added", "last_checked", "last_added"]
    for kind in KINDS:
        for e in data.get(kind, []):
            lines.append(f"[[{kind}]]")
            keys = [k for k in order if k in e] + sorted(k for k in e if k not in order)
            for k in keys:
                if e[k] is not None:
                    lines.append(f"{k} = {value(e[k])}")
            lines.append("")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    os.replace(tmp, path)


def label(kind, e):
    return f"{kind}: {e.get('name')}" + (f" ({e['tmdb_id']})" if e.get("tmdb_id") else "")


def same(e, text):
    t = str(text).strip()
    return fold(e.get("name")) == fold(t) or (t.isdigit() and str(e.get("tmdb_id")) == t)


# ------------------------------------------------------------------ watch

def run(action, args, config):
    if action == "update":
        return update(args, config)
    path = path_of(config)
    data = load(path)
    if args.op == "list":
        return show_list(data, path)
    if args.op == "remove":
        return remove(args, data, path)
    return add_entry(args, config, data, path)


def show_list(data, path):
    total = sum(len(v) for v in data.values())
    print(f"Watch list: {path}  ({total} entries)")
    if not total:
        print('  (empty; add someone with: mogul.py radarr watch add --cast "Name")')
    for kind in KINDS:
        for e in data[kind]:
            extra = [f"{k}={e[k]}" for k in OPTIONAL if e.get(k) not in (None, "", False)]
            checked = e.get("last_checked") or "never"
            print((f"  {label(kind, e):<45} checked {checked:<10}"
                  + (f"  last added {e['last_added']}" if e.get("last_added") is not None else "")
                   + (f"  [{', '.join(extra)}]" if extra else "")).rstrip())
    return 0


def remove(args, data, path):
    only_kind = next((k for k in KINDS if getattr(args, k)), None)
    wanted = args.name or (getattr(args, only_kind) if only_kind else None)
    if not wanted:
        raise SystemExit('Say who to remove: mogul.py radarr watch remove "Name"')
    hits = [(k, e) for k in KINDS if not only_kind or k == only_kind
            for e in data[k] if same(e, wanted)]
    if not hits:
        raise SystemExit(f"'{wanted}' isn't on the watch list (see: mogul.py radarr watch list)")
    if len(hits) > 1:
        names = "\n  ".join(label(k, e) for k, e in hits)
        raise SystemExit(f"'{wanted}' is on the list more than once; say which, e.g. "
                         f"--cast or --director:\n  {names}")
    kind, e = hits[0]
    data[kind].remove(e)
    save(path, data)
    print(f"Removed {label(kind, e)}")
    return 0


def add_entry(args, config, data, path):
    given = [(k, getattr(args, k)) for k in KINDS if getattr(args, k)]
    if len(given) != 1:
        raise SystemExit('Add one at a time, e.g.: mogul.py radarr watch add --cast "Keanu Reeves"')
    kind, name = given[0]
    mcfg = config.section("movie")
    client = tmdb.Client(config, per_second=float(mcfg.get("tmdb_requests_per_second", 20)))
    try:
        if kind == "studio":
            found = add.resolve_studios(client, [name])
            entry = {"name": name}
            print(f"{name}: {len(found)} studio(s) on TMDB: "
                  + ", ".join(sorted(found.values()))[:500])
        else:
            pid, pname = add.resolve_person(client, name)
            entry = {"name": pname, "tmdb_id": pid}
    except tmdb.BadKey as e:
        raise SystemExit(f"TMDB refused the API key: {e}. Check secrets.toml [tmdb].")
    for e in data[kind]:
        if same(e, entry["name"]) or (entry.get("tmdb_id") and e.get("tmdb_id") == entry["tmdb_id"]):
            raise SystemExit(f"Already on the list: {label(kind, e)}")
    for k in OPTIONAL:
        v = getattr(args, k, None)
        if v not in (None, False, ""):
            entry[k] = v
    entry["added"] = datetime.date.today().isoformat()
    data[kind].append(entry)
    save(path, data)
    print(f"Added {label(kind, entry)} to {path}")
    print("Their movies get added on the next: mogul.py radarr update")
    return 0


# ------------------------------------------------------------------ update

def update(args, config):
    mcfg = config.section("movie")
    path = path_of(config)
    data = load(path)
    entries = [(k, e) for k in KINDS for e in data[k]]
    if args.only:
        entries = [(k, e) for k, e in entries
                   if fold(args.only) in fold(e.get("name")) or str(e.get("tmdb_id")) == args.only]
        if not entries:
            raise SystemExit(f"Nothing on the watch list matches '{args.only}'.")
    every = int(mcfg.get("radarr_update_every_days", 25))
    waiting = []
    if every > 0 and not args.all and not args.only:
        due_before = datetime.date.today() - datetime.timedelta(days=every)
        due = []
        for k, e in entries:
            try:
                last = datetime.date.fromisoformat(str(e.get("last_checked") or ""))
            except ValueError:
                last = None
            (due if last is None or last <= due_before else waiting).append((k, e))
        entries = due
    if waiting and not entries:
        print(f"Nobody on the watch list is due: all {len(waiting)} were checked in the last "
              f"{every} days (radarr_update_every_days). --all checks everyone anyway.")
        return 0
    if not entries:
        print(f"The watch list is empty ({path}).")
        print('Add someone: mogul.py radarr watch add --cast "Keanu Reeves"')
        return 0
    print(f"MOGUL radarr update: {len(entries)} to check"
          + (f", {len(waiting)} checked in the last {every} days left for later (--all = everyone)"
             if waiting else "")
          + ("  (DRY RUN: nothing is added or saved)" if args.dry_run else ""))

    ctx = {}
    if not args.dry_run and not args.no_backup and mcfg.get("radarr_backup_before_update", True):
        print("\n=== backing Radarr up first ===")
        try:
            no_zip = not (args.zip or mcfg.get("radarr_update_zip", False))
            backup.make(config, ctx=ctx, no_zip=no_zip)
        except SystemExit as e:
            raise SystemExit(f"Backup failed, nothing was added: {e}\n"
                             "(run with --no-backup to skip it)")

    summary, failed = [], 0
    today = datetime.date.today().isoformat()
    for kind, e in entries:
        print(f"\n=== {label(kind, e)} ===")
        ns = argparse.Namespace(**add.DEFAULTS)
        ns.show, ns.dry_run, ns.refresh, ns.jobs = args.show, args.dry_run, args.refresh, args.jobs
        setattr(ns, kind, [str(e["tmdb_id"]) if e.get("tmdb_id") else e["name"]])
        for k in ("top", "root", "profile"):
            if e.get(k) not in (None, ""):
                setattr(ns, k, e[k])
        ns.monitor = bool(e.get("monitor", False))
        ctx.pop("last", None)
        try:
            code = add.run(ns, config, ctx)
        except SystemExit as err:
            print(f"  FAILED: {err}")
            summary.append((label(kind, e), "FAILED", str(err)))
            failed += 1
            continue
        last = ctx.get("last", {})
        counts = last.get("counts", {})
        added = counts.get("ADDED", 0) + counts.get("WOULD ADD", 0)
        summary.append((label(kind, e), "error" if code else "ok",
                        ", ".join(f"{k.lower()} {v}" for k, v in sorted(counts.items())),
                        last.get("report", "")))
        failed += 1 if code else 0
        if not args.dry_run:
            e["last_checked"] = today
            e["last_added"] = counts.get("ADDED", 0)
            if kind != "studio" and not e.get("tmdb_id"):
                ids = list(last.get("people", {}).get(kind, {}))
                if len(ids) == 1:
                    e["tmdb_id"] = ids[0]
            save(path, data)
        print(f"  -> {'would add' if args.dry_run else 'added'} {added}")

    # summary
    os.makedirs(config.reports, exist_ok=True)
    rpath = os.path.join(config.reports, f"radarr-update_{time.strftime('%Y-%m-%d_%H%M%S')}"
                                         f"{'_dryrun' if args.dry_run else ''}.txt")
    lines = [f"MOGUL radarr update {store.now()}" + (" (dry run)" if args.dry_run else ""), ""]
    for row in summary:
        lines.append(f"{row[0]:<45} {row[1]:<7} {row[2]}")
        if len(row) > 3 and row[3]:
            lines.append(f"{'':<45} report: {row[3]}")
    with open(rpath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n=== summary ===")
    for row in summary:
        print(f"  {row[0]:<45} {row[1]:<7} {row[2]}")
    print(f"\nSummary: {rpath}")
    return 1 if failed else 0
