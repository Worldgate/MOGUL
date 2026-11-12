#!/usr/bin/env python3
"""Listening list for damaged recordings, plus Audacity labels.

    python3 tools/listen.py              files not judged yet, worst first
    python3 tools/listen.py --all        include ones already marked ok/keep/bad
    python3 tools/listen.py --top 25     only the 25 worst
    python3 tools/listen.py --config test.toml

Reads what `mogul.py radio check` found (the radio-check_*.json reports in
mogul_reports/, newest result per file wins) and the catalog (for the
current ok/keep/bad verdict and where the file lives), then writes:

  mogul_reports/listen.txt
      every damaged file, worst first: where it is, its real length vs
      the length its header claims, and EVERY damaged spot with its time
      in the show, so you know where to jump to.

  mogul_reports/audacity_labels/<file name>.txt
      one per damaged file. In Audacity: open the mp3, then
      File > Import > Labels... and pick the matching .txt. Each bad spot
      shows up as a marker on a label track (starting a couple of seconds
      early so you hear it coming). Click a label to jump there.
      If the file is shorter than its header says, there's also a label
      where the audio really ends.

After listening:  mogul.py radio damaged --ok / --keep / --bad NAME
Then:             mogul.py radio quarantine    (pulls out the --bad ones)

Reads only; changes nothing in the catalog. listen.txt and the label
files are rewritten each time it runs (mogul_reports/ is gitignored).
"""

import argparse
import glob
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mogul_core import damage, store                 # noqa: E402
from mogul_core.config import Config                 # noqa: E402
from modules.radio.importer import target_folder     # noqa: E402
from modules.radio.scan import load_catalog          # noqa: E402

LEAD_IN = 2.0      # seconds before a spot where the label starts
TAIL = 1.0         # seconds after
MAX_LINES = 40     # spots listed per file in listen.txt (labels have all)


def hms(t):
    return damage.hms(t)


def latest_results(reports_dir):
    """file name -> newest radio check result (with every stretch)."""
    results = {}
    for path in sorted(glob.glob(os.path.join(reports_dir, "radio-check_*.json"))):
        if path.endswith("_dry-run.json"):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                report = json.load(f)
        except (OSError, ValueError):
            continue
        if report.get("target") != "library" or report.get("dry_run"):
            continue
        for r in report.get("files", []):
            results[r["file"]] = r      # later reports overwrite earlier ones
    return results


def is_damaged(c):
    return (c.get("readable") is False or c.get("spots") or c.get("biggest_gap")
            or (c.get("header_missing_seconds") or 0) >= 1)


def lost(c):
    """Best guess at how much audio is gone."""
    return max(c.get("missing_seconds") or 0, c.get("header_missing_seconds") or 0)


def main():
    ap = argparse.ArgumentParser(description="Listening list + Audacity labels for damaged recordings.")
    ap.add_argument("--all", action="store_true", help="include files already marked ok/keep/bad")
    ap.add_argument("--top", type=int, help="only the N worst")
    ap.add_argument("--config", help="use this mogul.toml instead of the default one")
    args = ap.parse_args()

    config = Config(args.config)
    radio_cfg = config.section("radio")
    library = config.resolve(radio_cfg.get("library", "mogul_library/radio"))
    folder_t = radio_cfg.get("folder_template", "{show}/{yyyy}/{mm}")
    reports = config.reports

    results = latest_results(reports)
    manifests, _ = load_catalog(config.json_store)

    rows = []
    for manifest in manifests.values():
        for entry in manifest.get("files", []):
            status = entry.get("status") or {}
            c = status.get("check")
            if not c or not is_damaged(c):
                continue
            verdict = (((status.get("damaged") or {}).get("checked")) or {}).get("verdict")
            if verdict and not args.all:
                continue
            name = entry["naming"]["filename"]
            full = results.get(name) or {}
            path = (os.path.join(target_folder(library, folder_t, entry), name)
                    if status.get("imported") else None)
            rows.append({"name": name, "check": c, "stretches": full.get("stretches") or [],
                         "verdict": verdict or "not judged", "path": path,
                         "header": entry["physical"].get("duration")})

    rows.sort(key=lambda r: (r["check"].get("readable", True), -lost(r["check"]),
                             -(r["check"].get("spots") or 0), r["name"]))
    if args.top:
        rows = rows[:args.top]

    os.makedirs(reports, exist_ok=True)
    label_dir = os.path.join(reports, "audacity_labels")
    if os.path.isdir(label_dir):
        shutil.rmtree(label_dir)
    os.makedirs(label_dir)

    out = [f"MOGUL listening list - {store.now()}",
           f"{len(rows)} damaged file(s), worst first"
           + ("" if args.all else " (not judged yet; --all for every one)"),
           "Audacity: open the file, then File > Import > Labels... "
           "and pick its .txt from mogul_reports/audacity_labels/",
           ""]

    for n, r in enumerate(rows, 1):
        c = r["check"]
        out.append(f"{n:>3}. {r['name']}   [{r['verdict']}]")
        if r["path"]:
            out.append(f"     {r['path']}")
        out.append(f"     {damage.summary(c)}")
        real = c.get("real_duration")
        if real and r["header"]:
            out.append(f"     length: header says {hms(r['header'])}, really there {hms(real)}")
        for w in c.get("warnings") or []:
            out.append(f"     also: {w}")

        labels = []
        st = r["stretches"]
        if st:
            for i, s in enumerate(st):
                spots = f"{s['count']} bad frame(s)"
                miss = f", ~{s['missing']} s missing" if s.get("missing") else ""
                span = hms(s["start"]) if s["start"] == s["end"] else f"{hms(s['start'])}-{hms(s['end'])}"
                if i < MAX_LINES:
                    out.append(f"       {span:<17} {spots}{miss}")
                labels.append((max(s["start"] - LEAD_IN, 0), s["end"] + TAIL, f"{spots}{miss}"))
            if len(st) > MAX_LINES:
                out.append(f"       ... and {len(st) - MAX_LINES} more (all of them are in the label file)")
        elif c.get("spots") and c.get("first") is not None:
            out.append(f"       spots between {hms(c['first'])} and {hms(c['last'])} "
                       "(run radio check --recheck for every spot)")
            labels.append((max(c["first"] - LEAD_IN, 0), c["first"] + TAIL, "first bad spot"))
            labels.append((max(c["last"] - LEAD_IN, 0), c["last"] + TAIL, "last bad spot"))
        if real and (c.get("header_missing_seconds") or 0) >= 1:
            labels.append((real, real, f"audio ends here; header says ~{c['header_missing_seconds']} s more"))

        if labels:
            stem = os.path.splitext(r["name"])[0]
            with open(os.path.join(label_dir, stem + ".txt"), "w", encoding="utf-8") as f:
                for start, end, text in sorted(labels):
                    f.write(f"{start:.3f}\t{end:.3f}\t{text}\n")
        out.append("")

    out.append("After listening:  python3 mogul.py radio damaged --ok / --keep / --bad NAME")
    out.append("Then:             python3 mogul.py radio quarantine   (moves the --bad ones out)")
    list_path = os.path.join(reports, "listen.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")

    print(f"{len(rows)} damaged file(s)")
    print(f"Listening list:  {list_path}")
    print(f"Audacity labels: {label_dir}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
