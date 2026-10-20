#!/usr/bin/env bash
# Print the Muse marketplace integrity digest for plugin/ at a git ref.
# Hashes a CLEAN clone (never the working tree: plugin/.env would be hashed)
# in a throwaway HOME, and reads `package_sha256` -- the value Muse's
# marketplace install verifies. (`manifest_sha256` is a different hash.)
#   scripts/plugin-digest.sh [ref]          print digest (default ref: HEAD)
#   scripts/plugin-digest.sh --check [ref]  exit 1 unless marketplace.json matches
set -euo pipefail
check=0; [[ "${1:-}" == --check ]] && { check=1; shift; }
ref="${1:-HEAD}"
repo="$(git rev-parse --show-toplevel)"
S="$(mktemp -d)"; trap 'rm -rf "$S"' EXIT
git clone -q "$repo" "$S/repo"
git -C "$S/repo" checkout -q "$(git -C "$repo" rev-parse "$ref")"
mkdir -p "$S/home"
digest="$(HOME="$S/home" XDG_CONFIG_HOME="$S/home/.config" \
  XDG_DATA_HOME="$S/home/.local/share" XDG_CACHE_HOME="$S/home/.cache" \
  muse plugins install "$S/repo/plugin" --json \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["installed"]["package_sha256"])')"
if (( check )); then
  pinned="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["plugins"][0]["integrity"]["digest"])' "$S/repo/marketplace.json")"
  [[ "$pinned" == "$digest" ]] && { echo "ok $digest"; exit 0; }
  echo "mismatch: marketplace.json has $pinned, plugin/ hashes to $digest" >&2; exit 1
fi
echo "$digest"
