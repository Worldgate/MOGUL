"""File hashing.

xxh64 is the MOGUL file ID. It is taken over the audio packets only
(ffmpeg copies the audio stream out with no container, tags or
header frames), so it stays the same when tags are added, changed or
stripped, and when the file is renamed. A real re-encode gives a
different ID.

The other hashes are taken over the whole file, exactly as it sits
on disk.
"""

import hashlib
import re
import subprocess

try:
    import xxhash
except ImportError:  # pragma: no cover
    xxhash = None

try:
    import blake3 as _blake3
except ImportError:  # pragma: no cover
    _blake3 = None

CHUNK = 8 * 1024 * 1024
SUPPORTED = ("blake3", "sha256", "sha512", "sha1", "md5")


class HashError(Exception):
    pass


def check_available(names):
    """Return a list of problems with the requested hash setup."""
    problems = []
    if xxhash is None:
        problems.append("Python package 'xxhash' is missing (pip install xxhash)")
    for name in names:
        if name not in SUPPORTED:
            problems.append(f"unknown hash '{name}' in mogul.toml (choices: {', '.join(SUPPORTED)})")
        elif name == "blake3" and _blake3 is None:
            problems.append("Python package 'blake3' is missing (pip install blake3)")
    return problems


def _new(name):
    if name == "blake3":
        return _blake3.blake3()
    return hashlib.new(name)


def audio_xxh64(path):
    """xxh64 of the first audio stream's packets, as 16 lowercase hex chars."""
    cmd = [
        "ffmpeg", "-nostdin", "-v", "error",
        "-i", path,
        "-map", "0:a:0", "-c", "copy", "-f", "data", "-",
    ]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        raise HashError("ffmpeg not found (sudo apt install ffmpeg)")
    h = xxhash.xxh64()
    total = 0
    while True:
        chunk = proc.stdout.read(CHUNK)
        if not chunk:
            break
        total += len(chunk)
        h.update(chunk)
    err = proc.stderr.read().decode(errors="replace").strip()
    proc.wait()
    if proc.returncode != 0:
        first = err.splitlines()[0] if err else "unknown error"
        first = re.sub(r"^\[[^\]]*\]\s*", "", first)  # drop "[mp3 @ 0x...]" prefix
        raise HashError(f"ffmpeg could not read the audio ({first})")
    if total == 0:
        raise HashError("no audio data found")
    return h.hexdigest()


def whole_file_xxh64(path):
    """xxh64 of the whole file. Used to check copies, not as an ID."""
    h = xxhash.xxh64()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def file_hashes(path, names):
    """Whole-file hashes in one read pass. Returns {name: hex}."""
    hashers = {name: _new(name) for name in names}
    if not hashers:
        return {}
    with open(path, "rb") as f:
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                break
            for h in hashers.values():
                h.update(chunk)
    return {name: h.hexdigest() for name, h in hashers.items()}
