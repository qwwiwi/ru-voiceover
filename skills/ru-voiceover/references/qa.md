# QA: back-transcription, listening, dictionary loop

## What each check catches

| Check | Catches | Misses |
|---|---|---|
| prepare.py review (`!!` rows) | homographs, owner decisions, unknown words, Latin, digits, caps abbreviations | anything the model stressed confidently but wrongly |
| check.py (mlx-whisper) | skipped / repeated / swallowed words, Latin read letter by letter, garbage | **wrong stress** (same letters) |
| Human listening checklist | stress, intonation, accent, squeak | – |

## check.py

```bash
$PY scripts/check.py audio/manifest.json --review review.json --report qa.md
$PY scripts/check.py --audio take.mp3 --text "..." --review review.json
```

- Default model `mlx-community/whisper-large-v3-turbo` (Apple Silicon; install with
  `setup.sh --with-whisper`; first run downloads the model). Without mlx-whisper it
  prints «skipped – listen manually» and still prints the checklist.
- Compares normalised words (Latin terms mapped via the glossary, ё/э → е, marks
  removed); near-identical words (ratio ≥ 0.8) count as equal. Digits vs words are
  reported as `(number?)` – judge by ear.
- Listening checklist: every homograph / glossary / manual / flagged word from the
  review JSON with the timestamp where whisper heard it (`?:??` if not found).
- `--fail-on-diff` exits 1 on any word issue.
- Alternative without Apple Silicon: Groq `whisper-large-v3` (OpenAI-compatible
  `/audio/transcriptions`, `language=ru`) – not wired in; key handling rules as for ElevenLabs.

## Escalation for a bad word

1. Regenerate the scene with another seed: `tts.py tts.json --out audio/ --only <id> --force --seed <n>`.
2. Force the variant in the source with a manual mark (`з+амок`) or a respelling, re-run prepare.py.
3. eleven_v4: IPA for that one word in the TTS text, in quotes: `"/zɐˈmok/"`.
4. The user records the phrase → Voice Changer into the target voice.

## Feeding errors back

- Wrong stress on an ordinary word → `glossary.yaml → words` (exact form, or `stem*`
  if the stress never moves).
- Homograph guessed wrong → add hints or forms to `homographs.yaml`; a frequent sense → `default`.
- Latin term → `glossary.yaml → terms` with all spellings in `src`.
- Re-run prepare.py on the whole lesson to see the effect; keep `status: needs_owner_decision`
  until the user answers.
- If the same dictionary is used in Studio/UI: `dict.py --id <id>` (upsert) or
  `dict.py --export-pls file.pls` for manual upload; pass `--dict id:version` to tts.py.

## Before handing over audio

- [ ] review.md has no unresolved `AMBIGUOUS` / `needs_owner_decision` rows (or the user decided)
- [ ] check.py: no missing / extra words
- [ ] every checklist item listened to
- [ ] manifest.json durations match the expected timing of the video
