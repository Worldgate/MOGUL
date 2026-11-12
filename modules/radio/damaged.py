"""Radio damaged list (mogul.py radio damaged).

Lists every cataloged recording with damaged spots (bad mp3 frames that a
player or ffmpeg skips over), so they can be listened to and judged by hand.

Where the flag comes from: when fpcalc can't read a file by itself, the
fingerprint is made through ffmpeg instead, and the entry gets

    "status": {"damaged": {"date": ..., "decode_errors": 3,
                           "found_by": "fingerprint", "checked": null}}

After listening:
  --ok NAME    it plays fine; marks it checked "ok" (stays in the library)
  --bad NAME   it really is broken; marks it checked "bad". The next
               `mogul.py radio quarantine` pulls it out.
  --unchecked  only list the ones not judged yet

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


def run(args, config):
    json_root = config.json_store
    manifests, _ = load_catalog(json_root)

    verdicts = {}
    for name in args.ok or []:
        verdicts[name] = "ok"
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
            rows.append((entry["naming"]["filename"], d.get("decode_errors", 0), verdict or "not checked"))

    if not rows:
        print("No damaged recordings in the catalog." if not args.unchecked else "Nothing left to check.")
        return 0
    rows.sort()
    for name, errors, verdict in rows:
        print(f"{name:<40} {errors:>4} damaged spot(s)   {verdict}")
    counts = {}
    for _, _, v in rows:
        counts[v] = counts.get(v, 0) + 1
    print(f"\n{len(rows)} damaged: " + ", ".join(f"{n} {v}" for v, n in sorted(counts.items())))
    return 0
