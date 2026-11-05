# Pipeline details: prepare.py and the data files

## Steps (what prepare.py does)

1. **Load scenes.** Plain text → one scene per paragraph (`p1`, `p2`…). Markdown →
   scenes from `**Голос:**` / `**Voice:**` lines, id from the preceding
   ``### N. `id` `` heading. JSON → `[{id, text, tts?}]` or `{scenes|segments: [...]}`;
   if `tts` is present it is used instead of `text`. `--scene 4`
   or `--scene a1,a2` picks scenes by number or id. Ids must match `^[A-Za-z0-9_-]+$`.
2. **Manual marks.** U+0301 in the source is converted to the internal `+` form.
   A word with a manual mark is never touched by later steps (origin `manual`).
3. **ё-fication.** `eyo --stdin` (safe replacements only) if installed by setup.sh;
   silero-stress restores ё as well. The author's ё is authoritative.
4. **Glossary terms.** `data/glossary.yaml → terms`: whole-word, case-sensitive,
   longest first: `Codex` → `К+одекс`. All tokens produced by the glossary keep the
   glossary's stress (the model is not allowed to re-stress `Ко́декс`).
5. **silero-stress.** `stress_single_vowel=False`, `put_yo_homo=False` (the е/ё
   homographs such as все/всё are decided by our dictionary – silero alone turns
   «все знают» into «всё знают»). Pass `--yo-homo` to let silero decide.
6. **Word rules.** `glossary.yaml → words`: Cyrillic forms with forced stress;
   `stem*` keys match any ending (`рилс*` → ри́лсы, ри́лсов) – only for words whose
   stress never moves.
7. **Homographs.** `data/homographs.yaml`: every occurrence is flagged. The sense
   is picked by hints in the same sentence (±6 words, closer = heavier); tie → the
   group's `default`; no default → model choice flagged `AMBIGUOUS – decide`.
8. **Cleanup.** Marks removed from one-vowel words and before ё (ё is always stressed).
   Hyphenated respellings count as one word (`эм-си-пи́` keeps its mark).
9. **Render.** `--mode acute` (default, U+0301 after the vowel) | `caps` (stressed
   vowel uppercase – adds emphasis on v4) | `plus` | `none`.
10. **Review.** Table of every marked word: scene, source word, TTS form, origin
    (`model`, `model+ё`, `glossary:term`, `glossary:word`, `homograph:<sense>`, `manual`),
    flags. `!!` rows need a human: homographs, `needs_owner_decision`, `check_by_ear`,
    `unknown: model gave no stress`, Latin left over, digits, Cyrillic caps abbreviations.
    `--fail-on-flags` exits 3 when anything is flagged (for agents in a loop).

Outputs: stdout (TTS text per scene + summary), `--out tts.json` (input for tts.py:
`id, text, tts, tts_plus, chars`), `--review review.md`, `--review-json review.json`
(input for check.py's listening checklist).

## glossary.yaml

```yaml
terms:                       # Latin/brand -> spoken Cyrillic, before the model
  - src: [GitHub, Github]    # every spelling that appears in scripts
    say: Гитх+аб             # '+' before the stressed vowel (or U+0301 after it)
    ipa: ...                 # optional, dict.py --phoneme only (eleven_v4)
    status: ok               # ok | check_by_ear | needs_owner_decision
    note: why / alternatives
words:                       # Cyrillic forms -> forced stress, after the model
  двести: дв+ести
  рилс*: р+илс*              # stem rule
  хештег*: {say: х+эштег*, status: needs_owner_decision, note: ...}
```

prepare.py refuses a `+` that is not directly before a vowel (typo guard).

Rules for new terms:
- Respell as Russians say it, in Cyrillic, with stress: `Ко́декс`, `Гитха́б`.
- Abbreviations by letters with stress on the last letter: `эм-си-пи́`, `эй-пи-а́й`.
- A Latin term does not decline; if the sentence needs a case («к Клоду», «в ри́лсах»),
  write that sentence in Cyrillic in the script's TTS text.
- On screen and in subtitles keep the original (Claude, MCP); only the TTS text changes.

## homographs.yaml

```yaml
groups:
  - group: замок
    default: null            # optional sense used when hints do not decide
    senses:
      lock: [двер, ключ, -ть]   # prefixes; 1–3 letters = whole word; '-x' = suffix
      castle: [стар, рыцар]
    forms:
      замок: {lock: зам+ок, castle: з+амок}
      замка: {lock: замк+а, castle: з+амка}
```

Hints are heuristics; long sentences with two senses («Замок на двери старого
замка сломан») still go wrong – that is why every homograph is flagged. Force the
variant in the source text with a manual mark when the hint guess is wrong.

## Stress tools (research summary)

| Tool | License | Output | Homographs | Verdict |
|---|---|---|---|---|
| silero-stress 1.5 | MIT | `+` before vowel, ё | ~2.2k, F1 0.92 | **used**; torch only |
| RUAccent turbo3.1 | MIT / Apache-2.0 | `+` | yes | good second; onnxruntime, transformers<5 |
| eyo (e2yo) | MIT | ё only | safe dictionary | **used** for safe ё (Node) |
| StressRNN, russtress | – | `'` | no | outdated |
| omogre | CC-BY-NC-SA | + IPA | yes | non-commercial – do not use |

Both models get 9/13 on our homograph set and fail on long multi-homograph
sentences: the model covers 95%+ of ordinary words; homographs need the dictionary
and a human ear.
