#!/usr/bin/env python3
"""Find and design Russian voices in ElevenLabs.

Subcommands:
    search  – Voice Library (shared voices), language=ru, filters by gender/age/use case/accent.
    add     – add a library voice to My Voices (needed before using some library voices via API).
    prompt  – print a Voice Design prompt from the «Native Russian …» template (offline).
    design  – generate Voice Design previews from a prompt; saves MP3s + previews.json.
    save    – turn a chosen preview (generated_voice_id) into a permanent voice.

Every subcommand supports --dry-run (prints the request; no key read, no API calls).

Usage:
    voices.py search --gender female --age young --use-case narrative_story --dry-run
    voices.py prompt --gender neutral --age "20–25" --persona "friendly fennec fox mascot, curious teacher"
    voices.py design --prompt-file p.txt --out previews/ --dry-run
    voices.py save --generated-voice-id ID --name "My RU voice v2" --description "..." --dry-run
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import re
import sys
import urllib.parse
from pathlib import Path

from el_client import Client, load_key

log = logging.getLogger("voices")

ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# The research paragraph: homographs, anglicisms, numbers, a question.
TEST_PARAGRAPH = (
    "Привет, я Ми́ша. Замо́к на двери́ ста́рого за́мка сло́ман. Купи́ муку́ – э́то не му́ка. "
    "Все зна́ют: всё бу́дет хорошо́. Сего́дня начнём с хэ́штегов: найдём залётные ри́лсы, "
    "соберём ба́зу из двухсо́т конкуре́нтов и включи́м сле́жку. Клод и Ко́декс подключа́т "
    "эм-си-пи́ че́рез эй-пи-а́й. Договори́лись?"
)

PROMPT_TEMPLATE = (
    "Native Russian, standard Moscow pronunciation. {gender_age}. Studio quality.\n"
    "Persona: {persona}. Emotion: {emotion}.\n"
    "{pitch}, {timbre}, clear articulation, {pace}. Not childlike, not squeaky."
)


def build_prompt(args: argparse.Namespace) -> str:
    """Fill the Voice Design template (language first, pitch words that avoid squeak)."""
    gender = {"male": "Male", "female": "Female", "neutral": "Gender-neutral"}[args.gender]
    return PROMPT_TEMPLATE.format(
        gender_age=f"{gender}, {args.age} years old adult voice",
        persona=args.persona,
        emotion=args.emotion,
        pitch=args.pitch,
        timbre=args.timbre,
        pace=args.pace,
    )


def run(client: Client | None, method: str, path: str, body: object | None, dry: bool) -> dict[str, object]:
    """Print the request on dry-run, else call the API."""
    if dry or client is None:
        print(f"{method} {path}")
        if body is not None:
            shown = json.dumps(body, ensure_ascii=False)
            print("  " + (shown[:600] + "…" if len(shown) > 600 else shown))
        print("dry-run: no key read, no API calls")
        return {}
    return client.json(method, path, body)


def cmd_search(args: argparse.Namespace, client: Client | None) -> None:
    """Search the Voice Library for Russian voices."""
    params: dict[str, str | int] = {"page_size": args.page_size, "language": "ru", "sort": args.sort}
    for key, val in (
        ("gender", args.gender),
        ("age", args.age),
        ("use_cases", args.use_case),
        ("accent", args.accent),
        ("search", args.query),
        ("category", args.category),
    ):
        if val:
            params[key] = val
    res = run(client, "GET", "/v1/shared-voices?" + urllib.parse.urlencode(params), None, args.dry_run)
    voices = res.get("voices", [])
    if not res or not isinstance(voices, list):
        return
    for v in voices:
        if not isinstance(v, dict):
            continue
        desc = str(v.get("description") or "").replace("\n", " ")[:80]
        print(
            f"{v.get('voice_id')} | owner {v.get('public_owner_id')} | {v.get('name')} | "
            f"{v.get('gender')} {v.get('age')} {v.get('accent')} | {v.get('use_case')} | "
            f"{v.get('category')} | clones {v.get('cloned_by_count')} | notice {v.get('notice_period')} | {desc}"
        )
    print(f"found={len(voices)} (add with: voices.py add --owner <public_owner_id> --voice-id <id> --name ...)")


def cmd_add(args: argparse.Namespace, client: Client | None) -> None:
    """Add a shared voice to My Voices."""
    res = run(client, "POST", f"/v1/voices/add/{args.owner}/{args.voice_id}", {"new_name": args.name}, args.dry_run)
    if res:
        print(f"voice_id={res.get('voice_id')}")


def cmd_design(args: argparse.Namespace, client: Client | None) -> None:
    """Generate Voice Design previews and save them."""
    prompt = Path(args.prompt_file).read_text(encoding="utf-8").strip() if args.prompt_file else args.prompt
    if not prompt:
        raise SystemExit("give --prompt or --prompt-file (see: voices.py prompt)")
    text = args.text or TEST_PARAGRAPH
    if not 20 <= len(prompt) <= 1000:
        raise SystemExit(f"voice_description must be 20–1000 chars, got {len(prompt)}")
    if not 100 <= len(text) <= 1000:
        raise SystemExit(f"preview text must be 100–1000 chars, got {len(text)}")
    body: dict[str, object] = {
        "voice_description": prompt,
        "model_id": args.model,
        "text": text,
    }
    if args.guidance_scale is not None:
        body["guidance_scale"] = args.guidance_scale
    if args.seed is not None:
        body["seed"] = args.seed
    res = run(client, "POST", "/v1/text-to-voice/design", body, args.dry_run)
    previews = res.get("previews", [])
    if not isinstance(previews, list) or not previews:
        return
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    meta = []
    for n, p in enumerate(previews, start=1):
        if not isinstance(p, dict):
            continue
        gid = str(p.get("generated_voice_id", ""))
        if not ID_RE.match(gid):
            log.warning("preview %d: unexpected generated_voice_id, skipped", n)
            continue
        file = out / f"preview-{n}-{gid}.mp3"
        file.write_bytes(base64.b64decode(str(p.get("audio_base_64", ""))))
        meta.append({"n": n, "generated_voice_id": gid, "file": file.name, "duration_s": p.get("duration_secs")})
        print(f"preview {n}: {file} generated_voice_id={gid}")
    (out / "previews.json").write_text(
        json.dumps({"prompt": prompt, "text": body["text"], "previews": meta}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print("listen, pick one, then: voices.py save --generated-voice-id <id> --name ... --description ...")


def cmd_save(args: argparse.Namespace, client: Client | None) -> None:
    """Create a permanent voice from a preview."""
    body = {"voice_name": args.name, "voice_description": args.description, "generated_voice_id": args.generated_voice_id}
    res = run(client, "POST", "/v1/text-to-voice", body, args.dry_run)
    if res:
        print(f"voice_id={res.get('voice_id')} name={res.get('name')}")


def main() -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print the request; no key, no API calls")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="Voice Library search (language=ru)")
    s.add_argument("--gender", choices=["male", "female", "neutral"])
    s.add_argument("--age", choices=["young", "middle_aged", "old"])
    s.add_argument("--use-case", help="e.g. narrative_story, conversational, social_media, informative_educational")
    s.add_argument("--accent", help="e.g. standard, moscow")
    s.add_argument("--category", help="professional | high_quality | famous ...")
    s.add_argument("--query", help="free text search")
    s.add_argument("--sort", default="trending", help="trending | cloned_by_count | created_date ...")
    s.add_argument("--page-size", type=int, default=30)
    s.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS)

    a = sub.add_parser("add", help="add a library voice to My Voices")
    a.add_argument("--owner", required=True, help="public_owner_id from search")
    a.add_argument("--voice-id", required=True)
    a.add_argument("--name", required=True)
    a.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS)

    p = sub.add_parser("prompt", help="print a Voice Design prompt (offline)")
    p.add_argument("--gender", choices=["male", "female", "neutral"], default="neutral")
    p.add_argument("--age", default="20–25")
    p.add_argument("--persona", default="friendly fennec fox mascot, curious teacher")
    p.add_argument("--emotion", default="warm, playful, confident")
    p.add_argument("--pitch", default="Mid-to-low pitch")
    p.add_argument("--timbre", default="soft slightly husky timbre")
    p.add_argument("--pace", default="relaxed natural pace")

    d = sub.add_parser("design", help="Voice Design previews")
    d.add_argument("--prompt")
    d.add_argument("--prompt-file")
    d.add_argument("--text", help="preview text in Russian (default: the stress test paragraph)")
    d.add_argument("--model", default="eleven_ttv_v3", help="eleven_ttv_v3 | eleven_multilingual_ttv_v2")
    d.add_argument("--guidance-scale", type=float, help="0–100, API default 5; higher = closer to prompt, worse audio")
    d.add_argument("--seed", type=int)
    d.add_argument("--out", default="voice-previews")
    d.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS)

    v = sub.add_parser("save", help="save a preview as a voice")
    v.add_argument("--generated-voice-id", required=True)
    v.add_argument("--name", required=True)
    v.add_argument("--description", required=True, help="usually the design prompt")
    v.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS)

    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.cmd == "prompt":
        print(build_prompt(args))
        print("\npreview text (Russian, not English):\n" + TEST_PARAGRAPH)
        return 0
    client = None if args.dry_run else Client(load_key())
    {"search": cmd_search, "add": cmd_add, "design": cmd_design, "save": cmd_save}[args.cmd](args, client)
    return 0


if __name__ == "__main__":
    sys.exit(main())
