"""Short acoustic fingerprint digest (chromaprint / fpcalc).

The digest is one 32-bit value per minute of audio: each bit is set
to whichever value it has most often across that minute's
fingerprint frames. Written as 8 hex characters per minute.

Two encodes of the same recording score about 98-99% alike; different
shows score about 83-86%. 95% and up = probably the same recording.
"""

import subprocess
import tempfile

ALGORITHM = "chromaprint"
DIGEST_VERSION = 1
FRAMES_PER_MINUTE = 485  # chromaprint makes about 8.08 frames per second


class FingerprintError(Exception):
    pass


def _parse(stdout):
    for line in stdout.splitlines():
        if line.startswith("FINGERPRINT="):
            frames = [int(x) for x in line.split("=", 1)[1].strip().split(",") if x]
            if frames:
                return frames
    return None


def _errors(stderr):
    lines = [l.strip().removeprefix("ERROR: ") for l in stderr.strip().splitlines() if l.strip()]
    return "; ".join(lines) or "fpcalc failed"


def raw_frames(path):
    """Fingerprint frames for the whole file. Returns (frames, decode_errors).

    fpcalc gives up at the first damaged mp3 frame. If that happens, the
    audio is decoded by ffmpeg instead (which skips bad frames, like a
    player does) and piped into fpcalc. decode_errors is how many errors
    ffmpeg reported (0 when fpcalc read the file by itself)."""
    try:
        proc = subprocess.run(
            ["fpcalc", "-raw", "-length", "0", path],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        raise FingerprintError("fpcalc not found (sudo apt install libchromaprint-tools)")
    frames = _parse(proc.stdout) if proc.returncode == 0 else None
    if frames:
        return frames, 0
    first_error = _errors(proc.stderr) if proc.returncode != 0 else "fpcalc returned no fingerprint"

    # fallback: let ffmpeg decode (skipping bad frames) and feed fpcalc
    with tempfile.TemporaryFile() as err:
        try:
            dec = subprocess.Popen(
                ["ffmpeg", "-nostdin", "-v", "error", "-i", path, "-map", "0:a:0", "-f", "wav", "-"],
                stdout=subprocess.PIPE, stderr=err,
            )
        except FileNotFoundError:
            raise FingerprintError(first_error)
        fp = subprocess.run(["fpcalc", "-raw", "-length", "0", "-"], stdin=dec.stdout,
                            capture_output=True, text=True)
        dec.stdout.close()
        dec.wait()
        err.seek(0)
        decode_errors = sum(1 for l in err.read().decode("utf-8", "replace").splitlines() if l.strip())
    frames = _parse(fp.stdout) if fp.returncode == 0 else None
    if not frames:
        raise FingerprintError(first_error)
    return frames, max(decode_errors, 1)


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
    frames, decode_errors = raw_frames(path)
    values = make_digest(frames)
    entry = {
        "algorithm": ALGORITHM,
        "digest_version": DIGEST_VERSION,
        "digest": "".join(f"{v:08x}" for v in values),
        "digest_minutes": len(values),
    }
    if decode_errors:
        # fpcalc couldn't read it alone; ffmpeg decoded it, skipping damaged frames
        entry["decode_errors"] = decode_errors
    return entry


def similarity(hex_a, hex_b):
    a = [int(hex_a[i:i + 8], 16) for i in range(0, len(hex_a) - 7, 8)]
    b = [int(hex_b[i:i + 8], 16) for i in range(0, len(hex_b) - 7, 8)]
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    diff = sum(bin(a[i] ^ b[i]).count("1") for i in range(n))
    return 100.0 - 100.0 * diff / (32 * n)
