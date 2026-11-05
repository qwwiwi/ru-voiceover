#!/usr/bin/env python3
"""Prepare Russian text for ElevenLabs TTS: ё, glossary, stress marks, review table.

Pipeline per scene: source text -> ё-fication (eyo, optional) -> glossary
(Latin/brands -> Cyrillic with stress) -> silero-stress -> homograph overrides ->
cleanup (no marks on one-vowel words or before ё) -> U+0301 (or caps / '+').

Input: plain text (one scene per paragraph), markdown lesson script
(``### N. `id``` headings + ``**Голос:** ...`` lines), or scenes JSON
(list of {"id", "text", "tts"?} or {"scenes"|"segments": [...]}).
Manual stress in the source ('+' before a vowel or U+0301 after it) always wins.

Usage:
    prepare.py lesson.md --scene 4 --out tts.json --review review.md
    echo "Привет, я Миша." | prepare.py -
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

import yaml

log = logging.getLogger("prepare")

SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_GLOSSARY = SKILL_DIR / "data" / "glossary.yaml"
DEFAULT_HOMOGRAPHS = SKILL_DIR / "data" / "homographs.yaml"
DEFAULT_HOME = Path(os.environ.get("RU_VOICEOVER_HOME", Path.home() / ".cache" / "ru-voiceover"))

VOWELS = "аеёиоуыэюяАЕЁИОУЫЭЮЯ"
ACUTE = "́"
_W = "(?:\\+(?=[аеёиоуыэюяАЕЁИОУЫЭЮЯ])|[А-Яа-яЁё\u0301])+"
WORD_RE = re.compile(rf"{_W}(?:-{_W})*")
LATIN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:['’.-][A-Za-z0-9]+)*")
DIGIT_RE = re.compile(r"\d+(?:[.,]\d+)?")
ABBR_RE = re.compile(r"(?<![А-Яа-яЁё])[А-ЯЁ]{2,}(?![А-Яа-яЁё])")
SENTENCE_END_RE = re.compile(r"[.!?…;:]")
VOICE_LINE_RE = re.compile(r"^\s*\*\*(?:Голос|Озвучка|Voice|VO)[:：]?\*\*[:：]?\s*(.+?)\s*$", re.IGNORECASE)
MD_HEADING_RE = re.compile(r"^#{2,4}\s+(?:(\d+)[.)]\s*)?(?:`([^`]+)`)?")
ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


# ---------------------------------------------------------------- data models


@dataclass
class Term:
    """Latin/brand term -> spoken Cyrillic."""

    src: list[str]
    say: str
    status: str = "ok"
    note: str = ""
    ipa: str = ""


@dataclass
class WordRule:
    """Forced stress for a Cyrillic word form or stem."""

    key: str
    say: str
    prefix: bool
    status: str = "ok"
    note: str = ""


@dataclass
class HomographGroup:
    """One homograph family: senses with context hints and per-form variants."""

    name: str
    senses: dict[str, list[str]]
    forms: dict[str, dict[str, str]]
    default: str | None = None


@dataclass
class Glossary:
    """Parsed glossary.yaml."""

    terms: list[Term]
    words: list[WordRule]


@dataclass
class Scene:
    """One TTS segment."""

    id: str
    text: str
    tts: str | None = None


@dataclass
class ReviewRow:
    """One marked or flagged word in the review table."""

    scene: str
    word: str
    tts: str
    origin: str
    flags: list[str] = field(default_factory=list)


@dataclass
class SceneResult:
    """Prepared scene, written to the output JSON."""

    id: str
    text: str
    tts: str
    tts_plus: str
    chars: int


# ---------------------------------------------------------------- text helpers


def acute_to_plus(text: str) -> str:
    """Convert 'V' + U+0301 to '+V' so all marks share one internal form."""
    return re.sub(rf"([{VOWELS}]){ACUTE}", r"+\1", text)


def strip_marks(text: str) -> str:
    """Remove stress marks: '+' before a vowel and U+0301 (other '+' stay, e.g. «2+2»)."""
    return re.sub(rf"\+(?=[{VOWELS}])", "", text).replace(ACUTE, "")


def to_acute(text: str) -> str:
    """'+V' -> 'V' + U+0301."""
    return re.sub(rf"\+([{VOWELS}])", lambda m: m.group(1) + ACUTE, text)


def to_caps(text: str) -> str:
    """'+V' -> uppercase V (fallback; on v4 capitals also add emphasis)."""
    return re.sub(rf"\+([{VOWELS}])", lambda m: m.group(1).upper(), text)


def render(text: str, mode: str) -> str:
    """Render internal '+' marks in the requested output mode."""
    renderers: dict[str, Callable[[str], str]] = {
        "acute": to_acute,
        "caps": to_caps,
        "plus": lambda t: t,
        "none": strip_marks,
    }
    return renderers[mode](text)


def vowel_count(word: str) -> int:
    """Count Russian vowels in a word (marks ignored)."""
    return sum(ch in VOWELS for ch in word)


def match_case(template: str, original: str) -> str:
    """Copy the original word's case pattern (lower / Title / UPPER) onto template."""
    bare = strip_marks(original)
    if not bare:
        return template
    if len(bare) > 1 and bare.isupper():
        return template.upper()
    if not bare[0].isupper():
        return template.lower()
    for i, ch in enumerate(template):
        if ch.isalpha():
            return template[:i] + ch.upper() + template[i + 1 :]
    return template


