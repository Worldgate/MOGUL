"""Progress labels with a time-remaining estimate.

The estimate is based on file sizes, not file counts: a 4-hour show takes
much longer to hash and fingerprint than a 40-minute one. After each file,
the speed so far (bytes per second) is used to estimate the time for the
bytes still to go.
"""

import os
import time


def fmt_duration(seconds):
    """3725 -> '1h02m', 95 -> '1m35s', 7 -> '7s'."""
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, s = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{s:02d}s"
    hours, m = divmod(minutes, 60)
    return f"{hours}h{m:02d}m"


class Progress:
    def __init__(self, files):
        self.count = len(files)
        self.width = len(str(max(self.count, 1)))
        self.sizes = []
        for path in files:
            try:
                self.sizes.append(os.path.getsize(path))
            except OSError:
                self.sizes.append(0)
        self.total = sum(self.sizes) or 1
        self.before = [0]  # bytes in all files before file i
        for size in self.sizes:
            self.before.append(self.before[-1] + size)
        self.start = time.time()

    def tag(self, i):
        """Label for file number i (1-based), e.g. '[ 53/194  ~12m05s left]'.
        For files handled in order: everything before file i is done."""
        return self.label(i, self.before[i - 1])

    def label(self, number, done_bytes):
        """Label for when 'done_bytes' bytes are finished. Used directly when
        files finish out of order (parallel work)."""
        elapsed = time.time() - self.start
        if done_bytes > 0 and elapsed >= 1:
            remaining = max(self.total - done_bytes, 0) * elapsed / done_bytes
            eta = f"~{fmt_duration(remaining)} left"
        else:
            eta = "estimating..."
        return f"[{number:>{self.width}}/{self.count}  {eta:>15}]"

    def elapsed(self):
        return fmt_duration(time.time() - self.start)
