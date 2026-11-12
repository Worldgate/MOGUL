"""Find damage in an audio file.

analyze(path) reads the whole file and reports:
  - damaged spots: places where the audio stream is broken (for MP3s,
    where mp3val had to skip junk to find the next good frame; for other
    formats, decoder errors from ffmpeg)
  - for MP3s, where they are in the show (h:mm:ss), grouped into
    stretches, and roughly how many seconds of audio the junk replaced
  - other mp3val warnings (missing VBR header, mixed MPEG versions, ...)
  - how much of the file is empty (zero bytes) and the biggest empty gap

Reads only; changes nothing.
"""

import bisect
import os
import re
import shutil
import statistics
import subprocess

GROUP_SECONDS = 10          # spots closer than this are one stretch
CHUNK = 4 * 1024 * 1024
MIN_GAP = 512               # runs of zero bytes shorter than this are normal audio

# mp3val warnings that aren't problems for us
IGNORED_WARNINGS = ("No supported tags in the file",)

# mp3val's wordy warnings -> short versions (matched by how they start)
SHORT_WARNINGS = (
    ("Wrong number of MPEG", "VBR header counts don't match the audio (length/seeking may be off)"),
    ("VBR detected, but no VBR header", "VBR without a VBR header (seeking may be off)"),
    ("Different MPEG versions or layers", "mixed MPEG versions (stray junk frame)"),
)


def shorten(text):
    for start, short in SHORT_WARNINGS:
        if text.startswith(start):
            return short
    return text


def hms(t):
    t = int(t or 0)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}"


# ---------------------------------------------------------------- empty bytes

def empty_report(path):
    """How much of the file is empty (zero bytes), and the biggest empty stretch."""
    size = os.path.getsize(path)
    zeros = 0
    best = (0, 0)          # (length, start) of the biggest gap
    run, run_start = 0, 0  # a gap still open at the end of the previous chunk
    pos = 0
    gap = re.compile(rb"\x00{%d,}" % MIN_GAP)
    with open(path, "rb") as f:
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                break
            zeros += chunk.count(0)
            i = 0
            if run:                                   # continue an open gap
                lead = len(chunk) - len(chunk.lstrip(b"\0"))
                run += lead
                i = lead
                if lead < len(chunk):                 # gap closed in this chunk
                    if run > best[0]:
                        best = (run, run_start)
                    run = 0
            if not run:
                for m in gap.finditer(chunk, i):
                    if m.end() == len(chunk):         # may continue in next chunk
                        run, run_start = m.end() - m.start(), pos + m.start()
                    elif m.end() - m.start() > best[0]:
                        best = (m.end() - m.start(), pos + m.start())
                if not run:
                    tail = len(chunk) - len(chunk.rstrip(b"\0"))
                    if tail:
                        run, run_start = tail, pos + len(chunk) - tail
            pos += len(chunk)
    if run >= MIN_GAP and run > best[0]:
        best = (run, run_start)
    if best[0] < MIN_GAP:
        best = (0, 0)

    with open(path, "rb") as f:
        head = f.read(CHUNK)
    lead = len(head) - len(head.lstrip(b"\0"))
    if lead == len(head) and best[1] == 0:
        lead = best[0]

    def pct(n):
        return round(100.0 * n / size, 1) if size else 0.0

    return {
        "size": size,
        "zero_bytes": zeros,
        "zero_percent": pct(zeros),
        "biggest_empty_stretch": {"start": best[1], "length": best[0],
                                  "start_percent": pct(best[1]), "length_percent": pct(best[0])},
        "empty_at_start": lead,
    }


def describe_empty(d):
    big = d["biggest_empty_stretch"]
    parts = [f"{d['zero_percent']}% empty"]
    if big["length"] >= 4096:
        parts.append(f"biggest gap {big['length_percent']}% of the file at {big['start_percent']}%")
    if d["empty_at_start"] >= 4096:
        parts.append("starts empty")
    return ", ".join(parts)


# ---------------------------------------------------------------- frames

def read_packets(path):
    """(time, byte position, size) for every audio frame, via ffprobe."""
    proc = subprocess.run(
        ["ffprobe", "-v", "quiet", "-select_streams", "a:0",
         "-show_entries", "packet=pts_time,pos,size", "-of", "compact=p=0", path],
        capture_output=True, text=True,
    )
    packets = []
    for line in proc.stdout.splitlines():
        f = dict(kv.split("=", 1) for kv in line.strip().split("|") if "=" in kv)
        try:
            packets.append((float(f["pts_time"]), int(f["pos"]), int(f["size"])))
        except (KeyError, ValueError):
            continue
    return packets