def cleanup(word: str) -> str:
    """Drop marks that only add risk: one-vowel words and '+' before ё."""
    if vowel_count(word) <= 1:
        return word.replace("+", "")
    return re.sub(r"\+([ёЁ])", r"\1", word)


def norm(word: str) -> str:
    """Lowercase, no marks, ё -> е: key for comparing words."""
    return strip_marks(word).lower().replace("ё", "е")


# ---------------------------------------------------------------- loading


BAD_MARK_RE = re.compile(rf"\+(?![{VOWELS}])")


def check_marks(value: str, where: str) -> None:
    """Fail fast on '+' that is not followed by a vowel (a typo in the data files)."""
    if BAD_MARK_RE.search(value):
        raise SystemExit(f"{where}: '+' must stand right before a vowel: {value!r}")


def load_glossary(path: Path) -> Glossary:
    """Load glossary.yaml.

    Args:
        path: Path to glossary.yaml.

    Returns:
        Parsed glossary.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    terms: list[Term] = []
    for item in raw.get("terms") or []:
        src = item["src"] if isinstance(item["src"], list) else [item["src"]]
        check_marks(acute_to_plus(str(item["say"])), f"{path.name} term {src}")
        terms.append(
            Term(
                src=[str(s) for s in src],
                say=acute_to_plus(str(item["say"])),
                status=str(item.get("status", "ok")),
                note=str(item.get("note", "")),
                ipa=str(item.get("ipa", "")),
            )
        )
    words: list[WordRule] = []
    for key, val in (raw.get("words") or {}).items():
        info = val if isinstance(val, dict) else {"say": val}
        key = str(key).lower()
        say = acute_to_plus(str(info["say"])).lower()
        check_marks(say.rstrip("*"), f"{path.name} word {key}")
        prefix = key.endswith("*")
        words.append(
            WordRule(
                key=key.rstrip("*"),
                say=say.rstrip("*"),
                prefix=prefix,
                status=str(info.get("status", "ok")),
                note=str(info.get("note", "")),
            )
        )
    return Glossary(terms=terms, words=words)


def load_homographs(path: Path) -> list[HomographGroup]:
    """Load homographs.yaml.

    Args:
        path: Path to homographs.yaml.

    Returns:
        List of homograph groups.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    groups: list[HomographGroup] = []
    for g in raw.get("groups") or []:
        forms = {
            str(form).lower(): {sense: acute_to_plus(str(v)) for sense, v in variants.items()}
            for form, variants in (g.get("forms") or {}).items()
        }
        for form, variants in forms.items():
            for v in variants.values():
                check_marks(v, f"{path.name} {g['group']}/{form}")
        senses = {str(k): [str(h).lower() for h in (v or [])] for k, v in (g.get("senses") or {}).items()}
        groups.append(HomographGroup(name=str(g["group"]), senses=senses, forms=forms, default=g.get("default")))
    return groups


