"""Talking to Radarr's API (v3 paths; Radarr v4/v5).

Key: secrets.toml [radarr] api_key, or the RADARR_API_KEY environment
variable (Radarr: Settings > General > API Key). Address:
[movie] radarr_url (set it in mogul.local.toml).
"""

import json
import os
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request


class RadarrError(Exception):
    pass


def radarr_key(config):
    key = os.environ.get("RADARR_API_KEY")
    path = os.path.join(config.base, "secrets.toml")
    if not key and os.path.isfile(path):
        with open(path, "rb") as f:
            key = tomllib.load(f).get("radarr", {}).get("api_key") or None
    if not key:
        raise SystemExit(
            "No Radarr API key found.\n"
            f"Add it to {path}:\n\n"
            "  [radarr]\n"
            '  api_key = "your key here"\n\n'
            "(Radarr: Settings > General > API Key)\n"
            "or set the RADARR_API_KEY environment variable.")
    return key


class Radarr:
    def __init__(self, url, key, timeout=60):
        self.url = url.rstrip("/")
        self.key = key
        self.timeout = timeout

    def call(self, method, path, params=None, body=None):
        address = f"{self.url}/api/v3/{path.lstrip('/')}"
        if params:
            address += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"X-Api-Key": self.key, "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        for attempt in range(3):
            try:
                req = urllib.request.Request(address, data=data, headers=headers, method=method)
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                    return json.loads(raw) if raw else None
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:500]
                if e.code == 401:
                    raise SystemExit("Radarr refused the API key (401). Check secrets.toml [radarr].")
                if e.code >= 500 and attempt < 2:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise RadarrError(f"HTTP {e.code}: {detail}")
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                if attempt < 2:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise SystemExit(f"Can't reach Radarr at {self.url}: {getattr(e, 'reason', e)}\n"
                                 "Check [movie] radarr_url (mogul.local.toml).")

    def get(self, path, params=None):
        return self.call("GET", path, params)

    def post(self, path, body):
        return self.call("POST", path, body=body)

    def command(self, name, wait=600):
        """Run a Radarr command (e.g. "Backup") and wait for it to finish."""
        cmd = self.post("command", {"name": name})
        deadline = time.time() + wait
        while time.time() < deadline:
            state = self.get(f"command/{cmd['id']}")
            if state.get("status") in ("completed", "failed", "aborted", "cancelled", "orphaned"):
                return state
            time.sleep(2)
        raise RadarrError(f"Radarr's {name} didn't finish within {wait} s")

    def download(self, path, dest):
        """Download a file Radarr serves (like a backup zip) to dest."""
        address = f"{self.url}/{path.lstrip('/')}"
        address += ("&" if "?" in address else "?") + urllib.parse.urlencode({"apikey": self.key})
        req = urllib.request.Request(address, headers={"X-Api-Key": self.key})
        tmp = dest + ".part"
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp, open(tmp, "wb") as f:
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
            os.replace(tmp, dest)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def movie_ids(self):
        return {m.get("tmdbId") for m in self.get("movie") or []}

    def exclusion_ids(self):
        for path in ("exclusions", "importlistexclusion"):
            try:
                data = self.get(path)
            except RadarrError:
                continue
            if isinstance(data, dict):
                data = data.get("records", [])
            return {x.get("tmdbId") for x in data or []}
        print("  (couldn't read Radarr's exclusion list; going on without it)")
        return set()

    def quality_profile_id(self, wanted):
        profiles = self.get("qualityprofile") or []
        for p in profiles:
            if str(p.get("id")) == str(wanted) or p.get("name", "").lower() == str(wanted).lower():
                return p["id"]
        names = ", ".join(f"'{p.get('name')}'" for p in profiles)
        raise SystemExit(f"Radarr has no quality profile '{wanted}'. "
                         f"Set [movie] radarr_quality_profile (or --profile) to one of: {names}")

    def root_folder(self, path):
        roots = [r.get("path", "").rstrip("/") for r in self.get("rootfolder") or []]
        if path.rstrip("/") not in roots:
            raise SystemExit(f"'{path}' isn't a root folder in Radarr.\n"
                             "Add it in Radarr: Settings > Media Management > Root Folders.\n"
                             f"Radarr's root folders: {', '.join(roots) or '(none)'}")
        return path.rstrip("/")

    def tag_ids(self, labels):
        have = {t["label"].lower(): t["id"] for t in self.get("tag") or []}
        for label in labels:
            if label not in have:
                have[label] = self.post("tag", {"label": label})["id"]
        return {label: have[label] for label in labels}

    def lookup(self, tmdb_id):
        data = self.get("movie/lookup/tmdb", {"tmdbId": tmdb_id})
        if isinstance(data, list):
            data = data[0] if data else None
        return data




def connect(config):
    """A Radarr client from the settings."""
    mcfg = config.section("movie")
    return Radarr(mcfg.get("radarr_url", "http://localhost:7878"), radarr_key(config))
