#!/usr/bin/env python3
"""Where in the audio is a file damaged?

    python3 tools/damagemap.py FILE.mp3

Reads the file's audio frames with ffprobe and finds every spot where the
reader had to skip junk bytes to find the next good frame (the same spots
mp3val calls "MPEG stream error, resynchronized"). Prints each spot as a
time in the show, how much junk was skipped, and roughly how much audio
that junk replaced. Spots close together are grouped into one stretch.

What it means when you listen there:
  - a click, bloop or chirp: a damaged frame got played as noise
  - a jump: the junk replaced real audio, so a bit of the show is gone
    (the "about N s missing" column is a rough guess of how much)
Reads only; changes nothing.
"""

import bisect
import re
import shutil
import statistics
import subprocess
import sys

GROUP_SECONDS = 10      # spots closer than this are one stretch


def hms(t):
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}"


def read_packets(path):
    """(time, byte position, size) for every audio frame."""
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


def mp3val_offsets(path):
    """Byte offsets where mp3val had to resync (damaged spots), or None."""
    if not shutil.which("mp3val"):
        return None
    proc = subprocess.run(["mp3val", path], capture_output=True, text=True)
    return [int(m.group(1), 16) for m in re.finditer(r"offset (0x[0-9a-fA-F]+)\): MPEG stream error",
                                                    proc.stdout)]


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 1
    path = sys.argv[1]
    packets = read_packets(path)
    if len(packets) < 2:
        print("Couldn't read any audio frames from that file.")
        return 1

    duration = packets[-1][0]
    sizes = [p[2] for p in packets]
    normal = statistics.median(sizes)
    bytes_per_sec = sum(sizes) / duration if duration else 0
    positions = [p[1] for p in packets]

    offsets = mp3val_offsets(path)
    spots = {}                                   # packet index -> junk bytes
    if offsets is not None:
        how = "mp3val"
        for off in offsets:
            i = max(bisect.bisect_right(positions, off) - 1, 0)
            spots[i] = max(sizes[i] - normal, 0)
    else:
        # no mp3val: a damaged frame shows up as a frame much bigger than
        # normal (the junk gets glued onto it). Rough for VBR files.
        how = "frame sizes (install mp3val for exact spots)"
        for i, size in enumerate(sizes):
            if size > normal * 1.6:
                spots[i] = size - normal
    spots = sorted((packets[i][0], junk) for i, junk in spots.items())

    print(f"{path}")
    print(f"  length {hms(duration)}, {len(packets)} frames, {len(spots)} damaged spot(s) (found by {how})")
    if not spots:
        print("  No damaged spots found.")
        return 0

    groups = []
    for t, junk in spots:
        if groups and t - groups[-1]["end"] <= GROUP_SECONDS:
            g = groups[-1]
            g["end"] = t
            g["count"] += 1
            g["junk"] += junk
        else:
            groups.append({"start": t, "end": t, "count": 1, "junk": junk})

    print()
    print(f"  {'where':<19} {'spots':>5}   audio likely missing")
    for g in groups:
        where = hms(g["start"]) if g["start"] == g["end"] else f"{hms(g['start'])}-{hms(g['end'])}"
        missing = g["junk"] / bytes_per_sec if bytes_per_sec else 0
        print(f"  {where:<19} {g['count']:>5}   about {missing:.1f} s")
    total = sum(j for _, j in spots)
    print()
    print(f"  Damage runs from {hms(spots[0][0])} to {hms(spots[-1][0])}, in {len(groups)} stretch(es); "
          f"about {total / bytes_per_sec:.1f} s of audio missing in all.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
