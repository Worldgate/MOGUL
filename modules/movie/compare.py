"""mogul.py movie compare: your library vs. a streaming service's movie list.

Uses TMDB's "where to watch" data (supplied to TMDB by JustWatch):
  1. finds the service by name in TMDB's list for your country
     (--list-services shows them all),
  2. downloads every movie that service currently offers there
     (TMDB "discover", 20 per page), and keeps a dated copy in
     mogul_cache/tmdb/providers/<country>/<service>_<date>.json,
  3. matches by TMDB ID against the {tmdb-ID} names in your movie folders.

Reports: how many they have, how many you have, how many you share, and
two lists: theirs that you have, theirs that you don't.
  mogul_reports/movie-compare_<service>_<date>_<time>.txt and .csv

Streaming lists change every month: each run is a snapshot of that day.
Read-only on your movie folders.
"""

import concurrent.futures
import csv
import datetime
import fnmatch
import os
import re
import sys
import time

from mogul_core import store, tmdb

from . import library
from .pull import fold

TMDB_RESULT_CAP = 10000  # discover never returns more than 500 pages x 20


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", fold(name)).strip("-") or "service"


def providers(client, region):
    data = client.get("watch/providers/movie", {"watch_region": region})
    return sorted(data.get("results", []), key=lambda p: p.get("display_priority", 999))


def find_provider(plist, wanted):
    """Exact name (ignoring case/accents) first, then wildcard / contains."""
    w = fold(wanted)
    for p in plist:
        if fold(p.get("provider_name", "")) == w:
            return [p]
    pattern = w if "*" in w else f"*{w}*"
    return [p for p in plist if fnmatch.fnmatchcase(fold(p.get("provider_name", "")), pattern)]


def discover_all(client, base_params, jobs):
    """Every movie for these discover settings. If TMDB's 10,000-result cap
    would cut the list short, it's fetched one release year at a time."""
    first = client.get("discover/movie", dict(base_params, page=1))
    total = first.get("total_results", 0)
    if total <= TMDB_RESULT_CAP:
        return fetch_pages(client, base_params, first, jobs), total, []
    print(f"  {total} movies: more than TMDB lists in one go, fetching year by year ...")
    movies, notes = {}, []
    for year in range(1874, datetime.date.today().year + 3):
        p = dict(base_params, primary_release_year=year)
        f = client.get("discover/movie", dict(p, page=1))
        if not f.get("total_results"):
            continue
        if f["total_results"] > TMDB_RESULT_CAP:
            notes.append(f"{year}: {f['total_results']} movies, only the first {TMDB_RESULT_CAP} listed")
        for m in fetch_pages(client, p, f, jobs):
            movies[m["id"]] = m
    return list(movies.values()), total, notes


def fetch_pages(client, params, first, jobs):
    pages = min(first.get("total_pages", 1), 500)
    results = list(first.get("results", []))
    if pages > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
            for data in pool.map(lambda n: client.get("discover/movie", dict(params, page=n)),
                                 range(2, pages + 1)):
                results.extend(data.get("results", []))
    seen, out = set(), []
    for m in results:
        if m.get("id") not in seen:
            seen.add(m.get("id"))
            out.append(m)
    return out


def title_of(m):
    year = (m.get("release_date") or "")[:4]
    return f"{m.get('title', '?')}" + (f" ({year})" if year else "")


