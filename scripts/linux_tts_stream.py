#!/usr/bin/env python3
"""
Linux Voicebox TTS streaming script.

Implements the documented remote/backend flow from:
- /home/runner/work/voicebox/voicebox/docs/content/docs/overview/remote-mode.mdx
- /home/runner/work/voicebox/voicebox/docs/content/docs/developer/tts-generation.mdx
- /home/runner/work/voicebox/voicebox/README.md

Behavior:
1) GET /health (server readiness check)
2) GET /profiles (resolve hardcoded custom voice profile)
3) POST /generate/stream (hardcoded phrase -> WAV stream)
4) Pipe streamed WAV bytes directly to Linux speakers via ffplay/mpv/aplay
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


# Voicebox server (Remote Mode backend URL)
SERVER_URL = "http://127.0.0.1:17493"

# Your custom voice profile identifier (name or id from GET /profiles)
PROFILE_SELECTOR = "Morgan"

# Hardcoded phrase requested by user
HARDCODED_TEXT = "Hello from Voicebox on Linux. This is a hardcoded streaming test."

# Optional generation tuning
LANGUAGE = "en"
ENGINE: str | None = None
MODEL_SIZE: str | None = None
TIMEOUT_SECONDS = 30


def _http_json(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    url = f"{SERVER_URL}{path}"
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(url=url, method=method, data=data, headers=headers)
    with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8"))


def check_health() -> None:
    try:
        health = _http_json("GET", "/health")
    except (HTTPError, URLError, TimeoutError) as err:
        raise RuntimeError(f"Voicebox health check failed at {SERVER_URL}/health: {err}") from err

    status = str(health.get("status", "unknown"))
    if status.lower() not in {"ok", "healthy", "ready"}:
        print(f"Warning: /health status is '{status}'", file=sys.stderr)


def resolve_profile_id(selector: str) -> str:
    try:
        profiles = _http_json("GET", "/profiles")
    except (HTTPError, URLError, TimeoutError) as err:
        raise RuntimeError(f"Failed to fetch profiles from {SERVER_URL}/profiles: {err}") from err

    if not isinstance(profiles, list):
        raise RuntimeError("Unexpected /profiles response format (expected list).")

    selector_lower = selector.strip().lower()
    for profile in profiles:
        profile_id = str(profile.get("id", ""))
        profile_name = str(profile.get("name", ""))
        if selector == profile_id or selector_lower == profile_name.lower():
            return profile_id

    available = ", ".join(sorted(str(p.get("name", "<unnamed>")) for p in profiles))
    raise RuntimeError(
        f"Profile '{selector}' was not found. Available profiles: {available or '(none)'}"
    )


def choose_player() -> list[str]:
    if shutil.which("ffplay"):
        return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", "-i", "pipe:0"]
    if shutil.which("mpv"):
        return ["mpv", "--no-video", "--really-quiet", "--cache=no", "-"]
    if shutil.which("aplay"):
        return ["aplay", "-q", "-"]
    raise RuntimeError("No audio player found. Install ffplay, mpv, or aplay.")


def stream_and_play(profile_id: str) -> None:
    payload: dict[str, Any] = {
        "profile_id": profile_id,
        "text": HARDCODED_TEXT,
        "language": LANGUAGE,
    }
    if ENGINE:
        payload["engine"] = ENGINE
    if MODEL_SIZE:
        payload["model_size"] = MODEL_SIZE

    request = Request(
        url=f"{SERVER_URL}/generate/stream",
        method="POST",
        headers={"Content-Type": "application/json"},
        data=json.dumps(payload).encode("utf-8"),
    )

    player_cmd = choose_player()
    player: subprocess.Popen[bytes] | None = None
    try:
        player = subprocess.Popen(player_cmd, stdin=subprocess.PIPE)
        if player.stdin is None:
            raise RuntimeError("Failed to open player stdin.")

        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                try:
                    player.stdin.write(chunk)
                    player.stdin.flush()
                except BrokenPipeError as err:
                    raise RuntimeError("Audio player closed early while streaming.") from err

        player.stdin.close()
        exit_code = player.wait(timeout=30)
        if exit_code != 0:
            raise RuntimeError(f"Audio player exited with code {exit_code}. Command: {' '.join(player_cmd)}")
    except HTTPError as err:
        message = err.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"/generate/stream failed: HTTP {err.code} - {message}") from err
    except URLError as err:
        raise RuntimeError(f"Failed to reach Voicebox server: {err}") from err
    finally:
        if player and player.poll() is None:
            player.terminate()


def main() -> int:
    try:
        check_health()
        profile_id = resolve_profile_id(PROFILE_SELECTOR)
        print(f"Using profile_id={profile_id}")
        print(f"Speaking: {HARDCODED_TEXT}")
        stream_and_play(profile_id)
        print("Done.")
        return 0
    except Exception as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
