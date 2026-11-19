"""mogul.py movie count: how many of your movies each actor, director,
studio... is in.

Reads the movie folders (names only) and the TMDB info saved by
`movie tmdb`. Moves nothing, changes nothing.

Each movie counts once per person/studio, even if you have two copies
of it (same TMDB ID). People and studios are counted by their TMDB ID,
so two different actors who share a name stay separate, and a name
spelled two ways stays one person.

Prints the top of each list and writes the full lists to
mogul_reports/movie-count_<date>_<time>.txt  (readable, with titles)
mogul_reports/movie-count_<date>_<time>.csv  (for a spreadsheet)
"""

import csv
import os
import sys
import time
from collections import defaultdict

from mogul_core import store

from . import library

# What can be counted: name shown, and how to get (id, name) pairs from a movie.
CREW_JOBS = {
    "director": ("Directors", {"Director"}),
    "writer": ("Writers", {"Screenplay", "Writer", "Story", "Novel", "Author", "Characters"}),
    "composer": ("Composers", {"Original Music Composer", "Music"}),
    "producer": ("Producers", {"Producer"}),
    "cinematographer": ("Cinematographers", {"Director of Photography"}),
}
CHOICES = ["cast", "director", "studio", "writer", "composer", "producer",
           "cinematographer", "genre", "keyword", "country", "collection", "decade"]
DEFAULT_BY = ["cast", "director", "studio"]
TITLES = {"cast": "Actors", "studio": "Studios", "genre": "Genres", "keyword": "Keywords",
          "country": "Countries", "collection": "Collections (series)", "decade": "Decades"}


def items(kind, t, top):
    """(key, name) pairs for one movie's TMDB data."""
    credits = t.get("credits", {})
    if kind == "cast":
        cast = sorted(credits.get("cast", []), key=lambda c: c.get("order", 999))
        if top:
            cast = cast[:top]
        return [(c.get("id"), c.get("name", "?")) for c in cast]
    if kind in CREW_JOBS:
        jobs = CREW_JOBS[kind][1]
        return [(c.get("id"), c.get("name", "?")) for c in credits.get("crew", [])
                if c.get("job") in jobs]
    if kind == "studio":
        return [(c.get("id"), c.get("name", "?")) for c in t.get("production_companies", [])]
    if kind == "genre":
        return [(g.get("id"), g.get("name", "?")) for g in t.get("genres", [])]
    if kind == "keyword":
        kw = t.get("keywords", {}).get("keywords", [])
        return [(k.get("id"), k.get("name", "?")) for k in kw]
    if kind == "country":
        return [(c.get("iso_3166_1"), c.get("name", "?")) for c in t.get("production_countries", [])]
    if kind == "collection":
        c = t.get("belongs_to_collection")
        return [(c.get("id"), c.get("name", "?"))] if c else []
    if kind == "decade":
        year = (t.get("release_date") or "")[:4]
        if year.isdigit():
            d = int(year) // 10 * 10
            return [(d, f"{d}s")]
        return [(None, "unknown year")]
    return []


def title_of(t):
    year = (t.get("release_date") or "")[:4]
    return f"{t.get('title', '?')}" + (f" ({year})" if year else "")


def run(args, config):
    store_dir = config.resolve(config.section("movie").get("tmdb_store", "mogul_cache/tmdb/movie"))
    by = args.by or DEFAULT_BY
    folders = library.roots(config, args.path)

    print("MOGUL movie count (reads only, changes nothing)")
    for f in folders:
        print(f"  reading names in {f} ...")
    units = library.find_units(folders)
    ids = sorted({tid for tid, _, _ in units})
    print(f"  movies:    {len(ids)}" + (f"  ({len(units)} folders/files)" if len(units) != len(ids) else ""))

    movies, missing = {}, 0
    for tid in ids:
        try:
            movies[tid] = store.load(os.path.join(store_dir, f"{tid}.json")).get("tmdb", {})
        except (OSError, ValueError):
            missing += 1
    if missing:
        print(f"  {missing} movie(s) have no saved TMDB info and aren't counted "
              "(run: mogul.py movie tmdb)")
    print()

    # tally: kind -> key -> {"name": ..., "movies": set()}
    tally = {k: defaultdict(lambda: {"name": None, "movies": set()}) for k in by}
    for tid, t in movies.items():
        for kind in by:
            seen = set()
            for key, name in items(kind, t, args.top):
                k = key if key is not None else name
                if k in seen:
                    continue  # e.g. someone credited twice in one movie
                seen.add(k)
                entry = tally[kind][k]
                entry["name"] = entry["name"] or name
                entry["movies"].add(tid)

    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    base = os.path.join(config.reports, f"movie-count_{stamp}")
    os.makedirs(config.reports, exist_ok=True)
    lines = [f"MOGUL movie count  {store.now()}",
             f"Folders: {', '.join(folders)}",
             f"Movies counted: {len(movies)}" + (f" ({missing} without TMDB info not counted)" if missing else ""),
             ""]
    with open(base + ".csv", "w", newline="", encoding="utf-8") as cf:
        w = csv.writer(cf)
        w.writerow(["list", "name", "tmdb_id", "count", "movies"])
        for kind in by:
            label = TITLES.get(kind) or CREW_JOBS[kind][0]
            if kind == "cast" and args.top:
                label += f" (top {args.top} billed)"
            rows = [(len(e["movies"]), e["name"], key, e["movies"]) for key, e in tally[kind].items()
                    if len(e["movies"]) >= args.min]
            rows.sort(key=lambda r: (-r[0], (r[1] or "").casefold()))

            # screen: the top ones
            shown = rows if args.show == 0 else rows[:args.show]
            print(f"== {label}: {len(rows)}" + (f" (showing top {len(shown)})" if len(shown) < len(rows) else ""))
            width = len(str(rows[0][0])) if rows else 1
            for count, name, _, _ in shown:
                print(f"  {count:>{width}}  {name}")
            print()

            # files: everything, with titles
            lines.append(f"== {label}: {len(rows)}")
            for count, name, key, mids in rows:
                titles = sorted((title_of(movies[m]) for m in mids), key=str.casefold)
                lines.append(f"{count:>{width}}  {name}")
                for ti in titles:
                    lines.append(f"{'':>{width}}      {ti}")
                w.writerow([kind, name, key, count, " | ".join(titles)])
            lines.append("")
    with open(base + ".txt", "w", encoding="utf-8") as tf:
        tf.write("\n".join(lines) + "\n")

    print(f"Full lists with every title: {base}.txt")
    print(f"For a spreadsheet:           {base}.csv")
    return 0
