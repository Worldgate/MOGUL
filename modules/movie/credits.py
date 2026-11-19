"""Credits OCR engine for `mogul.py movie verify`.

Adapted from Mavrick's tools/credits_id.py (kept there unchanged). Same
frame sampling, OCR, name matching, blind search, CBFC certificate and
sequel tie-breaking; settings now come from mogul.toml [movie] (set by
modules/movie/verify.py via configure()), and TMDB lookups go through
verify.py's TMDB adapter (saved TMDB files first, then the API).
"""

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

try:
    import cv2
except ImportError:
    sys.exit("movie verify needs OpenCV: pip install opencv-python-headless numpy pytesseract")

# ----------------------------------------------------------------- settings --
# Defaults; configure() overrides them from mogul.toml [movie].

RENAME = True
FOLDER_PER_MOVIE = True
NAME_FORMAT = "{title} ({year}) {tags}"
FOLDER_FORMAT = "{title} ({year}) {{tmdb-{tmdb_id}}}"
ARTICLES_LAST = True
DECADE_FOLDERS = True       # renamed movies go into <decade>/ like the library (1990-1999)


VIDEO_EXTS ={".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".ts", ".m2ts",
              ".mpg", ".mpeg", ".webm", ".vob", ".flv", ".divx", ".ogm"}

END_MINUTES = 12          # how much of the end to scan
START_MINUTES = 5         # how much of the opening to scan if the end fails
FRAME_EVERY = 2.0         # seconds between sampled frames
SCAN_WIDTH = 1280         # frames are scaled to this width before OCR

CONFIRM_SCORE = 0.30      # share of top-billed cast that must be found
CONFIRM_MIN_NAMES = 3     # and at least this many names
WEAK_MIN_NAMES = 2

TOP_BILLED = 15           # how many top cast names count toward the score
FUZZ = 0.86               # how close an OCR'd name must be to count



# --------------------------------------------------------------------- text --

CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af\uf900-\ufaff]")


def has_cjk(s):
    return bool(CJK_RE.search(s))


def norm(s):
    """Uppercase, strip accents and punctuation: 'Rade Šerbedžija' -> 'RADE SERBEDZIJA'.
    Japanese/Chinese/Korean characters are kept; spaces inside them are dropped,
    since credits write 三浦 建太郎 and TMDB writes 三浦建太郎."""
    s = unicodedata.normalize("NFKC", s)          # full-width → normal width
    s = unicodedata.normalize("NFKD", s)
    # drop accents on Latin letters only (keeps Japanese ガ from turning into カ)
    s = "".join(c for i, c in enumerate(s)
                if not (unicodedata.combining(c) and i and s[i - 1] < "\u0250"))
    s = unicodedata.normalize("NFKC", s)          # put kana back together
    s = s.upper().replace("’", "'").replace("'", "")
    s = "".join(c if c.isalnum() else " " for c in s)
    s = re.sub(r"\s+", " ", s).strip()
    # remove spaces between CJK characters
    s = re.sub(r"(?<=[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]) (?=[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af])", "", s)
    return s


def name_found(name, blob, lines):
    """Is this person's name in the OCR text? Exact first, then fuzzy."""
    n = norm(name)
    cjk = has_cjk(n)
    if len(n) < (3 if cjk else 4):
        return False
    if f" {n} " in blob:
        return True
    squashed = n.replace(" ", "")   # OCR sometimes drops spaces: MAYARUDOLPH
    if len(squashed) >= (3 if cjk else 8) and any(squashed in l.replace(" ", "") for l in lines):
        return True
    if cjk:
        return False                 # fuzzy matching on 3-4 characters is too loose
    words = n.split()
    k = len(words)
    for line in lines:
        toks = line.split()
        if len(toks) < k:
            continue
        for i in range(len(toks) - k + 1):
            chunk = " ".join(toks[i:i + k])
            if abs(len(chunk) - len(n)) > 4:
                continue
            if SequenceMatcher(None, chunk, n).ratio() >= FUZZ:
                return True
    return False


TMDB_ID_RE = re.compile(r"(?i)tmdb(?:id)?[-=_: ]?(\d{1,8})")
IMDB_ID_RE = re.compile(r"(?i)(?<![a-z0-9])(tt\d{7,9})(?!\d)")


def ids_from_path(path):
    """IDs tagged in the filename or its folder:
    'Bridegroom (2013) {tmdb-174366}', '[tmdbid-123]', '{imdb-tt0133093}'."""
    p = Path(path)
    for text in (p.stem, p.parent.name):
        t = TMDB_ID_RE.search(text)
        i = IMDB_ID_RE.search(text)
        if t or i:
            return (int(t.group(1)) if t else None), (i.group(1).lower() if i else None)
    return None, None


def guess_from_filename(path):
    """'Taken.2.2012.1080p.BluRay.mkv' -> ('Taken 2', 2012)."""
    stem = Path(path).stem if Path(path).suffix.lower() in VIDEO_EXTS else Path(path).name
    stem = re.sub(r"\{.*?\}", " ", stem)            # {tmdb-123} and friends
    stem = re.sub(r"[._]", " ", stem)
    m = re.search(r"^(.*?)[\s(\[]*((?:19|20)\d\d)(?:[\s)\]]|$)", stem)
    if m and m.group(1).strip():
        title, year = m.group(1), int(m.group(2))
    else:
        title, year = stem, None
    title = re.sub(r"\[.*?\]|\(.*?\)", " ", title)
    title = re.sub(r"\b(1080p|720p|2160p|4k|bluray|bdrip|brrip|webrip|web dl|"
                   r"hdtv|dvdrip|x264|x265|hevc|remux|uhd|hdr)\b.*$", " ",
                   title, flags=re.I)
    title = re.sub(r"\s+", " ", title).strip(" -")
    # library style "Matrix, The" -> "The Matrix" for searching
    m = re.match(r"(?i)^(.+), (the|a|an)$", title)
    if m:
        title = f"{m.group(2)} {m.group(1)}"
    return (title or None), year


