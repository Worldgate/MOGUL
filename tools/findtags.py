#!/usr/bin/env python3
"""Are there tags buried in the middle of an mp3?

    python3 tools/findtags.py FILE.mp3 [FILE2.mp3 ...]

When mp3s are glued together without stripping their tags first, each
piece's tags end up in the middle of the file. A player skips them, but
they show up as "broken spots" in radio check (junk between frames, often
heard as a faint tick), and an ID3 tag's empty padding shows up as a run
of zero bytes.

Looks for:
  ID3v2 tags   "ID3" + a valid tag header (start of each glued piece)
  ID3v1 tags   "TAG" 128 bytes before the next audio frame (end of a piece)
  APE tags     "APETAGEX"
  Lyrics3      "LYRICS200"
  Xing/Info    the header frame every LAME-made mp3 starts with; more than
               one means pieces were joined (and the first one's length,
               which players believe, only covers the first piece)

Each one is shown with its time in the show (from ffprobe's frame list),
so you can compare with the times radio check / tools/listen.py gave.
Reads only; changes nothing.
"""

import bisect
import mmap
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mogul_core.damage import hms, read_packets   # noqa: E402

ID3V2 = re.compile(rb"ID3[\x02-\x04]\x00[\x00-\xf0][\x00-\x7f]{4}", re.S)
MARKERS = [(b"APETAGEX", "APE tag"), (b"LYRICS200", "Lyrics3 tag")]
XING = re.compile(rb"Xing|Info")


def syncsafe(b):
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


def is_frame_start(mm, pos):
    """Looks like an MPEG audio frame header (11 sync bits + sane fields)."""
    if pos + 4 > len(mm):
        return False
    b1, b2, b3 = mm[pos + 1], mm[pos + 2], mm[pos + 3]
    return (mm[pos] == 0xFF and (b1 & 0xE0) == 0xE0 and (b1 >> 3) & 3 != 1
            and (b1 >> 1) & 3 != 0 and (b2 >> 4) != 0xF and (b2 >> 2) & 3 != 3)


def scan(path):
    found = []
    size = os.path.getsize(path)
    if size == 0:
        return found, size
    with open(path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
        for m in ID3V2.finditer(mm):
            pos = m.start()
            tag_size = syncsafe(mm[pos + 6:pos + 10]) + 10
            found.append((pos, f"ID3v2.{mm[pos + 3]} tag", tag_size))
        for needle, label in MARKERS:
            start = 0
            while True:
                pos = mm.find(needle, start)
                if pos < 0:
                    break
                found.append((pos, label, None))
                start = pos + 1
        # ID3v1: "TAG" followed 128 bytes later by a frame start, or by the end
        start = 0
        while True:
            pos = mm.find(b"TAG", start)
            if pos < 0:
                break
            end = pos + 128
            if end == size or is_frame_start(mm, end) or mm[end:end + 3] == b"ID3":
                found.append((pos, "ID3v1 tag", 128))
            start = pos + 1
        # Xing/Info: sits 4 + (17 or 32 or 9 or 13) bytes into a frame
        for m in XING.finditer(mm):
            pos = m.start()
            if any(is_frame_start(mm, pos - k) for k in (21, 36, 13, 17)):
                found.append((pos, f"{m.group().decode()} header frame", None))
    found.sort()
    return found, size


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    for path in sys.argv[1:]:
        print(path)
        try:
            found, size = scan(path)
        except (OSError, ValueError) as e:
            print(f"  can't read: {e}\n")
            continue
        packets = read_packets(path)
        positions = [p[1] for p in packets]

        def when(pos):
            if not packets:
                return "?"
            i = bisect.bisect_right(positions, pos) - 1
            if i < 0:
                return "start"
            if i >= len(packets) - 1 and pos > positions[-1] + packets[-1][2]:
                return "end"
            return hms(packets[i][0])

        middle = [x for x in found if 0 < x[0] < size - 200]
        xing = [x for x in found if "header frame" in x[1]]
        for pos, label, length in found:
            where = "start of file" if pos < 4096 else ("end of file" if pos >= size - 512 else when(pos))
            extra = f", {length:,} bytes" if length else ""
            print(f"  {where:>13}  {label}  (offset {pos:,}{extra})")
        if not found:
            print("  no tags or header frames found")
        tags_mid = [x for x in middle if "tag" in x[1]]
        print(f"  -> {len(tags_mid)} tag(s) in the middle, {len(xing)} Xing/Info header frame(s)"
              + ("  (looks glued together from pieces)" if tags_mid or len(xing) > 1 else ""))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
