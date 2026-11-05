#!/usr/bin/env python3
"""Generate Russian voice-over with ElevenLabs: one MP3 per scene + manifest.json.

Input is the scenes JSON written by prepare.py (list of {"id", "tts", "text"}),
or a plain text file (one scene), or --text. Uses previous_text/next_text for
continuous intonation (not on v3), retries 429/5xx, regenerates only scenes whose
MP3 is missing or whose text hash changed (or --force), checks the character
budget before spending, and writes manifest.json with per-scene durations (ffprobe),
voice, model, settings and seed.

Usage:
    tts.py tts.json --out audio/ --dry-run
    tts.py tts.json --out audio/ --model eleven_v4 --voice YOUR_VOICE_ID --seed 42
    tts.py --budget
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from el_client import Client, ElevenLabsError, load_key

log = logging.getLogger("tts")

# Aliases for --voice. Add your own voices here (подставь свой voice_id).
VOICES = {
    "pavel": "VlCYpQzyafIQIhmvxR2w",  # Voice Library «Pavel – Warm Conversational Narrator» (add to My Voices first)
    # "my-clone": "YOUR_VOICE_ID",
}
# Default voice: env ELEVENLABS_VOICE_ID (voice id or alias), else the library voice above.
DEFAULT_VOICE = os.environ.get("ELEVENLABS_VOICE_ID", "").strip() or "pavel"
DEFAULT_MODEL = "eleven_v4"
ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
# multilingual_v2 rejects / ignores language_code (docs).
NO_LANGUAGE_CODE = {"eleven_multilingual_v2"}
# Request stitching (previous_text/next_text) is not available for v3 (docs).
NO_CONTEXT = {"eleven_v3"}
# Per-request character limits (docs); unknown models fall back to 5 000.
CHAR_LIMITS = {"eleven_v4": 10_000, "eleven_v4_turbo": 10_000, "eleven_multilingual_v2": 10_000, "eleven_v3": 5_000}


@dataclass
class Segment:
    """One scene to voice."""

    id: str
    text: str


@dataclass
class ManifestEntry:
    """Result for one scene."""

    id: str
    file: str
    chars: int
    duration_s: float | None
    status: str
    request_id: str = ""
    text_sha: str = ""
    voice_id: str = ""
    model_id: str = ""
    seed: int | None = None
    voice_settings: dict[str, float | bool] | None = None


def text_sha(text: str) -> str:
    """Short hash of the scene text: detects scripts changed after generation."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def load_manifest(path: Path) -> dict[str, ManifestEntry]:
    """Previous manifest entries by id; unknown fields are ignored, broken files skipped."""
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("ignoring unreadable manifest %s: %s", path.name, exc)
        return {}
    known = set(ManifestEntry.__dataclass_fields__)
    entries: dict[str, ManifestEntry] = {}
    for e in raw.get("scenes", []) if isinstance(raw, dict) else []:
        if isinstance(e, dict) and {"id", "file", "chars", "duration_s", "status"} <= e.keys():
            entries[str(e["id"])] = ManifestEntry(**{k: v for k, v in e.items() if k in known})
    return entries


def needs_generation(seg: Segment, out_dir: Path, previous: dict[str, ManifestEntry], force: bool) -> bool:
    """True if the MP3 is missing, forced, or was made from a different text."""
    if force or not (out_dir / f"{seg.id}.mp3").exists():
        return True
    entry = previous.get(seg.id)
    if entry is None or not entry.text_sha:
        log.warning("%s.mp3 exists without a manifest hash: kept (use --force to redo)", seg.id)
        return False
    if entry.text_sha != text_sha(seg.text):
        log.info("%s: text changed since generation – regenerating", seg.id)
        return True
    return False


def load_segments(args: argparse.Namespace) -> list[Segment]:
    """Read segments from --text, a scenes JSON or a plain text file."""
    if args.text:
        return [Segment("s1", args.text)]
    if not args.input:
        raise SystemExit("give a scenes JSON / text file or --text")
    path = Path(args.input)
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() != ".json":
        return [Segment(path.stem if ID_RE.match(path.stem) else "s1", raw.strip())]
    data = json.loads(raw)
    if isinstance(data, dict):
        data = data.get("scenes") or data.get("segments") or []
    if not isinstance(data, list):
        raise SystemExit("scenes JSON must be a list")
    segs: list[Segment] = []
    for i, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            raise SystemExit(f"scene #{i} is not an object")
        text = str(item.get("tts") or item.get("text") or "").strip()
        segs.append(Segment(str(item.get("id") or f"s{i}"), text))
    bad = [s.id for s in segs if not ID_RE.match(s.id)]
    if bad:
        raise SystemExit(f"scene ids must match {ID_RE.pattern}: {bad}")
    if "+" in "".join(s.text for s in segs):
        log.warning("'+' found in TTS text: ElevenLabs does not understand it – run prepare.py --mode acute")
    return segs


