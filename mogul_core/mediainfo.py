"""Read technical details from a media file with mediainfo."""

import json
import subprocess


class MediaInfoError(Exception):
    pass


CODECS = {
    "MPEG Audio": "mp3",
    "Vorbis": "vorbis",
    "Opus": "opus",
    "AAC": "aac",
    "FLAC": "flac",
    "PCM": "pcm",
    "WMA": "wma",
    "ALAC": "alac",
}


def _num(value, kind=float):
    if value in (None, ""):
        return None
    try:
        return kind(float(str(value).split("/")[0].strip()))
    except ValueError:
        return None


def read(path):
    try:
        proc = subprocess.run(
            ["mediainfo", "--Output=JSON", path],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        raise MediaInfoError("mediainfo not found (sudo apt install mediainfo)")
    if proc.returncode != 0:
        raise MediaInfoError(proc.stderr.strip() or "mediainfo failed")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise MediaInfoError("mediainfo output could not be read")

    tracks = (data.get("media") or {}).get("track") or []
    general = next((t for t in tracks if t.get("@type") == "General"), {})
    audio = next((t for t in tracks if t.get("@type") == "Audio"), None)
    if audio is None:
        raise MediaInfoError("no audio track found")

    fmt = audio.get("Format")
    codec = CODECS.get(fmt, (fmt or "unknown").lower())
    if fmt == "MPEG Audio" and audio.get("Format_Profile") not in (None, "Layer 3"):
        codec = "mp" + str(audio.get("Format_Profile", "")).replace("Layer ", "")

    bitrate = (_num(audio.get("BitRate"), int)
               or _num(audio.get("BitRate_Nominal"), int)
               or _num(general.get("OverallBitRate"), int))
    duration = _num(audio.get("Duration")) or _num(general.get("Duration"))

    return {
        "container": (general.get("Format") or "unknown"),
        "codec": codec,
        "bitrate": bitrate,
        "bitrate_mode": audio.get("BitRate_Mode") or general.get("OverallBitRate_Mode"),
        "channels": _num(audio.get("Channels"), int),
        "sample_rate": _num(audio.get("SamplingRate"), int),
        "duration": round(duration, 3) if duration is not None else None,
    }
