# ru-voiceover

Скилл для Claude Code: русская озвучка через ElevenLabs с правильными ударениями.

ElevenLabs хорошо читает по-русски, но угадывает ударения, омографы и латиницу. Скилл правит текст до отправки в API и проверяет аудио после генерации.

## Что делает

- **ё-фикация** – безопасная расстановка «ё» (eyo) плюс восстановление «ё» моделью.
- **Глоссарий** – бренды и англицизмы латиницей превращаются в кириллицу с ударением (`Codex` → `Ко́декс`, `MCP` → `эм-си-пи́`).
- **Ударения** – модель silero-stress ставит ударения, в API уходит знак U+0301.
- **Омографы** – словарь `замок / мука / все–всё` с подсказками по контексту; каждое вхождение помечается на проверку.
- **Генерация** – `eleven_v4` по сценам, пропуск уже готовых MP3, проверка лимита символов, `manifest.json` с длительностями.
- **QA** – обратная транскрипция (mlx-whisper) и чек-лист прослушивания с таймкодами.
- **Голоса** – поиск русских голосов в Voice Library, шаблон промпта для Voice Design, заметки по клонированию своего голоса.

## Что нужно

- Аккаунт [ElevenLabs](https://elevenlabs.io) и API-ключ.
- Python 3.11 или 3.12 (venv создаёт `setup.sh`; ставятся `silero-stress`, `torch`, `pyyaml`).
- Необязательно: Node.js и npm (для `eyo`), `ffprobe` (длительности в манифесте), Mac на Apple Silicon (для `mlx-whisper`).

## Установка

Через skills CLI:

```bash
npx skills add https://github.com/qwwiwi/ru-voiceover --skill ru-voiceover
```

Вручную:

```bash
git clone https://github.com/qwwiwi/ru-voiceover
mkdir -p ~/.claude/skills
cp -R ru-voiceover/skills/ru-voiceover ~/.claude/skills/ru-voiceover
```

Затем один раз:

```bash
bash ~/.claude/skills/ru-voiceover/scripts/setup.sh --with-whisper   # без флага – без QA-транскрипции
export ELEVENLABS_API_KEY="..."        # или файл ~/.config/ru-voiceover/elevenlabs.key (путь меняется через ELEVENLABS_KEY_FILE)
export ELEVENLABS_VOICE_ID="YOUR_VOICE_ID"   # подставь свой voice_id
```

Ключ читается только внутри процесса, не печатается и не передаётся в аргументах.

## Как пользоваться

Попроси агента: «озвучь урок lesson.md», «подбери русский голос», «исправь ударение в слове …». Либо запусти шаги сам:

```bash
PY=~/.cache/ru-voiceover/venv/bin/python
cd ~/.claude/skills/ru-voiceover
$PY scripts/prepare.py lesson.md --out tts.json --review review.md --review-json review.json
$PY scripts/tts.py tts.json --out audio/ --dry-run      # символы и запросы, ключ не читается
$PY scripts/tts.py tts.json --out audio/ --seed 42
$PY scripts/check.py audio/manifest.json --review review.json
```

В `review.md` строки с «!!» требуют решения человека: омографы, спорные слова, латиница, цифры.

## Пример

```bash
echo "Замок на двери старого замка сломан. Claude и Codex подключат MCP." | $PY scripts/prepare.py -
```

На выходе текст для TTS: латиница заменена на кириллицу, ударения расставлены, оба «замка» помечены на проверку.

## Свой голос и свой словарь

- Голос: `--voice <voice_id>`, переменная `ELEVENLABS_VOICE_ID` или свой алиас в словаре `VOICES` в `scripts/tts.py`. Встроенный пример – алиас `pavel` (голос из Voice Library, сначала добавь его в My Voices).
- Словарь: каждое неверно прочитанное слово добавляй в `data/glossary.yaml` или `data/homographs.yaml` и перезапускай `prepare.py`.

## Ограничения

- Транскрипция не ловит неверное ударение – чек-лист прослушивания обязателен.
- Омографы в длинных фразах с двумя значениями модель и подсказки путают; ставь ударение вручную (`зам+ок`).
- Каждая перегенерация тратит символы тарифа ElevenLabs.
- Клонировать можно только собственный голос; библиотечные голоса – по условиям Voice Library.

## Лицензия

MIT. Сторонние компоненты: silero-stress (MIT), eyo (MIT).
