#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "ERROR: '$PY' not found. Install Python 3.10+ or set PYTHON=/path/to/python." >&2
  exit 1
fi
echo "==> Using $("$PY" --version)"

if [ ! -d .venv ]; then
  echo "==> Creating virtualenv in .venv"
  "$PY" -m venv .venv
else
  echo "==> Reusing existing .venv"
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> Upgrading pip and installing requirements"
python -m pip install --upgrade pip >/dev/null
python -m pip install -r requirements.txt

mkdir -p data secrets

if [ ! -f secrets/accounts.json ]; then
  cp secrets/accounts.example.json secrets/accounts.json
  echo ""
  echo "==> Created secrets/accounts.json from the template."
  echo "    EDIT IT and paste your real X account cookies (auth_token + ct0)."
else
  echo "==> secrets/accounts.json already exists — leaving it untouched."
fi

if [ -t 0 ]; then
  echo ""
  read -r -p "Add your X accounts now (paste cookie strings)? [Y/n]: " REPLY
  case "${REPLY:-Y}" in
    [Nn]*) echo "Skipped — run 'python src/add_logins.py' whenever you're ready." ;;
    *)     python src/add_logins.py ;;
  esac
fi

echo ""
echo " Setup complete."
echo ""
echo " Next steps:"
echo "   1. source .venv/bin/activate"
echo "   2. python collection.py --help"
