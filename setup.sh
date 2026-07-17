#!/usr/bin/env bash
#
# One-shot setup for the populism tweet scraper.
# Creates a virtualenv, installs dependencies, and prepares the secrets file.
#
# Usage:
#   ./setup.sh
#
set -euo pipefail

cd "$(dirname "$0")"

# --- pick a Python ---------------------------------------------------------
PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "ERROR: '$PY' not found. Install Python 3.10+ or set PYTHON=/path/to/python." >&2
  exit 1
fi
echo "==> Using $($PY --version)"

# --- virtualenv ------------------------------------------------------------
if [ ! -d .venv ]; then
  echo "==> Creating virtualenv in .venv"
  "$PY" -m venv .venv
else
  echo "==> Reusing existing .venv"
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# --- dependencies ----------------------------------------------------------
echo "==> Upgrading pip and installing requirements"
python -m pip install --upgrade pip >/dev/null
python -m pip install -r requirements.txt

# --- folders ---------------------------------------------------------------
mkdir -p data secrets

# --- secrets template ------------------------------------------------------
if [ ! -f secrets/accounts.json ]; then
  cp secrets/accounts.example.json secrets/accounts.json
  echo ""
  echo "==> Created secrets/accounts.json from the template."
  echo "    EDIT IT and paste your real X account cookies (auth_token + ct0)."
  echo "    (This file is git-ignored — it will never be committed.)"
  NEEDS_COOKIES=1
else
  echo "==> secrets/accounts.json already exists — leaving it untouched."
  NEEDS_COOKIES=0
fi

# --- offer to add accounts interactively -----------------------------------
# Only prompt when we have a real terminal (skip in CI / piped runs).
if [ -t 0 ]; then
  echo ""
  read -r -p "Add your X accounts now (paste cookie strings)? [Y/n]: " REPLY
  case "${REPLY:-Y}" in
    [Nn]*) echo "Skipped — run 'python src/add_accounts.py' whenever you're ready." ;;
    *)     python src/add_accounts.py ;;
  esac
fi

# --- done ------------------------------------------------------------------
echo ""
echo "============================================================"
echo " Setup complete."
echo ""
echo " Next steps:"
echo "   1. source .venv/bin/activate"
echo "   2. python src/add_accounts.py    # add X accounts by cookie string (if not done above)"
echo "   3. python src/load_accounts.py   # load + verify the account pool"
echo "   4. python src/scrape.py          # start scraping"
echo "============================================================"
