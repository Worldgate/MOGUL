"""Read technical details from a media file with mediainfo."""

import json
import os
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


def packet_length(path):
    """Real playing time from the audio packets themselves (ffprobe adds up
    every packet's duration). For files whose header doesn't say how long
    they are, e.g. raw ADTS .aac streams, where mediainfo gives no
    duration. Reads the whole file but decodes nothing. None if it fails."""
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "packet=duration_time", "-of", "csv=p=0", path],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        return None
    total = 0.0
    for line in proc.stdout.splitlines():
        try:
            total += float(line.strip().rstrip(","))
        except ValueError:
            pass
    return round(total, 3) if total > 0 else None


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
    if not duration:
        # raw .aac (ADTS) and similar: no length in any header, so count it
        duration = packet_length(path)
    if not bitrate and duration:
        try:
            bitrate = int(os.path.getsize(path) * 8 / duration)
        except OSError:
            pass

    return {
        "container": (general.get("Format") or "unknown"),
        "codec": codec,
        "bitrate": bitrate,
        "bitrate_mode": audio.get("BitRate_Mode") or general.get("OverallBitRate_Mode"),
        "channels": _num(audio.get("Channels"), int),
        "sample_rate": _num(audio.get("SamplingRate"), int),
        "duration": round(duration, 3) if duration is not None else None,
    }