def run(args, config):
    mcfg = config.section("movie")
    region = (args.region or mcfg.get("watch_region", "US")).upper()
    client = tmdb.Client(config, per_second=float(mcfg.get("tmdb_requests_per_second", 20)))

    plist = providers(client, region)
    if args.list_services or not args.service:
        print(f"Streaming services TMDB knows for {region} ({len(plist)}):")
        for p in plist:
            print(f"  {p.get('provider_id'):>5}  {p.get('provider_name')}")
        if not args.service:
            print("\nPick one with --service NAME, e.g. --service netflix")
        return 0

    matches = find_provider(plist, args.service)
    if not matches:
        sys.exit(f"No service matching '{args.service}' in {region}. See --list-services.")
    if len(matches) > 1:
        names = "\n  ".join(f"{p['provider_id']:>5}  {p['provider_name']}" for p in matches)
        sys.exit(f"'{args.service}' matches more than one service; be more exact:\n  {names}")
    prov = matches[0]
    pname, pid = prov["provider_name"], prov["provider_id"]

    print(f"MOGUL movie compare: {pname} ({region}, {args.kind}) vs. your library")
    params = {"with_watch_providers": pid, "watch_region": region,
              "with_watch_monetization_types": args.kind, "include_adult": "false",
              "sort_by": "popularity.desc"}
    theirs, total, notes = discover_all(client, params, args.jobs)
    print(f"  {pname}: {len(theirs)} movies")

    today = datetime.date.today().isoformat()
    snap_dir = config.resolve(mcfg.get("providers_store", "mogul_cache/tmdb/providers"))
    snap = os.path.join(snap_dir, region, f"{slug(pname)}_{args.kind}_{today}.json")
    store.save(snap, {"mogul": {"schema": "mogul.tmdb.provider_list", "provider": pname,
                                "provider_id": pid, "region": region, "kind": args.kind,
                                "fetched": store.now(), "count": len(theirs), "notes": notes},
                      "movies": [{"id": m["id"], "title": m.get("title"),
                                  "release_date": m.get("release_date"),
                                  "vote_count": m.get("vote_count"),
                                  "popularity": m.get("popularity")} for m in theirs]})

    folders = library.roots(config, args.path)
    for f in folders:
        print(f"  reading names in {f} ...")
    mine = {tid for tid, _, _ in library.find_units(folders)}
    print(f"  you: {len(mine)} movies")

    have = [m for m in theirs if m["id"] in mine]
    lack = [m for m in theirs if m["id"] not in mine]
    pct = 100 * len(have) / len(theirs) if theirs else 0

    print()
    w = len("Only on " + pname) + 1
    print(f"  {pname:<{w}} {len(theirs):>7} movies")
    print(f"  {'You':<{w}} {len(mine):>7} movies")
    print(f"  {'Both':<{w}} {len(have):>7}  ({pct:.0f}% of {pname}'s list)")
    print(f"  {'Only on ' + pname:<{w}} {len(lack):>7}")
    print(f"  {'Only yours':<{w}} {len(mine) - len(have):>7}")
    for n in notes:
        print(f"  note: {n}")
    if args.show:
        print(f"\nMost popular on {pname} that you don't have:")
        for m in lack[:args.show]:
            print(f"  {title_of(m)}  {{tmdb-{m['id']}}}")

    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    base = os.path.join(config.reports, f"movie-compare_{slug(pname)}_{stamp}")
    os.makedirs(config.reports, exist_ok=True)
    with open(base + ".csv", "w", newline="", encoding="utf-8") as cf:
        w = csv.writer(cf)
        w.writerow(["you_have_it", "tmdb_id", "title", "year", "tmdb_votes"])
        for m in theirs:
            w.writerow(["yes" if m["id"] in mine else "no", m["id"], m.get("title"),
                        (m.get("release_date") or "")[:4], m.get("vote_count")])
    lines = [f"MOGUL movie compare  {store.now()}",
             f"{pname} ({region}, {args.kind}): {len(theirs)} movies   you: {len(mine)}   "
             f"both: {len(have)}   only {pname}: {len(lack)}   only yours: {len(mine) - len(have)}",
             *[f"note: {n}" for n in notes], "",
             f"== On {pname}, you DON'T have it ({len(lack)}), most popular first"]
    lines += [f"{title_of(m)}  {{tmdb-{m['id']}}}" for m in lack]
    lines += ["", f"== On {pname}, you HAVE it ({len(have)}), most popular first"]
    lines += [f"{title_of(m)}  {{tmdb-{m['id']}}}" for m in have]
    with open(base + ".txt", "w", encoding="utf-8") as tf:
        tf.write("\n".join(lines) + "\n")

    print()
    print(f"Full lists: {base}.txt")
    print(f"Spreadsheet: {base}.csv")
    print(f"{pname}'s list as of today: {snap}")
    print("Where-to-watch data: JustWatch, via TMDB.")
    return 0
