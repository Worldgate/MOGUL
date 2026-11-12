"""Radio damaged list (mogul.py radio damaged).

Lists every cataloged recording with damaged spots (bad mp3 frames that a
player or ffmpeg skips over), so they can be listened to and judged by hand.

Where the flag comes from: when fpcalc can't read a file by itself, the
fingerprint is made through ffmpeg instead, and the entry gets

    "status": {"damaged": {"date": ..., "decode_errors": 3,
                           "found_by": "fingerprint", "checked": null}}

After listening:
  --ok NAME    it plays fine; marks it checked "ok" (stays in the library)
  --keep NAME  playable but damaged (clicks, bloops); stays in the library
               as the copy we have, flagged as wanting a cleaner copy
  --bad NAME   it really is broken; marks it checked "bad". The next
               `mogul.py radio quarantine` pulls it out.
  --note TEXT  your own words, saved with the verdict (e.g. "faint tap at
               1:04:31, seam" or "last 2 hours missing")
  --auto-ok    mark the tiny-damage, full-length ones ok without listening
  --unchecked  only list the ones not judged yet
  --wanted     only list the "keep" ones (the want-a-better-copy list)

NAME is the library filename (e.g. 2002-07-01_O&A_969283BA.mp3).
Nothing is moved by this command.
"""

from mogul_core import VERSION, store

from .scan import load_catalog


def mark_damaged(entry, fp, found_by="fingerprint"):
    """Set status.damaged on an entry whose fingerprint needed ffmpeg's help."""
    if not fp or not fp.get("decode_errors"):
        return
    status = entry.setdefault("status", {})
    old = status.get("damaged") or {}
    status["damaged"] = {
        "date": store.now(),
        "decode_errors": fp["decode_errors"],
        "found_by": found_by,
        "checked": old.get("checked"),
    }


def damage_of(entry):
    """The damaged flag for an entry, or None. Also covers entries
    fingerprinted before the flag existed (fingerprint.decode_errors only)."""
    d = (entry.get("status") or {}).get("damaged")
    if d:
        return d
    errors = (entry.get("fingerprint") or {}).get("decode_errors")
    if errors:
        return {"date": None, "decode_errors": errors, "found_by": "fingerprint", "checked": None}
    return None


def auto_ok_reason(entry, max_missing):
    """Why this entry can be marked ok without listening, or None.

    Only files `radio check` has looked at: readable, an MP3 with every
    spot timed, no more than max_missing seconds lost in total, real
    length matching the header (nothing cut out or cut off), and no empty
    gap. That's the "seam" kind of damage: a broken frame where two
    pieces were joined, heard as a faint tick at most."""
    status = entry.get("status") or {}
    c = status.get("check")
    if not c or c.get("readable") is False:
        return None
    if ((status.get("damaged") or {}).get("checked")):
        return None                                   # already judged
    if not c.get("spots") or c.get("first") is None:
        return None                                   # nothing timed (not mp3val)
    if (c.get("missing_seconds") or 0) > max_missing:
        return None
    if (c.get("header_missing_seconds") or 0) >= 1:
        return None
    if c.get("biggest_gap"):
        return None
    return f"{c['spots']} spot(s), ~{c.get('missing_seconds') or 0} s missing, full length"


def auto_ok(args, config, manifests):
    radio_cfg = config.section("radio")
    max_missing = args.max_missing if args.max_missing is not None \
        else float(radio_cfg.get("auto_ok_max_missing", 0.5))
    rule = f"<= {max_missing} s missing, real length matches header, no empty gap"
    marked = 0
    for mpath, manifest in manifests.items():
        changed = False
        for entry in manifest.get("files", []):
            why = auto_ok_reason(entry, max_missing)
            if not why:
                continue
            marked += 1
            print(f"OK   {entry['naming']['filename']:<40} {why}")
            if args.dry_run:
                continue
            status = entry.setdefault("status", {})
            d = damage_of(entry) or {"date": None, "decode_errors": 0, "found_by": "check"}
            d["checked"] = {"date": store.now(), "verdict": "ok", "by": "auto", "rule": rule,
                            "note": args.note or f"auto: {why}"}
            status["damaged"] = d
            changed = True
        if changed:
            manifest["header"]["updated"] = store.now()
            manifest["header"]["mogul_version"] = VERSION
            store.save(mpath, manifest)
    print(f"\n{marked} file(s) {'would be marked' if args.dry_run else 'marked'} ok ({rule})")
    if args.dry_run:
        print("DRY RUN: nothing saved. Run again without --dry-run to mark them.")
    else:
        print("What's left to listen to:  python3 tools/listen.py")
    return 0


def run(args, config):
    json_root = config.json_store
    manifests, _ = load_catalog(json_root)

    if args.auto_ok:
        return auto_ok(args, config, manifests)

    verdicts = {}
    for name in args.ok or []:
        verdicts[name] = "ok"
    for name in args.keep or []:
        verdicts[name] = "keep"
    for name in args.bad or []:
        verdicts[name] = "bad"

    if verdicts:
        found = set()
        for mpath, manifest in manifests.items():
            changed = False
            for entry in manifest.get("files", []):
                name = entry["naming"]["filename"]
                if name not in verdicts:
                    continue
                found.add(name)
                status = entry.setdefault("status", {})
                d = damage_of(entry) or {"date": None, "decode_errors": 0, "found_by": "by hand"}
                d["checked"] = {"date": store.now(), "verdict": verdicts[name]}
                if args.note:
                    d["checked"]["note"] = args.note
                status["damaged"] = d
                changed = True
                print(f"{verdicts[name].upper():<4} {name}")
            if changed:
                manifest["header"]["updated"] = store.now()
                manifest["header"]["mogul_version"] = VERSION
                store.save(mpath, manifest)
        for name in sorted(set(verdicts) - found):
            print(f"NOT FOUND  {name} (check the exact library filename)")
        if any(v == "bad" for n, v in verdicts.items() if n in found):
            print("\nPull the bad ones out with:  mogul.py radio quarantine")
        return 0

    rows = []
    for manifest in manifests.values():
        for entry in manifest.get("files", []):
            d = damage_of(entry)
            if not d:
                continue
            verdict = (d.get("checked") or {}).get("verdict")
            if args.unchecked and verdict:
                continue
            unreadable = ((entry.get("status") or {}).get("check") or {}).get("readable") is False
            note = (d.get("checked") or {}).get("note")
            if note and verdict:
                verdict = f"{verdict}: {note}"
            rows.append((entry["naming"]["filename"], -1 if unreadable else d.get("decode_errors", 0),
                         verdict or "not checked"))

    if args.wanted:
        rows = [r for r in rows if r[2] == "keep"]
    if not rows:
        print("No damaged recordings in the catalog." if not args.unchecked else "Nothing left to check.")
        return 0
    rows.sort(key=lambda r: (r[1] >= 0, -r[1], r[0]))   # can't read first, then worst first
    for name, errors, verdict in rows:
        what = "    can't be read" if errors < 0 else f"{errors:>4} damaged spot(s)"
        print(f"{name:<40} {what}   {verdict}")
    counts = {}
    for _, _, v in rows:
        v = v.split(":")[0]
        counts[v] = counts.get(v, 0) + 1
    print(f"\n{len(rows)} damaged: " + ", ".join(f"{n} {v}" for v, n in sorted(counts.items())))
    return 0
