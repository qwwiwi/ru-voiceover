# ElevenLabs models, settings and stress methods

Labels: [doc] official docs · [test] our local run · [measure] outside measurement · [community] single report · [guess] our inference, unverified.

## Models

| Model | Russian | IPA / phoneme | Dict alias | Dict phoneme | Notes |
|---|---|---|---|---|---|
| `eleven_v4` | yes, 90+ langs, 10 000 chars/request | IPA in text `/zɐˈmok/` with `ˈ ˌ` | yes | yes, also non-English | only Stability + Similarity; no Style/Speed/SSML in UI; audio tags [doc] |
| `eleven_v4_turbo` | yes | not stated | yes | not stated | realtime ~100 ms |
| `eleven_v3` | yes, 70+ | not documented | yes | yes (one doc page) | previous gen; no break tags |
| `eleven_multilingual_v2` | yes, 29 | no phoneme tags | yes | **skipped** | most stable on long text; `language_code` not supported |
| `eleven_flash_v2_5` | yes, 32 | no | yes | skipped | normalisation off by default |
| `eleven_flash_v2` | **English only** | SSML phoneme | yes | yes | not for Russian |

Doc quotes [doc]: «Pronunciation dictionary phoneme tags only work with eleven_v4,
eleven_flash_v2 and eleven_v3 … If you want to use IPA and CMU pronunciations in
languages other than English, you will have to switch to the eleven_v4 model.»
Another page says v3 or v4 – conflicting, treat phoneme on v3 as unverified.

Dictionaries: up to 3 per request, case-sensitive matching, first match wins,
rules `alias` / `phoneme` with `case_sensitive` and `word_boundaries` flags.

## Stress methods

| Method | Example | Models | Status |
|---|---|---|---|
| U+0301 after the vowel | `замо́к` | v4 | [measure] 93–95% for native Russian voices, poor for a weak voice; sometimes a word gets «swallowed» (lymi PR #864) |
| Capital stressed vowel | `замОк` | v3 | [community] works, not spelled out; on v4 capitals add emphasis [doc] |
| IPA in text | `"/zɐˈmok/"` | v4 only | [doc]; without quotes 7/10 clips broke in the lymi test |
| Dictionary phoneme | IPA rule | v4 (ru) | [doc] |
| Dictionary alias | `Codex → Ко́декс` | all | [doc]; U+0301 inside alias – [guess], verify |
| ё instead of е | `всё` | all | reliable |
| `+` before vowel | `зам+ок` | – | no evidence ElevenLabs understands it – never send |
| Phonetic respelling | `хэ́штег` | all | [doc] «alternative spelling» |
| Voice Changer (STS) | say it yourself | `eleven_multilingual_sts_v2` | [doc] «fixing pronunciations», full control |

## tts.py flags → API

| Flag | Body field | Default | Notes |
|---|---|---|---|
| `--model` | `model_id` | `eleven_v4` | |
| `--voice` | path | `$ELEVENLABS_VOICE_ID` or `pavel` | alias `pavel` (library voice), your own aliases in `VOICES`, or any voice id |
| `--stability` | `voice_settings.stability` | 0.5 | higher = steadier, lower = livelier; v3 presets 0/0.5/1 |
| `--similarity` | `similarity_boost` | server default | an older pipeline of ours used 0.8 |
| `--style`, `--speed`, `--speaker-boost` | voice_settings | omitted | not documented for v4 – warning |
| `--language-code` | `language_code` | `ru` | dropped automatically for multilingual_v2 |
| `--seed` | `seed` | none | determinism not guaranteed |
| `--dict ID[:VER]` | `pronunciation_dictionary_locators` | none | max 3; create with dict.py |
| `--context` | `previous_text` / `next_text` | auto | auto = send (off for `eleven_v3`: no request stitching per docs), retry once without on 400/422 mentioning them; v4 accepts them per the API spec example |
| `--normalization` | `apply_text_normalization` | auto | numbers/dates; write numbers in words when case matters |
| `--format` | `output_format` query | `mp3_44100_128` | |

Scene length limits per request: v4 / multilingual_v2 10 000 chars, v3 5 000 – tts.py refuses longer scenes.
Regeneration: an MP3 is redone when the scene text hash in manifest.json differs, or with `--force`.

## Known quirks

- A dash before a verb can make the voice repeat a word (seen in one of our lessons).
- Do not mix languages in one generation – «this can cause confusion» [doc]. Latin → glossary.
- Voice Design voices are English-native; on v4 they «may be less expressive» [doc].
- Normalisation reads digits, but case agreement («двухсот», «пятьюстами») is safer in words.
- `language_code` on multilingual_v2 is ignored/unsupported – sending it there does nothing.