# ------------------------------------------------------------------- frames --

def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height:format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True)
    info = json.loads(out.stdout or "{}")
    st = (info.get("streams") or [{}])[0]
    dur = float((info.get("format") or {}).get("duration") or 0)
    return dur, st.get("width") or 1920, st.get("height") or 1080


def grab_frames(path, start, length, w, h):
    """Yield (timestamp, BGR frame) every FRAME_EVERY seconds in one ffmpeg pass."""
    ow = SCAN_WIDTH
    oh = int(round(h * ow / w / 2) * 2) or 720
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{start:.2f}", "-i", str(path),
           "-t", f"{length:.2f}", "-an", "-sn",
           "-vf", f"fps=1/{FRAME_EVERY},scale={ow}:{oh}",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size = ow * oh * 3
    i = 0
    try:
        while True:
            buf = p.stdout.read(size)
            if len(buf) < size:
                break
            yield start + i * FRAME_EVERY, np.frombuffer(buf, np.uint8).reshape(oh, ow, 3)
            i += 1
    finally:
        p.stdout.close()
        p.wait()


def text_channel(bgr):
    """Brightest of B/G/R per pixel, so red or blue credits read as bright,
    stretched so dim text becomes full white."""
    v = bgr.max(axis=2)
    hi = np.percentile(v, 99.7)
    if hi > 40:
        v = np.clip(v.astype(np.float32) * (255.0 / hi), 0, 255).astype(np.uint8)
    return v


def looks_like_credits(gray):
    """Dark background with a modest amount of bright text."""
    mean = gray.mean()
    bright = (gray > max(110, mean + 60)).mean()
    if mean > 90 or not (0.002 < bright < 0.20):
        return False
    # text makes lots of short horizontal edges; a dark film scene mostly doesn't
    edges = cv2.Canny(gray, 80, 200)
    return edges.mean() > 1.5


def white_channel(bgr):
    """(MOGUL) For white text over bright, colorful video: white is bright in
    all three channels, a colored picture usually isn't, so the darkest of
    B/G/R per pixel keeps the text and drops most of the background. The
    result is flipped and stretched to look like normal light-on-dark credits."""
    v = bgr.min(axis=2).astype(np.float32)
    # whatever isn't near-white goes dark; near-white stays bright
    v = np.clip((v - 170) * (255.0 / 85), 0, 255).astype(np.uint8)
    return v


def looks_like_bright_credits(white):
    """(MOGUL) After white_channel: a modest amount of white text shapes."""
    bright = (white > 128).mean()
    if not (0.002 < bright < 0.15):
        return False
    return cv2.Canny(white, 80, 200).mean() > 1.0


def pick_credit_frames(path, start, length, w, h, bright=False):
    """bright=False: classic credits (dark background).
    bright=True (MOGUL second pass): white text over bright/colored video."""
    frames, last = [], None
    for ts, f in grab_frames(path, start, length, w, h):
        if bright:
            g = white_channel(f)
            if not looks_like_bright_credits(g):
                continue
        else:
            g = text_channel(f)
            if not looks_like_credits(g):
                continue
        small = cv2.resize(g, (160, 90))
        if last is not None and np.abs(small.astype(int) - last).mean() < 2.0:
            continue  # same card as the last one we kept
        last = small.astype(int)
        frames.append((ts, g))
    return frames


# ---------------------------------------------------------------------- OCR --