def voice_settings(args: argparse.Namespace) -> dict[str, float | bool]:
    """voice_settings: v4 documents only stability + similarity; others only if given."""
    settings: dict[str, float | bool] = {"stability": args.stability}
    if args.similarity is not None:
        settings["similarity_boost"] = args.similarity
    if args.style is not None:
        settings["style"] = args.style
    if args.speed is not None:
        settings["speed"] = args.speed
    if args.speaker_boost is not None:
        settings["use_speaker_boost"] = args.speaker_boost
    if args.model.startswith("eleven_v4") and ({"style", "speed"} & settings.keys()):
        log.warning("eleven_v4 UI has no Style/Speed; the API may ignore or reject them (unverified)")
    if args.model == "eleven_v3" and args.stability not in (0.0, 0.5, 1.0):
        log.warning("eleven_v3 uses stability presets 0.0 / 0.5 / 1.0 (Creative / Natural / Robust)")
    return settings


def build_body(args: argparse.Namespace, segs: list[Segment], i: int, use_context: bool) -> dict[str, object]:
    """Request body for segment i."""
    body: dict[str, object] = {"text": segs[i].text, "model_id": args.model, "voice_settings": voice_settings(args)}
    if args.language_code and args.model not in NO_LANGUAGE_CODE:
        body["language_code"] = args.language_code
    if args.seed is not None:
        body["seed"] = args.seed
    if args.normalization != "auto":
        body["apply_text_normalization"] = args.normalization
    if args.dict:
        locators = []
        for item in args.dict[:3]:
            dict_id, _, version = item.partition(":")
            loc = {"pronunciation_dictionary_id": dict_id}
            if version:
                loc["version_id"] = version
            locators.append(loc)
        body["pronunciation_dictionary_locators"] = locators
    if use_context:
        if i > 0:
            body["previous_text"] = segs[i - 1].text
        if i < len(segs) - 1:
            body["next_text"] = segs[i + 1].text
    return body


def duration(path: Path) -> float | None:
    """Audio duration in seconds via ffprobe (None if unavailable)."""
    if not shutil.which("ffprobe") or not path.exists():
        return None
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        return round(float(res.stdout.strip()), 3)
    except ValueError:
        return None


