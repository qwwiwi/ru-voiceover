#!/usr/bin/env python3
"""QA for generated voice-over: back-transcribe and diff against the source text.

Transcribes each MP3 with mlx-whisper (if installed; otherwise skips with a note)
and flags missing, extra and misread words. Transcription does NOT catch wrong
stress (the words are the same), so the script also prints a listening checklist:
every homograph / glossary / flagged word from prepare.py's review JSON, with the
timestamp where it is spoken, for a human to check by ear.

Usage:
    check.py audio/manifest.json --review review.json
    check.py --audio take.mp3 --text "Привет, я Миша." --review review.json
"""

from __future__ import annotations

import argparse
import difflib
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("check")

SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_GLOSSARY = SKILL_DIR / "data" / "glossary.yaml"
DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo"
WORD_RE = re.compile(r"[a-zа-я0-9]+")


@dataclass
class Item:
    """One audio file with its expected text."""

    id: str
    audio: Path
    text: str


@dataclass
class Result:
    """Diff result for one item."""

    id: str
    transcript: str = ""
    issues: list[str] = field(default_factory=list)
    listen: list[str] = field(default_factory=list)
    skipped: str = ""


def norm_text(text: str, latin_map: dict[str, str]) -> list[str]:
    """Normalise for comparison: Latin terms -> Cyrillic, no marks, lower, ё/э -> е, hyphens split."""
    for src in sorted(latin_map, key=len, reverse=True):
        text = re.sub(rf"(?<![A-Za-z0-9]){re.escape(src)}(?![A-Za-z0-9])", latin_map[src], text)
    text = text.replace("+", "").replace("́", "").lower().replace("ё", "е").replace("э", "е")
    return WORD_RE.findall(text.replace("-", " "))


def load_latin_map(path: Path) -> dict[str, str]:
    """Glossary terms as Latin -> plain Cyrillic (PyYAML optional)."""
    try:
        import yaml
    except ImportError:
        log.info("PyYAML missing: Latin terms in transcripts are compared as is")
        return {}
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    mapping: dict[str, str] = {}
    for item in raw.get("terms") or []:
        srcs = item["src"] if isinstance(item["src"], list) else [item["src"]]
        for s in srcs:
            mapping[str(s)] = str(item["say"]).replace("+", "")
    return mapping