def _md_scenes(text: str) -> list[Scene]:
    """Extract scenes from a markdown lesson script."""
    scenes: list[Scene] = []
    current_id: str | None = None
    number = 0
    for line in text.splitlines():
        heading = MD_HEADING_RE.match(line)
        if heading and (heading.group(1) or heading.group(2)):
            number += 1
            current_id = heading.group(2) or f"s{heading.group(1)}"
            continue
        voice = VOICE_LINE_RE.match(line)
        if voice:
            sid = current_id or f"s{len(scenes) + 1}"
            scenes.append(Scene(id=sid, text=voice.group(1)))
            current_id = None
    if scenes:
        return scenes
    # No voice lines: strip markdown and treat paragraphs as scenes.
    plain = re.sub(r"`[^`]*`|^#+\s*|\*\*|__|^\s*[-*]\s+", "", text, flags=re.MULTILINE)
    plain = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", plain)
    return _paragraph_scenes(plain)


def _paragraph_scenes(text: str) -> list[Scene]:
    """One scene per non-empty paragraph."""
    paras = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", text)]
    return [Scene(id=f"p{i}", text=p) for i, p in enumerate((p for p in paras if p), start=1)]


def _json_scenes(data: object) -> list[Scene]:
    """Scenes from JSON: a list or an object with scenes/segments."""
    if isinstance(data, dict):
        data = data.get("scenes") or data.get("segments") or []
    if not isinstance(data, list):
        raise ValueError("scenes JSON must be a list or {scenes|segments: [...]}")
    scenes: list[Scene] = []
    for i, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"scene #{i} is not an object")
        text = str(item.get("text") or item.get("voice") or "")
        tts = item.get("tts")
        scenes.append(Scene(id=str(item.get("id") or f"s{i}"), text=text, tts=str(tts) if tts else None))
    return scenes


def load_scenes(source: str) -> list[Scene]:
    """Load scenes from a file path or '-' (stdin).

    Args:
        source: File path (.json, .md, other = plain text) or '-'.

    Returns:
        Scenes in order.
    """
    if source == "-":
        return _paragraph_scenes(sys.stdin.read())
    path = Path(source)
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return _json_scenes(json.loads(text))
    if path.suffix.lower() in {".md", ".markdown"}:
        return _md_scenes(text)
    return _paragraph_scenes(text)


def select_scenes(scenes: list[Scene], selector: str | None) -> list[Scene]:
    """Filter scenes by comma-separated ids or 1-based numbers."""
    if not selector:
        return scenes
    wanted = [s.strip() for s in selector.split(",") if s.strip()]
    picked: list[Scene] = []
    for w in wanted:
        match = [s for s in scenes if s.id == w]
        if not match and w.isdigit() and 1 <= int(w) <= len(scenes):
            match = [scenes[int(w) - 1]]
        if not match:
            raise SystemExit(f"scene not found: {w} (have: {', '.join(s.id for s in scenes)})")
        picked.extend(match)
    return picked


# ---------------------------------------------------------------- pipeline