def main() -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", nargs="?", help="scenes JSON from prepare.py, or a text file")
    ap.add_argument("--text", help="voice this text instead of a file")
    ap.add_argument("--out", type=Path, default=Path("audio"), help="output dir (default ./audio)")
    ap.add_argument("--voice", default=DEFAULT_VOICE, help=f"voice id or alias {sorted(VOICES)}")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="eleven_v4 (default) | eleven_v3 | eleven_multilingual_v2")
    ap.add_argument("--stability", type=float, default=0.5)
    ap.add_argument("--similarity", type=float, help="similarity_boost (server default if omitted)")
    ap.add_argument("--style", type=float, help="style exaggeration (not documented for v4)")
    ap.add_argument("--speed", type=float, help="0.7–1.2 (not documented for v4)")
    ap.add_argument("--speaker-boost", dest="speaker_boost", action="store_true", default=None)
    ap.add_argument("--no-speaker-boost", dest="speaker_boost", action="store_false")
    ap.add_argument("--seed", type=int, help="seed for repeatable takes (determinism not guaranteed)")
    ap.add_argument("--language-code", default="ru", help="ISO 639-1; dropped for multilingual_v2; '' to omit")
    ap.add_argument("--normalization", choices=["auto", "on", "off"], default="auto", help="apply_text_normalization")
    ap.add_argument("--dict", action="append", metavar="ID[:VERSION]", help="pronunciation dictionary (max 3)")
    ap.add_argument("--context", choices=["auto", "on", "off"], default="auto", help="previous_text/next_text")
    ap.add_argument("--format", default="mp3_44100_128", help="output_format")
    ap.add_argument("--only", help="comma-separated scene ids to (re)generate")
    ap.add_argument("--force", action="store_true", help="overwrite existing MP3s")
    ap.add_argument("--dry-run", action="store_true", help="print requests, no key, no API calls")
    ap.add_argument("--budget", action="store_true", help="print characters left and exit")
    ap.add_argument("--no-budget-check", action="store_true", help="skip the subscription check before generating")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")

    if args.budget:
        left, limit = Client(load_key()).characters_left()
        print(f"characters_left={left} limit={limit}")
        return 0

    voice_id = VOICES.get(args.voice, args.voice)
    segs = load_segments(args)
    only = {s.strip() for s in args.only.split(",")} if args.only else set()
    unknown = only - {s.id for s in segs}
    if unknown:
        raise SystemExit(f"unknown scene ids: {sorted(unknown)}")
    out_dir = args.out.resolve()
    manifest_path = out_dir / "manifest.json"
    previous = load_manifest(manifest_path)
    todo = [i for i, s in enumerate(segs) if (not only or s.id in only) and needs_generation(s, out_dir, previous, args.force)]
    chars = sum(len(segs[i].text) for i in todo)
    limit = CHAR_LIMITS.get(args.model, 5_000)
    too_long = [segs[i].id for i in todo if len(segs[i].text) > limit]
    if too_long:
        raise SystemExit(f"scenes over the {args.model} limit of {limit} chars: {too_long} – split them")
    use_context = args.context == "on" or (args.context == "auto" and args.model not in NO_CONTEXT)
    print(f"voice={voice_id} model={args.model} scenes={len(todo)}/{len(segs)} chars={chars} out={out_dir}")

    if args.dry_run:
        for i in todo:
            body = build_body(args, segs, i, use_context)
            shown = {k: (v[:60] + "…" if isinstance(v, str) and len(v) > 60 else v) for k, v in body.items()}
            print(f"POST /v1/text-to-speech/{voice_id}?output_format={args.format} [{segs[i].id}]")
            print("  " + json.dumps(shown, ensure_ascii=False))
        print("dry-run: no key read, no API calls")
        return 0

    client = Client(load_key())
    if not args.no_budget_check and todo:
        left, limit = client.characters_left()
        print(f"characters_left={left} limit={limit}")
        if left < chars:
            raise SystemExit(f"not enough characters: need {chars}, left {left}")

    out_dir.mkdir(parents=True, exist_ok=True)
    failed: list[str] = []
    for i in todo:
        seg = segs[i]
        target = (out_dir / f"{seg.id}.mp3").resolve()
        if target.parent != out_dir:
            raise SystemExit(f"refusing to write outside {out_dir}")
        body = build_body(args, segs, i, use_context)
        path = f"/v1/text-to-speech/{voice_id}?output_format={args.format}"
        try:
            audio, headers = client.request("POST", path, body, accept_json=False)
        except ElevenLabsError as exc:
            if use_context and args.context == "auto" and exc.status in (400, 422) and "_text" in exc.body:
                log.warning("model rejected previous_text/next_text; retrying without context")
                use_context = False
                body = build_body(args, segs, i, use_context)
                try:
                    audio, headers = client.request("POST", path, body, accept_json=False)
                except ElevenLabsError as exc2:
                    log.error("%s failed: %s", seg.id, exc2)
                    failed.append(seg.id)
                    continue
            else:
                log.error("%s failed: %s", seg.id, exc)
                failed.append(seg.id)
                continue
        tmp = target.with_suffix(".mp3.part")
        tmp.write_bytes(audio)
        os.replace(tmp, target)  # atomic: an interrupted run never leaves a truncated MP3
        entry = ManifestEntry(
            seg.id,
            target.name,
            len(seg.text),
            duration(target),
            "ok",
            headers.get("request-id", ""),
            text_sha(seg.text),
            voice_id,
            args.model,
            args.seed,
            voice_settings(args),
        )
        previous[seg.id] = entry
        log.info("%s ok %.2fs", seg.id, entry.duration_s or 0.0)
        time.sleep(0.3)

    scenes = []
    for s in segs:
        e = previous.get(s.id)
        if e is None and (out_dir / f"{s.id}.mp3").exists():
            e = ManifestEntry(s.id, f"{s.id}.mp3", len(s.text), duration(out_dir / f"{s.id}.mp3"), "existing")
        if e is not None:
            scenes.append(asdict(e))
    manifest = {
        "last_run": {"voice_id": voice_id, "model_id": args.model, "context": use_context},
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "scenes": scenes,
        "total_duration_s": round(sum(e["duration_s"] or 0 for e in scenes), 3),
        "texts": {s.id: s.text for s in segs},
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"manifest: {manifest_path} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
