# Voices: library, design, clones

## Known voices

| Voice | Id | Type | Notes |
|---|---|---|---|
| Pavel – Warm Conversational Narrator | `VlCYpQzyafIQIhmvxR2w` | Voice Library (native Russian) | tts.py alias `pavel`; add it to My Voices first |
| your designed voice | `YOUR_VOICE_ID` | Voice Design | English-native by nature [doc] – recreate with «Native Russian» or Remix, compare with Russian PVCs |
| your clone | `YOUR_VOICE_ID` | IVC / PVC | подставь свой voice_id; on `eleven_multilingual_v2` we used stability 0.5, similarity 0.8, style 0, speaker boost |

## Voice Library (native Russian voices)

- «All pre-made voices and generated voices are English» [doc]; native Russian = library
  PVC voices tagged Russian.
- Filters: language → accent, category, gender, age, use case, notice period, Studio Quality.
- `voices.py search --gender female --age young --use-case narrative_story` (language=ru is fixed);
  then `voices.py add --owner <public_owner_id> --voice-id <id> --name ...` to add it to My Voices.
- UI only: search by uploaded audio – upload 30 s of a target timbre, get similar Russian voices.
- Remix works only on own voices (IVC, PVC, Voice Design) and library voices with an indefinite notice period.

## Voice Design

Format [doc]: `Native <Language>. <Gender>, <Age range>. <Quality>. Persona: … Emotion: … <timbre, pace, delivery>`.
Language in the first sentence; no «accent» when you mean intonation; no FX words
(reverb, echo, phone). Higher guidance = closer to the prompt but worse audio.
Longer preview text = more stable voice. Preview text must be Russian.

Template (`voices.py prompt` prints it with your options):

```
Native Russian, standard Moscow pronunciation. Gender-neutral, 20–25 years old adult voice. Studio quality.
Persona: friendly fennec fox mascot, curious teacher. Emotion: warm, playful, confident.
Mid-to-low pitch, soft slightly husky timbre, clear articulation, relaxed natural pace. Not childlike, not squeaky.
```

Anti-squeak: use «mid-to-low pitch», «adult», an explicit age 20–30, «soft», «husky»,
«warm», «not childlike, not squeaky». Avoid «cute», «little», «squeaky», «high-pitched»,
«tiny», «cartoon» – the docs' own «cute little squeaky mouse» example produces exactly that.

Flow: `voices.py design --prompt-file p.txt --guidance-scale <n> --out previews/` → listen →
`voices.py save --generated-voice-id <id> --name ... --description "<prompt>"`.
UI shows guidance as 30–40 %; the API `guidance_scale` scale is not verified – try the
API default first. Do 3 series × 3 previews.

## Test-paragraph protocol

Paragraph (`voices.py prompt` prints it with stress marks):

«Привет, я Миша. Замок на двери старого замка сломан. Купи муку – это не мука.
Все знают: всё будет хорошо. Сегодня начнём с хештегов: найдём залётные рилсы,
соберём базу из двухсот конкурентов и включим слежку. Claude и Codex подключат MCP
через API. Договорились?»

- Run it through prepare.py, then tts.py with 2 different seeds per candidate.
- Score 1–5 on: stress (control words: замо́к, за́мка, муку́, му́ка, все/всё, хэ́штегов, ри́лсы,
  двухсо́т, Ко́декс, эм-си-пи́), «Russianness», timbre without squeak, stability between takes.
- Candidates: current voice, voice recreated with «Native Russian», Remix «native Russian
  speaker», 2–3 Russian library PVCs (+ Remix «slightly lower and softer»).
- Budget: ~350 chars × candidates × models × 2 takes (4 × 2 × 2 ≈ 6 000 chars).

## Your own clone: IVC vs PVC

| | IVC | PVC |
|---|---|---|
| Audio | 1–2 min (over 3 min – no gain or worse) | min 30 min, best 2–3 h |
| Training | instant | 3–6 h, up to 24 h |
| Plan | all | Creator+, 1 slot on Creator |
| Verification | voice captcha | mandatory, own voice only, same mic and manner |
| On v4 | «significant upgrade» in accuracy | supported, Clone Strength slider (start at 0 %) |

Steps:
1. No new recording: compare the current IVC on `eleven_multilingual_v2` vs `eleven_v4`
   on the test paragraph with U+0301.
2. Re-record IVC: 2 min of clean lesson-style speech – biggest win on v4.
3. PVC when lessons become regular: read own lesson scripts aloud (with Claude, Codex,
   MCP, рилсы, промпт – the clone learns their pronunciation), 30–60 min, 2–3 sessions,
   same room and mic.
4. Phrases whose stress never comes out right: record the phrase yourself with correct
   stress → Voice Changer (`eleven_multilingual_sts_v2`, up to 5 min, 1 000 chars per
   minute billed) → target voice.

Recording [doc]: one voice, no noise / reverb / music; −23…−18 dB RMS, true peak −3 dB;
one speaking manner; XLR mic (AT2020, Rode NT1) via interface, pop filter, ~2 fists
distance, treated room (a blanket works); cut long pauses and «эээ»; MP3 ≥128 kbps; Russian.

## Legal limits

- PVC only from your own voice – not even with another person's consent [doc].
- Library voices only under the Voice Library Addendum and Terms of Use.
- No cloning of anyone but yourself (the voice owner).

## Voice Design notes for a mascot / character voice
- Not squeaky: always «medium-low pitch», «adult», «not high-pitched, not squeaky, not childish» in the Voice Design prompt.
- Kind and warm, «caring mentor, a little playful»; a «husky/raspy» variant can sound too harsh – ask for a kinder one.
- Start the prompt with «Native Russian speaker» (designed voices are English-native by default).

## Never judge stress on Voice Design previews
Voice Design previews are read by the design model (eleven_multilingual_ttv_v2) and bypass prepare.py/tts.py – stress is worse on them. Use previews only to judge timbre; before showing a candidate to the user, save it and re-voice the test paragraph through prepare.py → tts.py on eleven_v4. Native-Russian Voice Library voices give better stress than designed voices.

## Example: choosing a library voice

Library «Pavel – Warm Conversational Narrator» `VlCYpQzyafIQIhmvxR2w`, male middle-aged, standard accent. In our run it was chosen from 6 library voices re-voiced through the pipeline on eleven_v4 seed 7. tts.py alias `pavel`.
