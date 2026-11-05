#!/usr/bin/env python3
"""Create or update an ElevenLabs pronunciation dictionary from glossary.yaml.

Rules: every glossary term spelling -> alias with U+0301 stress (works on all
models). With --phoneme, terms that have `ipa` become IPA phoneme rules instead
(only eleven_v4 applies phoneme rules to Russian; untested for our voices).
With --include-words, Cyrillic word rules (exact forms, not stems) become aliases too.

The dictionary is insurance for Studio / UI work and for text that skipped
prepare.py; in the scripted pipeline prepare.py already respells everything.

Usage:
    dict.py --dry-run                         # print rules, no key, no API calls
    dict.py --export-pls ru-voiceover.pls      # offline PLS file (Studio upload)
    dict.py --name ru-voiceover                # create, prints id + version_id
    dict.py --id <dictionary_id>               # upsert rules into an existing dictionary
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from el_client import Client, load_key
from prepare import DEFAULT_GLOSSARY, load_glossary, to_acute

log = logging.getLogger("dict")


def build_rules(glossary_path: Path, phoneme: bool, include_words: bool) -> list[dict[str, object]]:
    """Turn glossary entries into ElevenLabs rule objects.

    Args:
        glossary_path: glossary.yaml path.
        phoneme: Use IPA phoneme rules where `ipa` is set.
        include_words: Add exact Cyrillic word rules as aliases.

    Returns:
        Rule dicts for add-from-rules / add-rules.
    """
    glossary = load_glossary(glossary_path)
    rules: list[dict[str, object]] = []
    seen: set[str] = set()
    for term in glossary.terms:
        for src in term.src:
            if src in seen:
                continue
            seen.add(src)
            if phoneme and term.ipa:
                rule: dict[str, object] = {
                    "type": "phoneme",
                    "string_to_replace": src,
                    "phoneme": term.ipa,
                    "alphabet": "ipa",
                }
            else:
                rule = {"type": "alias", "string_to_replace": src, "alias": to_acute(term.say)}
            rule.update({"case_sensitive": True, "word_boundaries": True})
            rules.append(rule)
    if include_words:
        for w in glossary.words:
            if w.prefix or w.key in seen:
                continue
            seen.add(w.key)
            rules.append(
                {
                    "type": "alias",
                    "string_to_replace": w.key,
                    "alias": to_acute(w.say),
                    "case_sensitive": False,
                    "word_boundaries": True,
                }
            )
    return rules


def to_pls(rules: list[dict[str, object]]) -> str:
    """Render rules as a W3C PLS lexicon (alias -> <alias>, phoneme -> <phoneme>)."""
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<lexicon version="1.0" xmlns="http://www.w3.org/2005/01/pronunciation-lexicon"',
        '         alphabet="ipa" xml:lang="ru-RU">',
    ]
    for r in rules:
        grapheme = escape(str(r["string_to_replace"]))
        if r["type"] == "phoneme":
            body = f"<phoneme>{escape(str(r['phoneme']))}</phoneme>"
        else:
            body = f"<alias>{escape(str(r['alias']))}</alias>"
        lines.append(f"  <lexeme><grapheme>{grapheme}</grapheme>{body}</lexeme>")
    lines.append("</lexicon>")
    return "\n".join(lines) + "\n"


def main() -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--glossary", type=Path, default=DEFAULT_GLOSSARY)
    ap.add_argument("--name", default="ru-voiceover", help="dictionary name for create")
    ap.add_argument("--id", help="existing dictionary id: upsert rules (add-rules)")
    ap.add_argument("--phoneme", action="store_true", help="IPA phoneme rules where available (eleven_v4 only)")
    ap.add_argument("--include-words", action="store_true", help="also add exact Cyrillic word forms as aliases")
    ap.add_argument("--export-pls", type=Path, help="write a .pls file and exit (offline)")
    ap.add_argument("--dry-run", action="store_true", help="print rules, no key, no API calls")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    rules = build_rules(args.glossary, args.phoneme, args.include_words)
    if args.export_pls:
        args.export_pls.write_text(to_pls(rules), encoding="utf-8")
        print(f"pls: {args.export_pls} rules={len(rules)}")
        return 0
    if args.dry_run:
        action = f"POST /v1/pronunciation-dictionaries/{args.id}/add-rules" if args.id else (
            "POST /v1/pronunciation-dictionaries/add-from-rules"
        )
        print(f"{action} rules={len(rules)}")
        for r in rules:
            print("  " + json.dumps(r, ensure_ascii=False))
        print("dry-run: no key read, no API calls")
        return 0

    client = Client(load_key())
    if args.id:
        res = client.json("POST", f"/v1/pronunciation-dictionaries/{args.id}/add-rules", {"rules": rules})
    else:
        body = {"name": args.name, "rules": rules, "description": "ru-voiceover glossary (Cyrillic stress aliases)"}
        res = client.json("POST", "/v1/pronunciation-dictionaries/add-from-rules", body)
    dict_id = res.get("id", args.id)
    version = res.get("version_id", "")
    print(f"id={dict_id} version_id={version} rules={len(rules)}")
    print(f"use: tts.py ... --dict {dict_id}:{version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
