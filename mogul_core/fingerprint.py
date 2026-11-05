"""Short acoustic fingerprint digest (chromaprint / fpcalc).

The digest is one 32-bit value per minute of audio: each bit is set
to whichever value it has most often across that minute's
fingerprint frames. Written as 8 hex characters per minute.

Two encodes of the same recording score about 98-99% alike; different
shows score about 83-86%. 95% and up = probably the same recording.
"""

import subprocess

ALGORITHM = "chromaprint"
DIGEST_VERSION = 1
FRAMES_PER_MINUTE = 485  # chromaprint makes about 8.08 frames per second


class FingerprintError(Exception):
    pass


def raw_frames(path):
    try:
        proc = subprocess.run(
            ["fpcalc", "-raw", "-length", "0", path],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        raise FingerprintError("fpcalc not found (sudo apt install libchromaprint-tools)")
    if proc.returncode != 0:
        raise FingerprintError(proc.stderr.strip() or "fpcalc failed")
    for line in proc.stdout.splitlines():
        if line.startswith("FINGERPRINT="):
            frames = [int(x) for x in line.split("=", 1)[1].strip().split(",") if x]
            if frames:
                return frames
    raise FingerprintError("fpcalc returned no fingerprint")


def make_digest(frames):
    values = []
    for start in range(0, len(frames), FRAMES_PER_MINUTE):
        chunk = frames[start:start + FRAMES_PER_MINUTE]
        value = 0
        for bit in range(32):
            if sum((x >> bit) & 1 for x in chunk) * 2 > len(chunk):
                value |= 1 << bit
        values.append(value)
    return values


def digest_entry(path):
    values = make_digest(raw_frames(path))
    return {
        "algorithm": ALGORITHM,
        "digest_version": DIGEST_VERSION,
        "digest": "".join(f"{v:08x}" for v in values),
        "digest_minutes": len(values),
    }


def similarity(hex_a, hex_b):
    a = [int(hex_a[i:i + 8], 16) for i in range(0, len(hex_a) - 7, 8)]
    b = [int(hex_b[i:i + 8], 16) for i in range(0, len(hex_b) - 7, 8)]
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    diff = sum(bin(a[i] ^ b[i]).count("1") for i in range(n))
    return 100.0 - 100.0 * diff / (32 * n)
