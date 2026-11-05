---
name: ru-voiceover
description: >-
  Russian voice-over with ElevenLabs with correct stress, anglicisms and voice
  choice: ё-fication, glossary of brands/anglicisms in Cyrillic, automatic stress
  marks (silero-stress, U+0301), homograph dictionary, generation on eleven_v4,
  back-transcription QA, Voice Library search and Voice Design for Russian voices,
  notes on cloning your own voice. Use for: «озвучка», «озвучь», «озвучить урок», «голос»,
  «подбери голос», «ElevenLabs», «TTS», «ударения», «неправильное ударение»,
  «произношение», «англицизмы в озвучке», «клон голоса», voice-over, voiceover,
  text-to-speech, Russian TTS, stress marks, pronunciation dictionary. NOT for:
  writing the script itself, video rendering.
---

# ru-voiceover – Russian TTS with correct stress

ElevenLabs reads Russian well but guesses stress, homographs and Latin words.
This skill fixes the text before it reaches the API and checks the audio after.

## Pipeline

```
text ─► ё-fication (eyo, safe) ─► glossary: Latin/brands → Cyrillic with stress
     ─► silero-stress (+ marks) ─► homograph dictionary override (context hints)
     ─► cleanup (no mark on 1-vowel words, none before ё) ─► U+0301
     ─► ElevenLabs eleven_v4 ─► QA: back-transcription + human listen
     ─► every misread word goes into data/glossary.yaml or data/homographs.yaml
```

```bash
bash scripts/setup.sh [--with-whisper]          # once; venv ~/.cache/ru-voiceover/venv
PY=~/.cache/ru-voiceover/venv/bin/python
$PY scripts/prepare.py lesson.md --out tts.json --review review.md --review-json review.json
#   read review.md: every «!!» row is a homograph / user decision / unknown word
$PY scripts/tts.py tts.json --out audio/ --dry-run   # check chars + requests
$PY scripts/tts.py tts.json --out audio/ --seed 42   # checks budget, redoes only new/changed scenes, manifest.json
$PY scripts/check.py audio/manifest.json --review review.json
#   regenerate a bad scene: tts.py tts.json --out audio/ --only a1 --force --seed 7
```

Input for prepare.py: plain text (paragraph = scene), markdown script with
``### N. `id` `` headings and `**Голос:**` lines, or scenes JSON
(`[{id, text, tts?}]`). Manual stress in the source (`зам+ок` or `замо́к`) always wins.
Details of each step and data formats: `references/pipeline.md`.

## Models and settings

| Model | Use | Stress control | Notes |
|---|---|---|---|
| `eleven_v4` | **default** | U+0301 in text (37–38/40 in an outside test), IPA `/…/`, dictionary alias + phoneme | only Stability + Similarity documented; `language_code: ru` |
| `eleven_v3` | fallback | U+0301 / caps (community), dictionary alias (+ phoneme per one doc page) | stability presets 0 / 0.5 / 1 |
| `eleven_multilingual_v2` | fallback, long stable reads | **alias only** (phoneme rules skipped) | no `language_code` |
| `eleven_flash_v2` | never for Russian | – | English only |

Defaults in tts.py: `eleven_v4`, stability 0.5, server-default similarity,
`previous_text`/`next_text` on (off for v3; auto-retry without them if rejected),
mp3 44.1 kHz 128 kbps. Never send `+` to the API. Capitals add emphasis on v4,
so caps mode (`--mode caps`) is a fallback only. Full table: `references/models.md`.

## Voice choice

- **Voices:** pass any voice id with `--voice <id>` or set env `ELEVENLABS_VOICE_ID`
  (подставь свой voice_id). Built-in example alias: `--voice pavel` = Voice Library
  «Pavel – Warm Conversational Narrator» `VlCYpQzyafIQIhmvxR2w` (native Russian; add it to
  My Voices first). Add your own aliases to `VOICES` in tts.py (`"my-clone": "YOUR_VOICE_ID"`).
- **Е vs Э in loanwords:** dictionary-checked hard-Э words live in glossary `words:`;
  sources and the soft-kept list – `references/e-vs-e-loanwords.md`.
- **Voice Library:** `voices.py search --gender … --age … --use-case …` (language=ru).
  Native Russian voices are library PVCs tagged Russian. Search by uploaded audio in the UI finds similar timbres.
- **Voice Design:** prompt starts with «Native Russian …»; `voices.py prompt` prints the template.
  Pitch/age words that avoid squeak: «mid-to-low pitch», «adult», «20–25», «soft slightly husky»,
  «not childlike, not squeaky». Never «cute», «little», «squeaky», «high-pitched». Preview text in Russian.
- **Test-paragraph protocol:** same paragraph (homographs, anglicisms, numbers, a question),
  2 takes with different seeds, score stress / «Russianness» / timbre / stability.
- **Your own clone:** move IVC to `eleven_v4` first (big IVC upgrade there); PVC = 30 min–3 h of clean
  lesson-style Russian, 1 slot on Creator. Words that never come out right: record the phrase
  yourself → Voice Changer (STS) into the target voice.
Details, prompt, recording requirements and legal limits: `references/voices.md`.

## QA loop

check.py transcribes with mlx-whisper (skips with a note if missing) and flags
missing / extra / misread words. **Transcription does not catch wrong stress** –
the listening checklist (homographs, glossary, flagged words with timestamps) is
mandatory. Bad scene → new seed → IPA for that word (v4) → Voice Changer. Each
confirmed error → add a rule to `data/` and re-run prepare.py. See `references/qa.md`.

## Security

- Key: env `ELEVENLABS_API_KEY`, or a key file – `~/.config/ru-voiceover/elevenlabs.key`
  by default, override with env `ELEVENLABS_KEY_FILE`. Scripts read it in-process only.
- Never print, log, commit or pass the key in argv; never paste key fragments into
  chats or agent memory. Error messages show status + body only.
- `--dry-run` on tts.py, dict.py, voices.py never reads the key.

## Cost

Characters are limited by your ElevenLabs plan. Every regeneration costs again. Before a
batch: `tts.py --budget` (GET `/v1/user/subscription`); tts.py also refuses to
start if the batch exceeds the remaining characters. Existing MP3s are skipped
unless `--force`. A/B of 4 voices × 2 models × 2 takes ≈ 6 000 characters.

## Red lines

- No Latin in TTS text: everything goes through the glossary (subtitles keep the original).
- Owner-decision words (`status: needs_owner_decision` – e.g. хештег) are not
  finalised by an agent; ask the user, then change the status.
- Library voices: only under the Voice Library terms; no cloning of anyone but the voice owner (yourself).
- Do not hand over audio without the listening checklist done.

## Feedback loop

Every pronunciation / voice correction from the user goes into `data/glossary.yaml`
(or `data/homographs.yaml`) the same day, with a short `note`.