def run_mp3val(path):
    """(resync offsets, frame count, other warnings) from mp3val, or None
    if mp3val isn't installed."""
    if not shutil.which("mp3val"):
        return None
    proc = subprocess.run(["mp3val", path], capture_output=True, text=True, errors="replace")
    offsets, warnings, frames = [], [], None
    for line in proc.stdout.splitlines():
        m = re.search(r"offset (0x[0-9a-fA-F]+)\): MPEG stream error", line)
        if m:
            offsets.append(int(m.group(1), 16))
            continue
        m = re.search(r"(\d+) MPEG frames", line)
        if m and line.startswith("INFO"):
            frames = int(m.group(1))
            continue
        if line.startswith(("WARNING", "ERROR")):
            text = line.split('":', 1)[-1].strip() if '":' in line else line
            text = re.sub(r"^\(offset 0x[0-9a-fA-F]+\):\s*", "", text)
            text = shorten(text)
            if text and text not in IGNORED_WARNINGS and text not in warnings:
                warnings.append(text)
    return offsets, frames, warnings


def ffmpeg_errors(path):
    """(error count, readable) from decoding the whole file with ffmpeg."""
    proc = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", path, "-map", "0:a:0", "-f", "null", "-"],
        capture_output=True, text=True, errors="replace",
    )
    lines = [l for l in proc.stderr.splitlines() if l.strip()]
    return len(lines), proc.returncode == 0, (lines[0] if lines else "")


def group_spots(spots):
    """[(time, junk bytes)] -> stretches of spots close together."""
    groups = []
    for t, junk in spots:
        if groups and t - groups[-1]["end"] <= GROUP_SECONDS:
            g = groups[-1]
            g["end"] = t
            g["count"] += 1
            g["junk"] += junk
        else:
            groups.append({"start": t, "end": t, "count": 1, "junk": junk})
    return groups


# ---------------------------------------------------------------- main entry

def analyze(path, with_empty=True):
    """Full damage check of one file. Returns a dict (see module docstring)."""
    result = {
        "tool": None,
        "readable": True,
        "error": None,
        "spots": 0,
        "stretches": [],          # [{start, end, count, missing}] (seconds)
        "first": None,
        "last": None,
        "missing_seconds": 0.0,
        "warnings": [],
    }
    is_mp3 = path.lower().endswith(".mp3")
    mp3val = run_mp3val(path) if is_mp3 else None

    if mp3val is not None:
        result["tool"] = "mp3val"
        offsets, frames, warnings = mp3val
        result["warnings"] = warnings
        if not frames:
            result["readable"] = False
            result["error"] = "mp3val found no MPEG frames"
        elif offsets:
            packets = read_packets(path)
            if len(packets) >= 2:
                duration = packets[-1][0]
                sizes = [p[2] for p in packets]
                normal = statistics.median(sizes)
                bytes_per_sec = sum(sizes) / duration if duration else 0
                positions = [p[1] for p in packets]
                spots = {}
                for off in offsets:
                    i = max(bisect.bisect_right(positions, off) - 1, 0)
                    spots[i] = max(sizes[i] - normal, 0)
                spot_list = sorted((packets[i][0], junk) for i, junk in spots.items())
                for g in group_spots(spot_list):
                    result["stretches"].append({
                        "start": round(g["start"], 1), "end": round(g["end"], 1), "count": g["count"],
                        "missing": round(g["junk"] / bytes_per_sec, 2) if bytes_per_sec else 0.0,
                    })
                result["spots"] = len(spot_list)
                result["first"] = round(spot_list[0][0], 1)
                result["last"] = round(spot_list[-1][0], 1)
                result["missing_seconds"] = round(sum(s["missing"] for s in result["stretches"]), 1)
            else:
                result["spots"] = len(offsets)
    else:
        # not an MP3 (or no mp3val): count decoder errors; no positions
        result["tool"] = "ffmpeg"
        count, ok, first_line = ffmpeg_errors(path)
        result["spots"] = count
        if not ok:
            result["readable"] = False
            result["error"] = first_line or "ffmpeg couldn't decode the file"

    if with_empty:
        try:
            e = empty_report(path)
            result["empty_percent"] = e["zero_percent"]
            big = e["biggest_empty_stretch"]
            if big["length"] >= 4096:
                result["biggest_gap"] = {"bytes": big["length"], "at_percent": big["start_percent"]}
        except OSError:
            pass
    return result


def summary(r):
    """One line: '314 spots in 156 stretches, 2:43:47-4:00:42, ~8.9 s missing'."""
    if not r.get("readable", True):
        return f"can't read: {r.get('error') or 'unknown error'}"
    if not r.get("spots"):
        text = "clean"
    elif r.get("first") is None:
        text = f"{r['spots']} decode error(s)"
    else:
        st = r.get("stretches") or 0
        n = st if isinstance(st, int) else len(st)
        where = hms(r["first"]) if r["first"] == r["last"] else f"{hms(r['first'])}-{hms(r['last'])}"
        text = (f"{r['spots']} spot(s) in {n} stretch(es), {where}, "
                f"~{r.get('missing_seconds', 0)} s missing")
    if r.get("biggest_gap"):
        g = r["biggest_gap"]
        text += f"; empty gap {g['bytes'] // 1024} KB at {g['at_percent']}%"
    return text