class Preparer:
    """Runs the stress pipeline over scenes and collects review rows."""

    def __init__(
        self,
        glossary: Glossary,
        homographs: list[HomographGroup],
        yo_tool: Path | None,
        accentor: Callable[[str], str] | None,
    ) -> None:
        self.glossary = glossary
        self.yo_tool = yo_tool
        self.accentor = accentor
        self.rows: list[ReviewRow] = []
        # Every token produced by the glossary keeps the glossary's own stress.
        self.locked: dict[str, tuple[str, Term]] = {}
        for term in glossary.terms:
            for tok in WORD_RE.findall(term.say):
                if vowel_count(tok) >= 2:
                    self.locked[norm(tok)] = (tok, term)
        self.exact = {norm(r.key): r for r in glossary.words if not r.prefix}
        self.stems = sorted((r for r in glossary.words if r.prefix), key=lambda r: len(r.key), reverse=True)
        self.homo_forms: dict[str, HomographGroup] = {}
        for g in homographs:
            for form in g.forms:
                self.homo_forms[form] = g

    # -- steps

    def yoficate(self, text: str) -> str:
        """Safe ё-fication with eyo if installed (silero also restores ё)."""
        if not self.yo_tool:
            return text
        try:
            res = subprocess.run(
                [str(self.yo_tool), "--stdin"], input=text, capture_output=True, text=True, timeout=60, check=True
            )
        except (subprocess.SubprocessError, OSError) as exc:
            log.warning("eyo failed, skipping ё-fication: %s", exc)
            return text
        out = res.stdout.rstrip("\n")
        return out if norm(out) == norm(text) else text

    def apply_glossary(self, text: str, scene_id: str) -> tuple[str, list[str]]:
        """Replace Latin terms by Cyrillic respellings (longest first)."""
        pairs = sorted(((s, t) for t in self.glossary.terms for s in t.src), key=lambda p: len(p[0]), reverse=True)
        hits: list[str] = []
        for src, term in pairs:
            pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(src)}(?![A-Za-z0-9])")
            if pattern.search(text):
                text = pattern.sub(term.say, text)
                hits.append(src)
        return text, hits

    def stress(self, text: str) -> list[str]:
        """Model-stressed tokens aligned 1:1 with WORD_RE tokens of text."""
        tokens = WORD_RE.findall(text)
        if not self.accentor:
            return [strip_marks(t) for t in tokens]
        stressed = self.accentor(strip_marks(text))
        model_tokens = WORD_RE.findall(stressed)
        if len(model_tokens) == len(tokens) and all(norm(a) == norm(b) for a, b in zip(tokens, model_tokens)):
            return model_tokens
        log.warning("stress model changed tokenisation; falling back to word-by-word stressing")
        return [self.accentor(strip_marks(t)) for t in tokens]

    def resolve_word(
        self, idx: int, src_tok: str, model_tok: str, context: list[tuple[int, str]], scene_id: str
    ) -> str:
        """Choose the final '+'-marked form of one token and record a review row."""
        bare = strip_marks(src_tok)
        key = bare.lower()
        flags: list[str] = []
        origin = "model"
        final = model_tok

        glossary_tok = norm(bare) in self.locked and ("+" not in src_tok or src_tok.lower() == self.locked[norm(bare)][0].lower())
        if "+" in src_tok and not glossary_tok:
            final, origin = src_tok, "manual"
        elif glossary_tok:
            tmpl, term = self.locked[norm(bare)]
            final = src_tok if "+" in src_tok else match_case(tmpl, bare)
            origin = "glossary:term"
            if term.status != "ok":
                flags.append(f"{term.status}: {term.note}" if term.note else term.status)
        elif norm(key) in self.exact or any(norm(key).startswith(norm(r.key)) for r in self.stems):
            rule = self.exact.get(norm(key)) or next(r for r in self.stems if norm(key).startswith(norm(r.key)))
            final = rule.say + (key[len(rule.key) :] if rule.prefix else "")
            final, origin = match_case(final, bare), "glossary:word"
            if rule.status != "ok":
                flags.append(f"{rule.status}: {rule.note}" if rule.note else rule.status)
        elif key in self.homo_forms or key.replace("ё", "е") in self.homo_forms:
            final, origin, flags = self.resolve_homograph(key, bare, model_tok, idx, context)
        else:
            if "ё" in bare.lower():
                # Author's ё is authoritative.
                final = bare
            if "+" not in final and "ё" not in final.lower() and vowel_count(final) >= 2:
                flags.append("unknown: model gave no stress")
            if "ё" in final.lower() and "ё" not in bare.lower():
                origin = "model+ё"

        final = cleanup(final)
        if "+" in final or "ё" in final.lower() or flags:
            self.rows.append(ReviewRow(scene_id, bare, to_acute(final), origin, flags))
        return final

    def resolve_homograph(
        self, key: str, bare: str, model_tok: str, idx: int, context: list[tuple[int, str]]
    ) -> tuple[str, str, list[str]]:
        """Pick a homograph variant by context hints, default sense or model."""
        form_key = key if key in self.homo_forms else key.replace("ё", "е")
        group = self.homo_forms[form_key]
        variants = group.forms[form_key]
        if "ё" in key:
            authored = [s for s, v in variants.items() if strip_marks(v).lower() == key]
            if authored:
                return bare, "homograph:author-ё", [f"homograph {group.name}: {authored[0]} (author ё)"]
        scores: dict[str, float] = {s: 0.0 for s in group.senses}
        evidence: dict[str, list[str]] = {s: [] for s in group.senses}
        for pos, word in context:
            if pos == idx:
                continue
            w = norm(word)
            for sense, hints in group.senses.items():
                for hint in hints:
                    h = hint.replace("ё", "е")
                    hit = w.endswith(h[1:]) if h.startswith("-") else (w == h if len(h) <= 3 else w.startswith(h))
                    if hit:
                        scores[sense] += 1.0 / abs(pos - idx)
                        evidence[sense].append(word)
                        break
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        hint_sense = (
            ranked[0][0]
            if ranked and ranked[0][1] > 0 and (len(ranked) == 1 or ranked[0][1] > ranked[1][1])
            else None
        )
        # The model has an opinion only if it marked stress / ё and the result is one of our variants.
        model_clean = cleanup(model_tok).lower()
        model_sense = next(
            (s for s, v in variants.items() if ("+" in model_tok or "ё" in model_tok.lower()) and cleanup(v).lower() == model_clean),
            None,
        )
        if model_sense:
            if hint_sense and hint_sense != model_sense:
                hint_v = to_acute(variants.get(hint_sense, ""))
                flag = f"homograph {group.name}: model {model_sense}, hints «{', '.join(evidence[hint_sense])}» suggest {hint_v} – decide"
            else:
                flag = f"homograph {group.name}: model {model_sense} – check"
            return model_tok, f"homograph:model-{model_sense}", [flag]
        if hint_sense and hint_sense in variants:
            flag = f"homograph {group.name}: {hint_sense} by hint «{', '.join(evidence[hint_sense])}» – check"
            return match_case(variants[hint_sense], bare), f"homograph:{hint_sense}", [flag]
        if group.default and group.default in variants:
            flag = f"homograph {group.name}: default {group.default} – check"
            return match_case(variants[group.default], bare), f"homograph:{group.default}", [flag]
        options = " / ".join(to_acute(v) for v in variants.values())
        return model_tok, "model", [f"homograph {group.name}: AMBIGUOUS ({options}) – decide"]

    # -- scene

    def prepare_scene(self, scene: Scene, mode: str) -> SceneResult:
        """Run the full pipeline on one scene."""
        base = acute_to_plus(scene.tts or scene.text)
        base = self.yoficate(base)
        text, _ = self.apply_glossary(base, scene.id)
        for latin in LATIN_RE.findall(text):
            self.rows.append(ReviewRow(scene.id, latin, latin, "latin", ["latin not in glossary – add a term"]))
        for abbr in ABBR_RE.findall(text):
            if norm(abbr) not in self.locked:
                self.rows.append(ReviewRow(scene.id, abbr, abbr, "abbreviation", ["Cyrillic caps – check: word or letters?"]))
        for num in DIGIT_RE.findall(text):
            self.rows.append(ReviewRow(scene.id, num, num, "number", ["digits – write in words if case matters"]))
        tokens = WORD_RE.findall(text)
        model_tokens = self.stress(text)

        # Sentence-local context for homograph hints.
        sentence_of: list[int] = []
        sent, last_end = 0, 0
        for m in WORD_RE.finditer(text):
            if SENTENCE_END_RE.search(text[last_end : m.start()]):
                sent += 1
            sentence_of.append(sent)
            last_end = m.end()

        finals: list[str] = []
        for i, (src_tok, model_tok) in enumerate(zip(tokens, model_tokens)):
            context = [
                (j, tokens[j]) for j in range(max(0, i - 6), min(len(tokens), i + 7)) if sentence_of[j] == sentence_of[i]
            ]
            finals.append(self.resolve_word(i, src_tok, model_tok, context, scene.id))
        it = iter(finals)
        plus_text = WORD_RE.sub(lambda _m: next(it), text)
        out = render(plus_text, mode)
        return SceneResult(id=scene.id, text=scene.text, tts=out, tts_plus=plus_text, chars=len(out))


