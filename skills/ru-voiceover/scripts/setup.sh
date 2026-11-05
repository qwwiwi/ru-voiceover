#!/usr/bin/env bash
# Idempotent setup for the ru-voiceover skill.
#
# Creates a venv with silero-stress (+ torch) and PyYAML, optionally mlx-whisper
# for back-transcription QA, and the eyo CLI (safe yo-fication) if Node is present.
#
# Usage: setup.sh [--with-whisper] [--no-eyo]
# Env:   RU_VOICEOVER_HOME  base dir (default ~/.cache/ru-voiceover); venv lives in $RU_VOICEOVER_HOME/venv
set -euo pipefail

HOME_DIR="${RU_VOICEOVER_HOME:-$HOME/.cache/ru-voiceover}"
VENV="$HOME_DIR/venv"
WITH_WHISPER=0
WITH_EYO=1

for arg in "$@"; do
  case "$arg" in
    --with-whisper) WITH_WHISPER=1 ;;
    --no-eyo) WITH_EYO=0 ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

mkdir -p "$HOME_DIR"
# Model caches stay next to the venv, not in random home folders.
export TORCH_HOME="$HOME_DIR/torch"
export HF_HOME="$HOME_DIR/hf"

pick_python() {
  # torch wheels lag behind the newest CPython: prefer 3.12, then 3.11, then default.
  local cand
  for cand in python3.12 python3.11 python3; do
    if command -v "$cand" >/dev/null 2>&1; then
      echo "$cand"
      return 0
    fi
  done
  echo "no python3 found" >&2
  return 1
}

if [[ -x "$VENV/bin/python" ]]; then
  echo "venv exists: $VENV"
else
  PY="$(pick_python)"
  echo "creating venv with $PY at $VENV"
  if command -v uv >/dev/null 2>&1; then
    uv venv --python "$PY" "$VENV"
  else
    "$PY" -m venv "$VENV"
  fi
fi

pip_install() {
  if command -v uv >/dev/null 2>&1; then
    uv pip install --python "$VENV/bin/python" "$@"
  else
    "$VENV/bin/python" -m pip install --quiet "$@"
  fi
}

if "$VENV/bin/python" -c "import silero_stress, torch, yaml" >/dev/null 2>&1; then
  echo "silero-stress, torch, pyyaml already installed"
else
  echo "installing silero-stress, torch, pyyaml"
  pip_install silero-stress torch pyyaml
fi

if [[ "$WITH_WHISPER" == 1 ]]; then
  if [[ "$(uname -s)-$(uname -m)" != "Darwin-arm64" ]]; then
    echo "mlx-whisper needs Apple Silicon; skipping"
  elif "$VENV/bin/python" -c "import mlx_whisper" >/dev/null 2>&1; then
    echo "mlx-whisper already installed"
  else
    echo "installing mlx-whisper"
    pip_install mlx-whisper
  fi
fi

if [[ "$WITH_EYO" == 1 ]]; then
  if ! command -v npm >/dev/null 2>&1; then
    echo "npm not found; skipping eyo (silero-stress still restores ё)"
  elif [[ -x "$HOME_DIR/node/node_modules/.bin/eyo" ]]; then
    echo "eyo already installed"
  else
    echo "installing eyo into $HOME_DIR/node"
    npm install --silent --no-audit --no-fund --prefix "$HOME_DIR/node" eyo
  fi
fi

echo "warming up silero-stress model"
"$VENV/bin/python" - <<'PY'
from silero_stress import load_accentor

accentor = load_accentor()
print("silero-stress ok:", accentor("Привет, все знают этот замок."))
PY

echo "done. Run scripts with: $VENV/bin/python <skill>/scripts/prepare.py ..."
