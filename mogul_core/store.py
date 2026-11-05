"""Read and write JSON files safely."""

import datetime
import json
import os
import tempfile


def now():
    """Current local time, ISO 8601 with UTC offset, to the second."""
    return datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()


def utc_from_timestamp(ts):
    return (datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
            .replace(microsecond=0).isoformat().replace("+00:00", "Z"))


def load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save(path, data):
    """Write to a temp file in the same folder, then rename over the
    old one, so a crash mid-write can never leave a half-written file."""
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