def diff_words(expected: list[str], heard: list[str]) -> list[str]:
    """Human-readable word diff; near-identical words (ratio >= 0.8) are accepted."""
    issues: list[str] = []
    sm = difflib.SequenceMatcher(a=expected, b=heard, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        a, b = " ".join(expected[i1:i2]), " ".join(heard[j1:j2])
        if op == "replace" and difflib.SequenceMatcher(a=a, b=b).ratio() >= 0.8:
            continue
        if op == "delete":
            issues.append(f"missing: «{a}»")
        elif op == "insert":
            issues.append(f"extra: «{b}»")
        else:
            note = " (number?)" if any(ch.isdigit() for ch in a + b) else ""
            issues.append(f"misread: «{a}» → «{b}»{note}")
    return issues


def control_words(review_path: Path | None, scene: str) -> list[tuple[str, str]]:
    """(word in TTS form, reason) from prepare.py's review JSON for one scene."""
    if not review_path:
        return []
    rows = json.loads(review_path.read_text(encoding="utf-8"))
    if any(r.get("scene") == scene for r in rows):
        rows = [r for r in rows if r.get("scene") == scene]
    picked: list[tuple[str, str]] = []
    for r in rows:
        origin = str(r.get("origin", ""))
        if r.get("flags") or origin.startswith(("homograph", "glossary", "manual")):
            reason = "; ".join(r.get("flags") or []) or origin
            picked.append((str(r.get("tts", r.get("word", ""))), reason))
    return picked


def find_word(key: list[str], words_ts: list[tuple[str, float]], latin_map: dict[str, str]) -> float | None:
    """Start time of the first heard word similar to key[0] (ratio >= 0.75)."""
    if not key:
        return None
    for heard, start in words_ts:
        cand = norm_text(heard, latin_map)
        if cand and difflib.SequenceMatcher(a=key[0], b=cand[0]).ratio() >= 0.75:
            return start
    return None


def fmt_ts(seconds: float) -> str:
    """Seconds -> m:ss.s."""
    return f"{int(seconds // 60)}:{seconds % 60:04.1f}"


def transcribe(audio: Path, model: str) -> tuple[str, list[tuple[str, float]]]:
    """Transcribe with mlx-whisper: (text, [(word, start_s)])."""
    import mlx_whisper

    res = mlx_whisper.transcribe(str(audio), path_or_hf_repo=model, language="ru", word_timestamps=True)
    words: list[tuple[str, float]] = []
    for seg in res.get("segments", []):
        for w in seg.get("words", []):
            words.append((str(w.get("word", "")).strip(), float(w.get("start", 0.0))))
    return str(res.get("text", "")).strip(), words


def load_items(args: argparse.Namespace) -> list[Item]:
    """Items from a tts.py manifest or --audio/--text."""
    if args.audio:
        text = Path(args.text_file).read_text(encoding="utf-8") if args.text_file else (args.text or "")
        if not text:
            raise SystemExit("--audio needs --text or --text-file")
        return [Item(args.audio.stem, args.audio, text)]
    if not args.manifest:
        raise SystemExit("give a manifest.json or --audio")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    texts = manifest.get("texts", {})
    base = args.manifest.parent
    return [Item(s["id"], base / s["file"], texts.get(s["id"], "")) for s in manifest.get("scenes", [])]


def main() -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manifest", nargs="?", type=Path, help="manifest.json from tts.py")
    ap.add_argument("--audio", type=Path, help="single audio file instead of a manifest")
    ap.add_argument("--text", help="expected text for --audio")
    ap.add_argument("--text-file", help="expected text file for --audio")
    ap.add_argument("--review", type=Path, help="review JSON from prepare.py --review-json")
    ap.add_argument("--glossary", type=Path, default=DEFAULT_GLOSSARY)
    ap.add_argument("--model", default=DEFAULT_MODEL, help="mlx-whisper HF repo")
    ap.add_argument("--report", type=Path, help="write the markdown report here too")
    ap.add_argument("--fail-on-diff", action="store_true", help="exit 1 if any word issue is found")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    for noisy in ("httpx", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    items = load_items(args)
    latin_map = load_latin_map(args.glossary)
    try:
        import mlx_whisper  # noqa: F401

        have_whisper = True
    except ImportError:
        have_whisper = False
        log.warning("mlx-whisper not installed: back-transcription skipped (setup.sh --with-whisper)")

    results: list[Result] = []
    for it in items:
        res = Result(it.id)
        words_ts: list[tuple[str, float]] = []
        if not it.audio.exists():
            res.skipped = f"audio not found: {it.audio.name}"
        elif have_whisper:
            res.transcript, words_ts = transcribe(it.audio, args.model)
            res.issues = diff_words(norm_text(it.text, latin_map), norm_text(res.transcript, latin_map))
        else:
            res.skipped = "mlx-whisper not installed – listen manually"
        for word, reason in control_words(args.review, it.id):
            ts = find_word(norm_text(word, latin_map), words_ts, latin_map)
            res.listen.append(f"{fmt_ts(ts) if ts is not None else '?:??'} {word} – {reason}")
        results.append(res)

    lines = ["# Voice-over QA", ""]
    for r in results:
        lines.append(f"## {r.id}")
        if r.skipped:
            lines.append(f"- skipped: {r.skipped}")
        else:
            lines.append(f"- heard: {r.transcript}")
            lines.extend(f"- !! {x}" for x in r.issues) if r.issues else lines.append("- words: ok")
        if r.listen:
            lines.append("- listen for stress:")
            lines.extend(f"  - [ ] {x}" for x in r.listen)
        lines.append("")
    lines.append("Transcription does not detect wrong stress: listen to every checklist item.")
    report = "\n".join(lines)
    print(report)
    if args.report:
        args.report.write_text(report + "\n", encoding="utf-8")
    return 1 if args.fail_on_diff and any(r.issues for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
