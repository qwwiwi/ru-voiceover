"""Minimal ElevenLabs HTTP client shared by tts.py, dict.py and voices.py (stdlib only).

The API key is read from env ELEVENLABS_API_KEY, or from a key file (default below,
override with ELEVENLABS_KEY_FILE), inside this process only. It is never printed, logged, passed in argv or written
anywhere. Error messages contain the HTTP status and a short response body only.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger("el_client")

API = "https://api.elevenlabs.io"
DEFAULT_KEY_FILE = Path.home() / ".config" / "ru-voiceover" / "elevenlabs.key"
RETRY_STATUS = {429, 500, 502, 503, 504}


class ElevenLabsError(RuntimeError):
    """Non-retryable API error (status + short body, never the key)."""

    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}: {body[:300]}")
        self.status = status
        self.body = body


def key_file() -> Path:
    """Key file path: $ELEVENLABS_KEY_FILE or the default."""
    return Path(os.environ.get("ELEVENLABS_KEY_FILE") or DEFAULT_KEY_FILE).expanduser()


def load_key() -> str:
    """Read the API key from env ELEVENLABS_API_KEY or from the key file.

    Returns:
        The key string.

    Raises:
        SystemExit: If no key is configured (the path is not echoed to
            avoid leaking secret locations into chat logs).
    """
    env_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if env_key:
        return env_key
    path = key_file()
    if not path.is_file():
        raise SystemExit("ElevenLabs key not found (set ELEVENLABS_API_KEY or ELEVENLABS_KEY_FILE)")
    key = path.read_text(encoding="utf-8").strip()
    if not key:
        raise SystemExit("ElevenLabs key file is empty")
    return key


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects: urllib would forward the xi-api-key header to the new host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise urllib.error.HTTPError(req.full_url, code, f"redirect refused: {msg}", headers, fp)


_OPENER = urllib.request.build_opener(_NoRedirect())


class Client:
    """Tiny ElevenLabs client with retries on 429/5xx and network errors."""

    def __init__(self, key: str, retries: int = 4, timeout: float = 180.0) -> None:
        self._key = key
        self.retries = retries
        self.timeout = timeout

    def __repr__(self) -> str:  # never expose the key in tracebacks / logs
        return "Client(<key hidden>)"

    def request(
        self, method: str, path: str, body: object | None = None, accept_json: bool = True
    ) -> tuple[bytes, dict[str, str]]:
        """Send a request and return (raw body, response headers).

        Args:
            method: HTTP method.
            path: Path starting with /v1/...
            body: JSON-serialisable body or None.
            accept_json: Only affects the Accept header.

        Returns:
            Raw response bytes and lower-cased headers.

        Raises:
            ElevenLabsError: On non-retryable HTTP errors or after the last retry.
        """
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        headers = {"xi-api-key": self._key, "Accept": "application/json" if accept_json else "*/*"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        delay = 2.0
        for attempt in range(1, self.retries + 2):
            req = urllib.request.Request(API + path, data=data, method=method, headers=headers)
            try:
                with _OPENER.open(req, timeout=self.timeout) as resp:
                    return resp.read(), {k.lower(): v for k, v in resp.headers.items()}
            except urllib.error.HTTPError as exc:
                text = exc.read().decode("utf-8", "replace")
                if exc.code not in RETRY_STATUS or attempt > self.retries:
                    raise ElevenLabsError(exc.code, text) from None
                log.warning("%s %s -> HTTP %s, retry %d in %.0fs", method, path.split("?")[0], exc.code, attempt, delay)
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt > self.retries:
                    raise ElevenLabsError(0, f"network error: {exc}") from None
                log.warning("%s %s -> %s, retry %d in %.0fs", method, path.split("?")[0], exc, attempt, delay)
            time.sleep(delay)
            delay *= 2
        raise ElevenLabsError(0, "unreachable")

    def json(self, method: str, path: str, body: object | None = None) -> dict[str, object]:
        """Request and decode a JSON object."""
        raw, _ = self.request(method, path, body)
        decoded = json.loads(raw.decode("utf-8"))
        if not isinstance(decoded, dict):
            raise ElevenLabsError(200, "expected a JSON object")
        return decoded

    def characters_left(self) -> tuple[int, int]:
        """Return (characters left, monthly limit) from /v1/user/subscription."""
        sub = self.json("GET", "/v1/user/subscription")
        limit = int(str(sub.get("character_limit", 0)))
        used = int(str(sub.get("character_count", 0)))
        return limit - used, limit