class OCR:
    TESS = {"en": "eng", "ja": "jpn", "ch_sim": "chi_sim", "ch_tra": "chi_tra",
            "ko": "kor", "fr": "fra", "de": "deu", "es": "spa", "it": "ita",
            "ru": "rus"}

    def __init__(self, engine="auto", langs=("en",)):
        self.reader = None
        self.langs = list(langs)
        if engine in ("auto", "easyocr"):
            try:
                import easyocr
                self.reader = easyocr.Reader(self.langs, gpu=True, verbose=False)
                self.name = "easyocr"
                return
            except Exception:
                if engine == "easyocr":
                    raise
        import pytesseract
        self.pt = pytesseract
        self.tess_lang = "+".join(self.TESS.get(l, l) for l in self.langs)
        self.name = "tesseract"

    def read(self, gray):
        """Return normalized text lines from one frame."""
        return [n for n in (norm(r) for r in self.read_raw(gray)) if len(n) >= 3]

    def read_raw(self, gray, light_text=True):
        """Return the text lines from one frame as read, punctuation intact.
        light_text=False for dark text on a light background (certificates)."""
        if self.reader:
            # paragraph=False keeps role / actor columns as separate pieces
            res = self.reader.readtext(gray, detail=1, paragraph=False)
            rows = defaultdict(list)
            for box, text, conf in res:
                if conf < 0.25:
                    continue
                y = int((box[0][1] + box[2][1]) / 2 // 12)
                rows[y].append((box[0][0], text))
            raw = []
            for y in sorted(rows):
                parts = [t for _, t in sorted(rows[y])]
                raw.extend(parts)          # each column alone
                if len(parts) > 1:
                    raw.append(" ".join(parts))
        else:
            if not light_text:
                # dark text on a light page (certificates): plain upscale reads best
                img = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
                txt = self.pt.image_to_string(img, lang=self.tess_lang, config="--psm 6")
                return [l for l in txt.splitlines() if l.strip()]
            img = cv2.bitwise_not(gray)    # tesseract prefers dark text on white
            if img.shape[0] < 900:
                img = cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
            img = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
            d = self.pt.image_to_data(img, lang=self.tess_lang, config="--psm 6",
                                      output_type=self.pt.Output.DICT)
            rows = defaultdict(list)
            for i, word in enumerate(d["text"]):
                if word.strip() and float(d["conf"][i]) > 30:
                    key = (d["block_num"][i], d["par_num"][i], d["line_num"][i])
                    rows[key].append((d["left"][i], d["left"][i] + d["width"][i],
                                      d["height"][i], word))
            raw = []
            for key in sorted(rows):
                words = sorted(rows[key])
                raw.append(" ".join(w[3] for w in words))
                # split two-column rows ("Kim      MAGGIE GRACE") on wide gaps
                gap_limit = 1.5 * (np.median([w[2] for w in words]) or 20)
                parts, cur = [], [words[0]]
                for prev, w in zip(words, words[1:]):
                    if w[0] - prev[1] > gap_limit:
                        parts.append(cur)
                        cur = []
                    cur.append(w)
                parts.append(cur)
                if len(parts) > 1:
                    raw.extend(" ".join(w[3] for w in p) for p in parts)
        return raw


# ----------------------------------------------------------------- matching --

CREW_JOBS = {"Director", "Screenplay", "Writer", "Director of Photography",
             "Original Music Composer", "Editor", "Producer", "Novel",
             "Original Story", "Comic Book", "Characters", "Author", "Story"}


def cast_people(movie):
    """(top-billed cast, deeper cast, key crew) as lists of (name, tmdb_person_id)."""
    credits = (movie or {}).get("credits") or {}
    cast = sorted(credits.get("cast", []), key=lambda c: c.get("order", 999))
    top = [(c["name"], c.get("id")) for c in cast[:TOP_BILLED]]
    rest = [(c["name"], c.get("id")) for c in cast[TOP_BILLED:60]]
    seen, crew = set(), []
    for c in credits.get("crew", []):
        if c.get("job") in CREW_JOBS and c["name"] not in seen:
            seen.add(c["name"])
            crew.append((c["name"], c.get("id")))
    return top, rest, crew


class Hits(list):
    """Matched names, plus how many of them were cast (not crew)."""
    cast_count = 0


def score_movie(movie, blob, lines, tmdb=None):
    top, rest, crew = cast_people(movie)
    if not top and not crew:
        return 0.0, []
    # Credits in Japanese, Korean, etc.: also check each person's native-script
    # name from TMDB (also_known_as). Only top cast + key crew, to save calls.
    foreign = tmdb is not None and has_cjk(blob)

    def found(person, look_up_alias):
        name, pid = person
        if name_found(name, blob, lines):
            return True
        if look_up_alias and pid:
            return any(has_cjk(a) and name_found(a, blob, lines)
                       for a in tmdb.person_aliases(pid))
        return False

    hit_top = [p[0] for p in top if found(p, foreign)]
    hit_rest = [p[0] for p in rest if found(p, False)]
    hit_crew = [p[0] for p in crew if found(p, foreign)]
    hits = Hits(hit_top + hit_rest + hit_crew)
    hits.cast_count = len(hit_top) + len(hit_rest)
    # Cast score: driven by top-billed cast; deeper cast and key crew add a little.
    cast_score = 0.0
    if top:
        cast_score = len(hit_top) / min(len(top), 10)
        cast_score += 0.02 * len(hit_rest) + 0.03 * len(hit_crew)
    # Crew score: for documentaries, whose credits list the filmmakers, not a cast.
    # Scored the same way for every movie so a doc can't win just because TMDB
    # happens to list no cast for it.
    crew_score = len(hit_crew) / min(len(crew), 6) if crew else 0.0
    return round(min(max(cast_score, crew_score), 1.0), 3), hits


def describe(movie):
    if not movie:
        return None
    rd = movie.get("release_date") or ""
    return {"tmdb_id": movie["id"], "title": movie.get("title"),
            "original_title": movie.get("original_title"),
            "year": int(rd[:4]) if rd[:4].isdigit() else None,
            "runtime": movie.get("runtime")}


def person_like(line):
    """Could this OCR line be a person's name? Used only for the blind search."""
    if has_cjk(line):
        sq = line.replace(" ", "")
        return 2 <= len(sq) <= 8 and not any(ch.isdigit() for ch in sq)
    w = line.split()
    if not 2 <= len(w) <= 4 or any(ch.isdigit() for ch in line):
        return False
    junk = {"THE", "AND", "OF", "BY", "FOR", "IN", "WITH", "CAST", "PRODUCED",
            "DIRECTED", "WRITTEN", "MUSIC", "DIRECTOR", "PRODUCER", "ASSISTANT",
            "MANAGER", "COORDINATOR", "SUPERVISOR", "OPERATOR", "COMPANY", "LLC",
            "LTD", "INC", "CAMERA", "SOUND", "EDITOR", "ART", "DEPARTMENT"}
    return not (set(w) & junk) and all(len(x) >= 2 for x in w)


def blind_search(tmdb, lines, blob, max_people=25):
    """No usable filename: look up names on TMDB and find the movie they share."""
    counts = Counter(l for l in lines if person_like(l))
    people = [n for n, _ in counts.most_common(max_people)]
    votes = Counter()
    for name in people:
        hits = tmdb.search_person(name)
        if not hits:
            continue
        # English names must match exactly; native-script names match TMDB's
        # alternate names, so trust the top hit
        if not has_cjk(name) and norm(hits[0]["name"]) != name:
            continue
        for mid in tmdb.person_movies(hits[0]["id"]):
            votes[mid] += 1
    found = []
    for mid, v in votes.most_common(5):
        if v < 2:
            break
        m = tmdb.movie(mid)
        if m:
            found.append(m)
    return found


# --------------------------------------------------------------------- main --

def judge(score, hits):
    if score >= CONFIRM_SCORE and len(hits) >= CONFIRM_MIN_NAMES:
        return "strong"
    if len(hits) >= WEAK_MIN_NAMES:
        return "weak"
    return "none"


# ------------------------------------------------- CBFC certificate (India) --
# Indian theatrical releases open with a Central Board of Film Certification
# certificate that states the exact title, language and running time, e.g.
#   Film: CHAPEKAR BROTHERS (HINDI)(Colour)   ...   Duration 98:18 min:sec

CERT_SECONDS = 120          # how far into the movie to look for it
CERT_EVERY = 3.0            # seconds between sampled frames
INDIAN_LANGS = ("HINDI|TAMIL|TELUGU|MALAYALAM|KANNADA|MARATHI|BENGALI|GUJARATI|"
                "PUNJABI|ODIA|ORIYA|ASSAMESE|BHOJPURI|URDU|KONKANI|TULU|"
                "ENGLISH|SANSKRIT|RAJASTHANI|HARYANVI|CHHATTISGARHI|NEPALI")


def parse_certificate(text):
    """Pull title / language / duration / year out of certificate OCR text."""
    t = re.sub(r"[ \t]+", " ", text)
    if not re.search(r"(?i)film\s*certification|cbfc|censor", t):
        return None
    cert = {}
    m = (re.search(rf"(?i)\bfilm\s*[:;.]\s*(.+?)\s*[\(\[]\s*({INDIAN_LANGS})\b", t)
         or re.search(rf"(?i)\bfilm\s+(?!certification)(.+?)\s*[\(\[]\s*({INDIAN_LANGS})\b", t))
    if m:
        title = re.sub(r"\s+", " ", m.group(1)).strip(" :;.-'\"")
        if 2 <= len(title) <= 80:
            cert["title"] = title.title()
            cert["language"] = m.group(2).title()
    # OCR mixes up look-alike digits (9B:18 is 98:18)
    fix = str.maketrans("OoBSIlZ", "0085112")
    m = re.search(r"(?i)duration\D{0,15}?([0-9OoBSIlZ]{1,3})\s*[:.]\s*([0-9OoBSIlZ]{2})(?![0-9])", t)
    if m:
        cert["seconds"] = (int(m.group(1).translate(fix)) * 60
                           + int(m.group(2).translate(fix)))
    else:
        m = re.search(r"(?i)duration\D{0,15}?(\d)\s*h\w*\.?\s*(\d{1,2})\s*m", t)
        if m:
            cert["seconds"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60
    m = re.search(r"(?i)dated?\D{0,6}(\d{1,2})\s*[/.-]\s*(\d{1,2})\s*[/.-]\s*((?:19|20)\d\d)", t)
    if m:
        cert["year"] = int(m.group(3))
    return cert if cert.get("title") else None


def read_certificate(path, ocr, w, h, dur):
    """Look through the opening of the movie for a CBFC certificate."""
    for ts, f in grab_frames(path, 0, min(CERT_SECONDS, dur * 0.1), w, h):
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        if g.mean() < 25 or cv2.Canny(g, 80, 200).mean() < 6:
            continue                      # black or plain frame, not a document
        text = "\n".join(ocr.read_raw(g, light_text=g.mean() < 100))
        cert = parse_certificate(text)
        if cert:
            cert["at_seconds"] = round(ts)
            return cert
    return None


def match_certificate(tmdb, cert):
    """Find the TMDB movie the certificate describes: same title, same length."""
    want = norm(cert["title"])
    found = []
    for r in tmdb.search_movie(cert["title"], cert.get("year"))[:8]:
        m = tmdb.movie(r["id"])
        if not m:
            continue
        names = {norm(m.get("title") or ""), norm(m.get("original_title") or "")}
        if max(SequenceMatcher(None, want, n).ratio() for n in names) < 0.85:
            continue
        rt = m.get("runtime")
        if rt and cert.get("seconds") and abs(rt - cert["seconds"] / 60) > 4:
            continue                      # same title, different length -> other film
        yr = (m.get("release_date") or "")[:4]
        gap = abs(int(yr) - cert["year"]) if yr.isdigit() and cert.get("year") else 0
        found.append((gap, m))
    found.sort(key=lambda x: x[0])
    return found[0][1] if found else None


def read_section(path, ocr, start, length, w, h, bright=False):
    frames = pick_credit_frames(path, start, length, w, h, bright)
    lines = []
    for _, g in frames:
        lines.extend(ocr.read(g))
    return len(frames), lines


def identify(path, title=None, year=None, tmdb_id=None, tmdb=None, ocr=None,
             image=False):
    """Identify one file. Returns a dict (see module docstring for 'status')."""
    if tmdb is None:
        raise ValueError("identify() needs a TMDB adapter")
    ocr = ocr or OCR()
    path = Path(path)
    out = {"file": str(path), "ocr": ocr.name}

    if image:
        bgr = cv2.imread(str(path))
        g = text_channel(bgr) if bgr is not None else None
        if g is None:
            return {**out, "status": "unknown", "error": "could not read image"}
        lines, nframes = ocr.read(g), 1
        out["duration_min"] = None
    else:
        dur, w, h = probe(path)
        if dur <= 0:
            return {**out, "status": "unknown", "error": "ffprobe found no duration"}
        out["duration_min"] = round(dur / 60, 1)
        end_len = min(END_MINUTES * 60, dur * 0.25)
        nframes, lines = read_section(path, ocr, dur - end_len, end_len, w, h)
        out["searched"] = "end"

    blob = " " + " ".join(lines) + " "

    # 1. the guess: explicit ID, explicit title, or the filename
    #    An ID tagged on the file or its folder ({tmdb-174366}, {imdb-tt...}) goes
    #    first, but it's still only a hint: the credits have to agree with it.
    candidates = []
    if tmdb_id:
        candidates = [tmdb.movie(tmdb_id)]
    else:
        tag_tmdb, tag_imdb = (None, None) if image else ids_from_path(path)
        if not tag_tmdb and tag_imdb and hasattr(tmdb, "find_imdb"):
            tag_tmdb = tmdb.find_imdb(tag_imdb)
        if tag_tmdb:
            candidates.append(tmdb.movie(tag_tmdb))
            out["tagged_id"] = tag_tmdb
        if not title:
            title, fyear = guess_from_filename(path)
            year = year or fyear
            # junk filename inside a properly named folder -> use the folder
            ftitle, fy = guess_from_filename(path.parent) if not image else (None, None)
            if ftitle and (not title or (fy and not fyear)):
                title, year = ftitle, year or fy
        # MOGUL: with an ID tag, check that first; the title search only
        # happens below if the tagged movie doesn't match (saves requests)
        if title and not tag_tmdb:
            seen = {m["id"] for m in candidates if m}
            candidates += [tmdb.movie(r["id"]) for r in tmdb.search_movie(title, year)[:5]
                           if r["id"] not in seen]
    # The filename search can return several movies with the same title
    # (Belle 2013, Belle 2021, a Belle short...). All of them count as "what the
    # filename says"; the credits pick which one it actually is.
    candidates = [m for m in candidates if m]
    cand_ids = {m["id"] for m in candidates}
    guess = candidates[0] if candidates else None
    out["filename_guess"] = describe(guess)

    scored = {}   # tmdb id -> (movie, score, names)

    def consider(movies):
        for m in movies:
            if m:
                sc, h = score_movie(m, blob, lines, tmdb)
                scored[m["id"]] = (m, sc, h)

    def ranked():
        return sorted(scored.values(), key=lambda x: -x[1])

    def top():
        r = ranked()
        return r[0] if r else (None, 0.0, [])

    consider(candidates)
    best, score, hits = top()

    # 1b. (MOGUL) the tagged ID didn't match: try the filename title too
    if judge(score, hits) != "strong" and title and out.get("tagged_id"):
        seen = {m["id"] for m in candidates}
        extra = [tmdb.movie(r["id"]) for r in tmdb.search_movie(title, year)[:5]
                 if r["id"] not in seen]
        extra = [m for m in extra if m]
        candidates += extra
        cand_ids |= {m["id"] for m in extra}
        consider(extra)
        best, score, hits = top()

    # 2. nothing convincing at the end -> try the opening credits too
    if judge(score, hits) != "strong" and not image:
        n2, lines2 = read_section(path, ocr, 0, min(START_MINUTES * 60, dur * 0.1), w, h)
        if lines2:
            nframes += n2
            lines += lines2
            blob = " " + " ".join(lines) + " "
            consider(candidates)
            best, score, hits = top()
            out["searched"] = "end+start"

    # 2b. (MOGUL) still nothing: credits may be white text over bright video,
    #     which the dark-background check throws away. Read the end again
    #     looking for white text only.
    if judge(score, hits) != "strong" and not image:
        end_len = min(END_MINUTES * 60, dur * 0.25)
        n3, lines3 = read_section(path, ocr, dur - end_len, end_len, w, h, bright=True)
        if lines3:
            nframes += n3
            lines += lines3
            blob = " " + " ".join(lines) + " "
            consider(candidates)
            best, score, hits = top()
            out["searched"] = out.get("searched", "end") + "+bright"

    # 3. still nothing -> blind search from the names themselves
    if judge(score, hits) != "strong" and lines:
        before = best["id"] if best else None
        consider(blind_search(tmdb, lines, blob))
        best, score, hits = top()
        if best and best["id"] != before:
            out["found_by"] = "blind search"

    # 3b. Indian release? The CBFC certificate at the start names the film and
    #     its exact length. Read straight from the file, so it can overrule
    #     the filename just like the credits can.
    certified = False
    if judge(score, hits) != "strong" and not image:
        cert = read_certificate(path, ocr, w, h, dur)
        if cert:
            out["certificate"] = cert
            m = match_certificate(tmdb, cert)
            if m:
                best, score = m, 1.0
                label = f"CBFC certificate: {cert['title']}"
                if cert.get("language"):
                    label += f" ({cert['language']})"
                if cert.get("seconds"):
                    label += f", {cert['seconds'] // 60}:{cert['seconds'] % 60:02d}"
                hits = Hits([label])
                hits.cast_count = 99
                scored = {m["id"]: (m, score, hits)}
                certified = True
                out["found_by"] = "CBFC certificate"
                if cert.get("seconds") and out.get("duration_min"):
                    diff = out["duration_min"] - cert["seconds"] / 60
                    if abs(diff) > 2:
                        out["note"] = (f"file is {diff:+.1f} min vs. the certified "
                                       f"length; may be a different cut")
            else:
                out["note"] = (f"CBFC certificate says \"{cert['title']}\" but no "
                               f"TMDB movie matched its title and length")

    # 4. sequels / multi-part films share a cast (Berserk I, II, III).
    #    If two movies score about the same, let the runtime decide.
    tied = [x for x in ranked() if x[1] >= score - 0.10 and judge(x[1], x[2]) == "strong"]
    ambiguous = False
    named = [x for x in tied if x[0]["id"] in cand_ids]
    if len(tied) > 1 and named:
        # The credits fit several movies and one of them is what the filename
        # says. The filename only picks among movies the credits already back.
        others = ", ".join(_fmt(describe(x[0])) for x in tied if x not in named[:1])
        best, score, hits = named[0]
        out["note"] = f"credits also fit {others}; went with the filename"
        tied = [named[0]]
    if len(tied) > 1:
        names = ", ".join(_fmt(describe(x[0])) for x in tied[:4])
        dmin = out.get("duration_min")
        timed = [(abs(dmin - x[0]["runtime"]), x) for x in tied
                 if dmin and x[0].get("runtime")]
        timed.sort(key=lambda t: t[0])
        if len(timed) >= 2 and timed[1][0] - timed[0][0] >= 4:
            best, score, hits = timed[0][1]
            out["note"] = f"credits fit several movies ({names}); picked by runtime"
        else:
            ambiguous = True
            out["note"] = f"credits fit several movies ({names}); can't tell which"

    out["credit_frames"] = nframes
    out["match"] = describe(best)
    out["score"] = score
    out["matched_names"] = hits[:20]

    strength = "strong" if certified else judge(score, hits)
    crew_only = getattr(hits, "cast_count", len(hits)) < CONFIRM_MIN_NAMES
    if strength == "strong" and crew_only and best and best["id"] not in cand_ids:
        # Directors, producers and editors work on many films together, so crew
        # names alone can't overrule the filename.
        strength = "weak"
        out["note"] = ("matched on crew only (director/producers/editors), who work "
                       "on several films; check by hand")
    if strength == "none":
        out["status"] = "unknown"
    elif strength == "weak" or ambiguous:
        out["status"] = "weak"
    elif best and best["id"] in cand_ids:
        out["status"] = "confirmed"
        out["filename_guess"] = describe(best)
        if year and best.get("release_date", "")[:4].isdigit() \
                and abs(int(best["release_date"][:4]) - year) > 1:
            out["note"] = f"filename says {year}, TMDB says {best['release_date'][:4]}"
    elif guess:
        out["status"] = "mismatch"
    else:
        out["status"] = "identified"

    rt = (best or {}).get("runtime")
    if rt and out.get("duration_min"):
        out["runtime_diff_min"] = round(out["duration_min"] - rt, 1)
    # (MOGUL) no usable credits: at least say whether the file's length fits
    # the movie the name claims, so a likely-right copy stands out
    if out["status"] == "unknown" and guess and guess.get("runtime") and out.get("duration_min"):
        diff = round(out["duration_min"] - guess["runtime"], 1)
        out["runtime_diff_min"] = diff
        fits = "matches" if abs(diff) <= 3 else "does NOT match"
        hint = (f"no usable credits; length {fits} {_fmt(describe(guess))} "
                f"({out['duration_min']} min vs TMDB {guess['runtime']} min)")
        out["note"] = f"{out['note']}; {hint}" if out.get("note") else hint
    return out


def iter_files(target, skip_dir=None):
    p = Path(target)
    if p.is_file():
        yield p
        return
    root = p.resolve()
    skips = [Path(d).resolve() for d in (skip_dir if isinstance(skip_dir, (list, tuple))
                                         else [skip_dir]) if d]
    # don't skip a folder we were asked to scan (e.g. --scan SORT_DIR/weak)
    skips = [d for d in skips if d != root and d not in root.parents]
    for f in sorted(p.rglob("*")):
        if any(d in f.resolve().parents for d in skips):
            continue          # output folder sitting inside the scan folder
        if f.suffix.lower() in VIDEO_EXTS and f.is_file():
            yield f


# --------------------------------------------------------------------- move --

def safe_name(text):
    """Make a title safe for a filename: 'Face/Off' -> 'Face-Off'."""
    text = re.sub(r'[\\/:*?"<>|]+', "-", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text or "Unknown"


QUALITY_RE = re.compile(
    r"(?i)(?<![a-z0-9])(2160p|1080p|1080i|720p|576p|480p|4k|uhd|blu-?ray|bdrip|brrip|"
    r"bdremux|remux|web-?dl|webrip|web|hdtv|dvdrip|dvd|hdrip|x26[45]|h\.?26[45]|"
    r"hevc|avc|xvid|divx|10bit|8bit|hdr10?\+?|dolby|atmos|dts|truehd|aac|ac3|"
    r"proper|repack|extended|unrated|remastered|criterion|imax)(?![a-z0-9])")


def release_tags(filename):
    """The release info from an old filename, untouched:
    'Doctor.Who.The.Day.of.the.Doctor.2013.Bluray-1080p.8bit.x264' -> 'Bluray-1080p.8bit.x264'"""
    stem = Path(filename).stem
    m = re.search(r"(?<!\d)(?:19|20)\d\d(?!\d)[)\]]?", stem[1:])  # year after the title
    if m:
        rest = stem[1 + m.end():]
    else:
        q = QUALITY_RE.search(stem)
        rest = stem[q.start():] if q else ""
    return rest.strip(" ._-[]()")


def new_stem_for(match, old_name="", fmt=None):
    fmt = fmt or NAME_FORMAT
    title = match.get("title") or "Unknown"
    title = re.sub(r"\s*:\s*", " - ", title)      # 'Doctor Who: The Day' -> 'Doctor Who - The Day'
    if ARTICLES_LAST:
        # only the leading article, and keep any subtitle after " - " where it is:
        # 'A Christmas Prince - The Royal Wedding' -> 'Christmas Prince - The Royal Wedding, A'
        m = re.match(r"(?i)^(the|a|an)\s+(.+)$", title)
        if m:
            title = f"{m.group(2)}, {m.group(1).capitalize()}"
    name = fmt.format(title=title, year=match.get("year") or "",
                      tmdb_id=match.get("tmdb_id", ""),
                      original_title=match.get("original_title") or "",
                      tags=release_tags(old_name) if old_name else "")
    name = re.sub(r"\s*\(\s*\)", "", name)          # no year -> drop "()"
    name = re.sub(r"\s*\[\s*\]", "", name)
    return safe_name(name)


def move_verified(f, scan_dir, verified_dir, match=None, dry_run=False):
    """Move a verified movie into verified_dir, renamed to what the credits say.

    If the movie sits in its own folder (the only video in it), everything in
    that folder comes along (subtitles, posters, .nfo, extras). Otherwise only
    the video and files sharing its name (Movie.srt, Movie.nfo, ...) move.
    Anything named after the old filename gets the new name.
    Raises FileExistsError instead of overwriting, so duplicates get flagged.
    Returns the new path of the video.
    """
    f = Path(f).resolve()
    scan_dir = Path(scan_dir).resolve()
    verified_dir = Path(verified_dir).resolve()
    parent = f.parent
    videos_here = [x for x in parent.iterdir()
                   if x.is_file() and x.suffix.lower() in VIDEO_EXTS]
    own_folder = parent != scan_dir and len(videos_here) == 1

    rename = RENAME and match and match.get("title")
    stem = new_stem_for(match, f.name) if rename else f.stem

    base = verified_dir
    if rename and DECADE_FOLDERS and match.get("year"):
        d = int(match["year"]) // 10 * 10
        base = verified_dir / f"{d}-{d + 9}"
    if rename and FOLDER_PER_MOVIE:
        dest_dir = base / new_stem_for(match, f.name, FOLDER_FORMAT)
    elif rename:
        dest_dir = base
    else:
        try:
            dest_dir = verified_dir / parent.relative_to(scan_dir)
        except ValueError:
            dest_dir = verified_dir

    def renamed(name):
        if name.startswith(f.stem + ".") or name == f.name:
            return stem + name[len(f.stem):]
        return name

    if own_folder:
        items = list(parent.iterdir())
    else:
        items = [x for x in parent.iterdir()
                 if x.is_file() and (x == f or x.name.startswith(f.stem + "."))]
    moves = [(x, dest_dir / renamed(x.name)) for x in items]
    if own_folder and dest_dir.resolve() == parent:
        return str(f)                       # already where it belongs
    moves = [(a, b) for a, b in moves if a.resolve() != b.resolve()]
    if not moves:
        return str(f)

    if dest_dir.is_dir() and any(x.suffix.lower() in VIDEO_EXTS for x in dest_dir.iterdir()
                                 if x.is_file()) and (rename and FOLDER_PER_MOVIE):
        raise FileExistsError(f"already have a copy in {dest_dir}")
    for src, dest in moves:
        if dest.exists():
            raise FileExistsError(f"already exists: {dest}")

    if not dry_run:
        dest_dir.mkdir(parents=True, exist_ok=True)
        for src, dest in moves:
            shutil.move(str(src), str(dest))   # instant rename on the same drive
        if own_folder:
            try:
                parent.rmdir()                  # leave no empty folder behind
            except OSError:
                pass
    return str(dest_dir / renamed(f.name))


# ------------------------------------------------------------------- report --

STATUS_ORDER = ["weak", "unknown", "mismatch", "identified", "confirmed"]
STATUS_NOTE = {
    "weak":       "A few names matched, or the credits fit more than one movie. Check by hand.",
    "unknown":    "No credits found or nothing matched. Check by hand.",
    "mismatch":   "The filename was wrong. Credits say it's a different movie.",
    "identified": "Filename was no help, but the credits found the movie.",
    "confirmed":  "Credits back up the filename.",
}


def load_results(path):
    rows = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                    rows[r["file"]] = r          # later lines win (reruns)
                except Exception:
                    pass
    return list(rows.values())


def _fmt(m):
    return f"{m.get('title')} ({m.get('year')})" if m else ""


def write_reports(results_path, html_path, csv_path):
    import csv
    import html as H
    rows = load_results(results_path)
    groups = defaultdict(list)
    for r in rows:
        groups[r.get("status", "unknown")].append(r)

    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["status", "file", "moved_to", "filename_says", "credits_say",
                    "tmdb_id", "score", "runtime_diff_min", "matched_names", "note"])
        for st in STATUS_ORDER:
            for r in sorted(groups.get(st, []), key=lambda r: r["file"]):
                m = r.get("match") or {}
                w.writerow([st, r["file"], r.get("moved_to", ""),
                            _fmt(r.get("filename_guess")), _fmt(m), m.get("tmdb_id", ""),
                            r.get("score", ""), r.get("runtime_diff_min", ""),
                            "; ".join(r.get("matched_names", [])),
                            " | ".join(x for x in (r.get("note"), r.get("move_error"),
                                                   r.get("error")) if x)])

    total = len(rows)
    counts = " · ".join(f"{len(groups.get(s, []))} {s}" for s in STATUS_ORDER)
    out = [f"""<!doctype html><meta charset="utf-8"><title>Credits check</title>
<style>
:root{{--bg:#fff;--fg:#1b1b1b;--mut:#666;--line:#e3e3e3;--card:#f6f6f6}}
@media (prefers-color-scheme:dark){{:root{{--bg:#141414;--fg:#e8e8e8;--mut:#999;--line:#2c2c2c;--card:#1d1d1d}}}}
body{{background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif;margin:24px auto;max-width:1200px;padding:0 16px}}
h1{{margin:0 0 4px}} .mut{{color:var(--mut)}} details{{margin:18px 0;border:1px solid var(--line);border-radius:8px}}
summary{{cursor:pointer;padding:10px 14px;font-weight:600;background:var(--card);border-radius:8px}}
summary small{{font-weight:400;color:var(--mut);margin-left:8px}}
.tw{{overflow-x:auto}} table{{border-collapse:collapse;width:100%}}
th,td{{text-align:left;padding:6px 10px;border-top:1px solid var(--line);vertical-align:top}}
th{{color:var(--mut);font-weight:500;cursor:pointer;white-space:nowrap}}
td.f{{word-break:break-all;max-width:380px}} td.n{{color:var(--mut);font-size:12px;max-width:300px}}
.bad{{color:#d23}}
</style>
<h1>Credits check</h1><p class="mut">{total} files · {counts}</p>"""]
    for st in STATUS_ORDER:
        g = sorted(groups.get(st, []), key=lambda r: r["file"])
        if not g:
            continue
        open_attr = " open" if st in ("weak", "unknown", "mismatch") else ""
        out.append(f'<details{open_attr}><summary>{st} — {len(g)}<small>{H.escape(STATUS_NOTE[st])}</small></summary>'
                   '<div class="tw"><table><thead><tr><th>File</th><th>Filename says</th>'
                   '<th>Credits say</th><th>Score</th><th>Runtime diff</th><th>Names found</th></tr></thead><tbody>')
        for r in g:
            m = r.get("match") or {}
            link = (f'<a href="https://www.themoviedb.org/movie/{m["tmdb_id"]}">{H.escape(_fmt(m))}</a>'
                    if m.get("tmdb_id") else "")
            fname = r["file"] + (f"  →  {r['moved_to']}" if r.get("moved_to") else "")
            diff = r.get("runtime_diff_min", "")
            dcls = ' class="bad"' if isinstance(diff, (int, float)) and abs(diff) > 5 else ""
            names = ", ".join(r.get("matched_names", [])[:8]) or r.get("error", "")
            for extra in ("note", "move_error"):
                if r.get(extra):
                    names = (names + " — " if names else "") + r[extra]
            out.append(f'<tr><td class="f">{H.escape(fname)}</td>'
                       f'<td>{H.escape(_fmt(r.get("filename_guess")))}</td><td>{link}</td>'
                       f'<td>{r.get("score", "")}</td><td{dcls}>{diff}</td>'
                       f'<td class="n">{H.escape(names)}</td></tr>')
        out.append("</tbody></table></div></details>")
    out.append("""<script>
document.querySelectorAll('th').forEach((th,i)=>th.onclick=()=>{const tb=th.closest('table').tBodies[0];
const rows=[...tb.rows],asc=th.dataset.a!=='1';th.dataset.a=asc?'1':'0';
rows.sort((a,b)=>{const x=a.cells[i].innerText,y=b.cells[i].innerText,nx=parseFloat(x),ny=parseFloat(y);
const c=(!isNaN(nx)&&!isNaN(ny))?nx-ny:x.localeCompare(y);return asc?c:-c});rows.forEach(r=>tb.appendChild(r))});
</script>""")
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))
    return {s: len(groups.get(s, [])) for s in STATUS_ORDER}




def configure(mcfg):
    """Apply settings from mogul.toml [movie] (verify_* keys)."""
    g = globals()
    for key, name in [("verify_rename", "RENAME"), ("verify_folder_per_movie", "FOLDER_PER_MOVIE"),
                      ("verify_name_format", "NAME_FORMAT"), ("verify_folder_format", "FOLDER_FORMAT"),
                      ("verify_articles_last", "ARTICLES_LAST"), ("verify_decade_folders", "DECADE_FOLDERS"),
                      ("verify_end_minutes", "END_MINUTES"), ("verify_start_minutes", "START_MINUTES"),
                      ("verify_frame_every", "FRAME_EVERY"), ("verify_confirm_score", "CONFIRM_SCORE"),
                      ("verify_confirm_min_names", "CONFIRM_MIN_NAMES"), ("verify_fuzz", "FUZZ")]:
        if key in mcfg:
            g[name] = mcfg[key]
    exts = mcfg.get("extensions")
    if exts:
        g["VIDEO_EXTS"] = {"." + e.lower().lstrip(".") for e in exts}