def build_accentor(enabled: bool, yo_homo: bool) -> Callable[[str], str] | None:
    """Load silero-stress with marks on one-vowel words disabled."""
    if not enabled:
        return None
    try:
        from silero_stress import load_accentor
    except ImportError:
        raise SystemExit("silero-stress is not installed: run scripts/setup.sh and use its venv python")
    model = load_accentor()

    def accent(text: str) -> str:
        return model(text, stress_single_vowel=False, put_yo_homo=yo_homo)

    return accent


def find_eyo(mode: str) -> Path | None:
    """Locate the eyo CLI (setup.sh installs it into RU_VOICEOVER_HOME/node)."""
    if mode == "off":
        return None
    local = DEFAULT_HOME / "node" / "node_modules" / ".bin" / "eyo"
    if local.exists():
        return local
    found = shutil.which("eyo")
    if found:
        return Path(found)
    if mode == "on":
        raise SystemExit("eyo not found: run scripts/setup.sh or pass --yo off")
    return None


def review_markdown(rows: list[ReviewRow]) -> str:
    """Review table: every marked word, flagged rows first marker «!!»."""
    lines = ["| # | scene | word | TTS | origin | flags |", "|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, start=1):
        mark = "!! " if r.flags else ""
        lines.append(f"| {i} | {r.scene} | {mark}{r.word} | {r.tts} | {r.origin} | {'; '.join(r.flags)} |")
    return "\n".join(lines)


def main() -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="text/markdown/JSON file, or '-' for stdin")
    ap.add_argument("--scene", help="comma-separated scene ids or 1-based numbers")
    ap.add_argument("--glossary", type=Path, default=DEFAULT_GLOSSARY)
    ap.add_argument("--homographs", type=Path, default=DEFAULT_HOMOGRAPHS)
    ap.add_argument("--mode", choices=["acute", "caps", "plus", "none"], default="acute", help="stress mark output")
    ap.add_argument("--yo", choices=["auto", "on", "off"], default="auto", help="eyo ё-fication before the model")
    ap.add_argument("--yo-homo", action="store_true", help="let silero decide е/ё homographs (все→всё); off by default")
    ap.add_argument("--no-model", action="store_true", help="skip silero-stress (glossary + homographs only)")
    ap.add_argument("--out", type=Path, help="write scenes JSON for tts.py")
    ap.add_argument("--review", type=Path, help="write review table (markdown); default: print it")
    ap.add_argument("--review-json", type=Path, help="write review rows as JSON (input for check.py)")
    ap.add_argument("--fail-on-flags", action="store_true", help="exit 3 if any row is flagged")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")

    scenes = select_scenes(load_scenes(args.source), args.scene)
    bad = [s.id for s in scenes if not ID_RE.match(s.id)]
    if bad:
        raise SystemExit(f"scene ids must match {ID_RE.pattern}: {bad}")
    prep = Preparer(
        load_glossary(args.glossary),
        load_homographs(args.homographs),
        find_eyo(args.yo),
        build_accentor(not args.no_model, args.yo_homo),
    )
    results = [prep.prepare_scene(s, args.mode) for s in scenes]

    for r in results:
        print(f"[{r.id}] {r.tts}")
    flagged = [r for r in prep.rows if r.flags]
    print(f"\nscenes={len(results)} chars={sum(r.chars for r in results)} marked_rows={len(prep.rows)} flagged={len(flagged)}")

    table = review_markdown(prep.rows)
    if args.review:
        args.review.parent.mkdir(parents=True, exist_ok=True)
        args.review.write_text(table + "\n", encoding="utf-8")
        log.info("review table: %s", args.review)
    else:
        print("\n" + table)
    if args.review_json:
        args.review_json.parent.mkdir(parents=True, exist_ok=True)
        args.review_json.write_text(json.dumps([asdict(r) for r in prep.rows], ensure_ascii=False, indent=1), "utf-8")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=1), encoding="utf-8")
        log.info("scenes JSON: %s", args.out)
    return 3 if args.fail_on_flags and flagged else 0


if __name__ == "__main__":
    sys.exit(main())
