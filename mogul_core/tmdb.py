"""Talk to The Movie Database (TMDB) API.

Shared by every module that needs TMDB (movies now, TV later).

The API key is never stored in the repo. It's read from, in order:
  1. the TMDB_API_KEY environment variable
  2. secrets.toml next to mogul.toml:   [tmdb]  api_key = "..."
A v4 "read access token" also works (TMDB_READ_TOKEN / read_token =),
sent as a Bearer header instead of in the address.

Polite by default: a shared rate limit across all threads, retries on
"slow down" (429) and server hiccups (5xx, timeouts).
"""

import json
import os
import random
import threading
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.themoviedb.org/3"


class TMDBError(Exception):
    """A request failed. .status is the HTTP status (None = network)."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class NotFound(TMDBError):
    pass


class BadKey(TMDBError):
    pass


def load_credentials(config):
    """Return ("api_key", value) or ("token", value), or exit with help."""
    key = os.environ.get("TMDB_API_KEY")
    token = os.environ.get("TMDB_READ_TOKEN")
    path = os.path.join(config.base, "secrets.toml")
    if not (key or token) and os.path.isfile(path):
        with open(path, "rb") as f:
            section = tomllib.load(f).get("tmdb", {})
        key = section.get("api_key") or None
        token = section.get("read_token") or None
    if token:
        return ("token", token)
    if key:
        return ("api_key", key)
    raise SystemExit(
        "No TMDB API key found.\n"
        f"Make {path} with:\n\n"
        "  [tmdb]\n"
        '  api_key = "your key here"\n\n'
        "(copy secrets.toml.example; secrets.toml never goes to GitHub)\n"
        "or set the TMDB_API_KEY environment variable.")


class RateLimiter:
    """At most `per_second` requests per second, across all threads."""

    def __init__(self, per_second):
        self.gap = 1.0 / per_second if per_second > 0 else 0
        self.lock = threading.Lock()
        self.next = 0.0

    def wait(self):
        if not self.gap:
            return
        with self.lock:
            now = time.monotonic()
            start = max(now, self.next)
            self.next = start + self.gap
        delay = start - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    def pause(self, seconds):
        """TMDB said slow down: hold everyone back for a while."""
        with self.lock:
            self.next = max(self.next, time.monotonic() + seconds)


class Client:
    def __init__(self, config, per_second=20, retries=4, timeout=30):
        self.kind, self.secret = load_credentials(config)
        self.limiter = RateLimiter(per_second)
        self.retries = retries
        self.timeout = timeout

    def url(self, path, params=None, with_key=True):
        """Full request address. with_key=False gives a safe one for
        logs and saved files (no key in it)."""
        query = dict(params or {})
        if with_key and self.kind == "api_key":
            query["api_key"] = self.secret
        qs = urllib.parse.urlencode(query, safe=",")
        return f"{API}/{path.lstrip('/')}" + (f"?{qs}" if qs else "")

    def get(self, path, params=None):
        """GET a path like 'movie/550' and return the decoded JSON."""
        headers = {"Accept": "application/json", "User-Agent": "MOGUL"}
        if self.kind == "token":
            headers["Authorization"] = f"Bearer {self.secret}"
        address = self.url(path, params)
        last = None
        for attempt in range(self.retries + 1):
            self.limiter.wait()
            try:
                req = urllib.request.Request(address, headers=headers)
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                status = e.code
                try:
                    detail = json.loads(e.read().decode("utf-8")).get("status_message", "")
                except Exception:
                    detail = ""
                if status == 404:
                    raise NotFound(detail or "not found on TMDB", 404)
                if status == 401:
                    raise BadKey(detail or "TMDB refused the API key", 401)
                if status == 429 or status >= 500:
                    wait = e.headers.get("Retry-After") if status == 429 else None
                    wait = float(wait) if wait and wait.replace(".", "", 1).isdigit() \
                        else 2 ** attempt + random.random()
                    self.limiter.pause(wait)
                    last = TMDBError(f"HTTP {status} {detail}".strip(), status)
                    continue
                raise TMDBError(f"HTTP {status} {detail}".strip(), status)
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                last = TMDBError(f"network: {getattr(e, 'reason', e)}")
                time.sleep(2 ** attempt + random.random())
                continue
        raise last or TMDBError("request failed")
