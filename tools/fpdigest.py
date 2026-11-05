#!/usr/bin/env python3
"""
fpdigest.py - show the short MOGUL fingerprint digest for audio files.

Usage:
  fpdigest.py FILE [FILE ...]
      Print the digest for each file.

  fpdigest.py --compare FILE_A FILE_B
      Print both digests and how similar they are.

FILE can be an audio file (needs fpcalc from chromaprint installed)
or a saved dump from:  fpcalc -raw -length 0 FILE > dump.raw

Digest = one 32-bit value per minute of audio, written as 8 hex chars each.
95%+ similar = probably the same recording; lower = probably different.
"""
import subprocess
import sys

FRAMES_PER_MINUTE = 485  # chromaprint makes ~8.08 frames per second


def read_raw(path):
    """Get the raw fingerprint list from a dump file or by running fpcalc."""
    text = None
    try:
        with open(path, "r", errors="strict") as f:
            head = f.read(4096)
        if "FINGERPRINT=" in head or "DURATION=" in head:
            with open(path, "r") as f:
                text = f.read()
    except (UnicodeDecodeError, OSError):
        text = None

    if text is None:
        try:
            result = subprocess.run(
                ["fpcalc", "-raw", "-length", "0", path],
                capture_output=True, text=True, check=True,
            )
        except FileNotFoundError:
            sys.exit("fpcalc not found. Install it with:  sudo apt install libchromaprint-tools")
        except subprocess.CalledProcessError as e:
            sys.exit(f"fpcalc failed on {path}:\n{e.stderr.strip()}")
        text = result.stdout

    duration = None
    frames = None
    for line in text.splitlines():
        if line.startswith("DURATION="):
            duration = float(line.split("=", 1)[1])
        elif line.startswith("FINGERPRINT="):
            frames = [int(x) for x in line.split("=", 1)[1].strip().split(",") if x]
    if not frames:
        sys.exit(f"No fingerprint found for {path}")
    return duration, frames


def make_digest(frames):
    """One 32-bit value per minute: each bit = majority vote across that minute."""
    values = []
    for start in range(0, len(frames), FRAMES_PER_MINUTE):
        chunk = frames[start:start + FRAMES_PER_MINUTE]
        value = 0
        for bit in range(32):
            ones = sum((x >> bit) & 1 for x in chunk)
            if ones * 2 > len(chunk):
                value |= 1 << bit
        values.append(value)
    return values


def to_hex(values):
    return "".join(f"{v:08x}" for v in values)


def similarity(a, b):
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    diff = sum(bin(a[i] ^ b[i]).count("1") for i in range(n))
    return 100.0 - 100.0 * diff / (32 * n)


def show(path):
    duration, frames = read_raw(path)
    digest = make_digest(frames)
    hx = to_hex(digest)
    mins = f"{duration / 60:.1f} min" if duration else "unknown length"
    print(f"{path}")
    print(f"  length:  {mins}, {len(frames)} frames, {len(digest)} digest values")
    print(f"  size:    {len(hx)} characters")
    print(f"  digest:  {hx}")
    return digest


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__.strip())
        return
    if args[0] == "--compare":
        if len(args) != 3:
            sys.exit("Usage: fpdigest.py --compare FILE_A FILE_B")
        a = show(args[1])
        print()
        b = show(args[2])
        print()
        score = similarity(a, b)
        if len(a) != len(b):
            print(f"note: lengths differ ({len(a)} vs {len(b)} minutes), compared the first {min(len(a), len(b))}")
        verdict = "probably the SAME recording" if score >= 95 else "probably DIFFERENT"
        print(f"similarity: {score:.1f}%  ->  {verdict}")
        return
    for i, path in enumerate(args):
        if i:
            print()
        show(path)


if __name__ == "__main__":
    main()
