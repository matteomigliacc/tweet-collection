#!/usr/bin/env bash
# Upload the research dataset to a Yoda collection with iBridges.
#
# The destination collection must already exist. Authentication is configured
# separately with `ibridges setup` and `ibridges init`; no password is read or
# stored by this script.
set -Eeuo pipefail

SCRIPT_PATH=${BASH_SOURCE[0]:-$0}
ROOT=$(cd "$(dirname "$SCRIPT_PATH")/.." && pwd)
SOURCE="$ROOT/data/dataset"
DEST=${YODA_DEST:-}
IBRIDGES=${IBRIDGES_BIN:-"$ROOT/.venv/bin/ibridges"}
DRY_RUN=0

usage() {
  cat <<'EOF'
Usage: deploy/upload_yoda.sh --dest irods:~/collection [options]

Upload every .sqlite and .ndjson file below data/dataset, preserving the party
folders. Re-running is safe: iBridges sync only transfers missing files or files
whose content has changed. It does not delete extra files already in Yoda.

Options:
  --dest PATH       Existing Yoda/iRODS collection (or set YODA_DEST)
  --source DIR      Dataset root (default: data/dataset)
  --ibridges PATH   iBridges executable (default: .venv/bin/ibridges)
  --dry-run         Print the files and commands without contacting Yoda
  -h, --help        Show this help

Examples:
  deploy/upload_yoda.sh --dest 'irods:~/populism-scraper' --dry-run
  deploy/upload_yoda.sh --dest 'irods:/zone/home/research-group/populism-scraper'
EOF
}

die() {
  echo "upload_yoda: $*" >&2
  exit 1
}

while (($#)); do
  case "$1" in
    --dest)
      (($# >= 2)) || die "--dest needs a value"
      DEST=$2
      shift 2
      ;;
    --source)
      (($# >= 2)) || die "--source needs a value"
      SOURCE=$2
      shift 2
      ;;
    --ibridges)
      (($# >= 2)) || die "--ibridges needs a value"
      IBRIDGES=$2
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1 (try --help)"
      ;;
  esac
done

[[ -n "$DEST" ]] || die "set --dest or YODA_DEST to your Yoda collection"
[[ "$DEST" == irods:* ]] || die "destination must start with irods:"
[[ "$DEST" != "irods:" && "$DEST" != "irods:/" ]] || die "refusing to upload to the iRODS root"
[[ -d "$SOURCE" ]] || die "dataset directory does not exist: $SOURCE"
DEST=${DEST%/}
SOURCE=$(cd "$SOURCE" && pwd)

FILES=()
PARTIES=()
while IFS= read -r -d '' file; do
  relative=${file#"$SOURCE"/}
  top_level=${relative%%/*}
  # The production server also contains dated older snapshots under
  # backup_* directories. They are recovery material, not current datasets.
  [[ "$top_level" == backup_* ]] && continue
  FILES+=("$file")
  if ((${#PARTIES[@]} == 0)) || [[ "${PARTIES[${#PARTIES[@]}-1]}" != "$top_level" ]]; then
    PARTIES+=("$top_level")
  fi
done < <(find "$SOURCE" -mindepth 2 -maxdepth 2 -type f \
  \( -name '*.sqlite' -o -name '*.ndjson' \) -print0 | sort -z)

((${#FILES[@]})) || die "no .sqlite or .ndjson files found below $SOURCE"

total_bytes=0
for file in "${FILES[@]}"; do
  if stat -c '%s' "$file" >/dev/null 2>&1; then
    size=$(stat -c '%s' "$file")
  else
    size=$(stat -f '%z' "$file")
  fi
  total_bytes=$((total_bytes + size))
done

printf 'Yoda upload: %d files (%.2f GiB)\n' "${#FILES[@]}" "$(awk -v n="$total_bytes" 'BEGIN {print n/1073741824}')"
echo "  source:      $SOURCE"
echo "  destination: $DEST"

if ((DRY_RUN)); then
  echo
  for party in "${PARTIES[@]}"; do
    echo "# ensure collection exists: $DEST/$party"
    echo "# stage only the selected files for $party, then sync to $DEST/$party"
  done
  printf '  %s\n' "${FILES[@]}"
  exit 0
fi

[[ -x "$IBRIDGES" ]] || die "iBridges executable not found: $IBRIDGES (install with: .venv/bin/pip install -r deploy/requirements-yoda.txt)"

# Use the same lock as the systemd collection/backup jobs when possible. This keeps
# an SQLite database from changing while iBridges calculates and uploads it.
LOCK_FILE=${YODA_LOCK_FILE:-/run/populism-scraper.lock}
if command -v flock >/dev/null 2>&1 && [[ -w "$(dirname "$LOCK_FILE")" ]]; then
  exec 9>"$LOCK_FILE"
  echo "waiting for dataset lock: $LOCK_FILE"
  flock 9
fi

# Fail early on expired authentication or a misspelled/inaccessible collection.
if ! "$IBRIDGES" list "$DEST" >/dev/null; then
  die "cannot access $DEST; check the path and run '$IBRIDGES init' to refresh authentication"
fi

# A staging directory on the source filesystem allows hardlinks without copying
# the dataset. Directory sync then sees exactly the files counted above.
STAGING=$(mktemp -d "$SOURCE/.yoda-upload.XXXXXX")
trap 'rm -rf -- "$STAGING"' EXIT
for file in "${FILES[@]}"; do
  relative=${file#"$SOURCE"/}
  mkdir -p "$STAGING/${relative%/*}"
  ln "$file" "$STAGING/$relative"
done

uploaded=0
party_number=0
for party in "${PARTIES[@]}"; do
  remote_party="$DEST/$party"

  if ! "$IBRIDGES" list "$remote_party" >/dev/null 2>&1; then
    echo "creating collection: $remote_party"
    "$IBRIDGES" mkcoll "$remote_party"
  fi

  party_number=$((party_number + 1))
  party_files=0
  for file in "${FILES[@]}"; do
    [[ "$file" == "$SOURCE/$party/"* ]] && party_files=$((party_files + 1))
  done
  echo "[$party_number/${#PARTIES[@]}] $party ($party_files files)"
  "$IBRIDGES" sync "$STAGING/$party" "$remote_party"
  uploaded=$((uploaded + party_files))
done

echo "Yoda upload complete: $uploaded files synchronized to $DEST"
