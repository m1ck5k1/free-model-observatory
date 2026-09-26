#!/usr/bin/env bash
# Free Model Observatory — secret leak-scan gate (MoA FMO blocker 4).
# Runs on `git pre-commit` AND `git pre-push`. Scans the staged changes (pre-commit)
# or the to-be-pushed range (pre-push) for:
#   1. Provider credential shapes   (sk-, AIza, gsk_, aws AKIA, Bearer <40>, ...)
#   2. The sensitive client roster  (config/sensitive_terms.yaml) which carries client
#      identifiers and must NEVER land in the public repo (per the 2026-07-30 purge).
# Any hit aborts the operation. Override ONLY by deleting the hook (never expected).
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# Patterns: expensive matches, but correctness beats speed here.
LEAK_PATTERNS=(
  'sk-[A-Za-z0-9._-]{24,}'        # OpenAI: sk-proj-<32+ alnum/dash> (hyphen inside token)
  'AIza[0-9A-Za-z_-]{30,}'
  'gsk_[A-Za-z0-9]{20,}'
  'AKIA[0-9A-Z]{16}'
  'xox[baprs]-[0-9A-Za-z-]{10,}'
  'ghp_[A-Za-z0-9]{30,}'
  'Bearer [A-Za-z0-9._-]{40,}'
)

ROSTER='config/sensitive_terms.yaml'

die() { echo "FMO LEAK-SCAN GATE FAILED: $1" >&2; exit 1; }

# Gather the file range to scan based on hook type.
HOOK_TYPE="$(basename "$0")"
case "$HOOK_TYPE" in
  pre-commit)
    FILES="$(git diff --cached --name-only -z 2>/dev/null | tr '\0' '\n')"
    ;;
  pre-push)
    # $1 remote, $2 url; stdin lines: <local> <local-sha> <remote> <remote-sha> <forced?>
    SHAS=$(git rev-parse --verify HEAD 2>/dev/null) || die "cannot resolve HEAD"
    FILES=$(git diff --name-only "$(git merge-base origin/main HEAD 2>/dev/null || echo HEAD~1)" HEAD 2>/dev/null)
    ;;
  *) die "unknown hook type $HOOK_TYPE" ;;
esac

[ -z "$FILES" ] && exit 0  # nothing staged/pushed

# 1. Roster must never be tracked.
if echo "$FILES" | grep -qx "$ROSTER"; then
  die "config/sensitive_terms.yaml is STAGED/PUSHED. This file carries client "
       "identifiers and must never enter the public repo (purged 2026-07-30). "
       "Remove it from the commit and set FMO_SENSITIVE_TERMS_PATH instead."
fi

# 2. Scan each file's content for credential shapes (skip binary / lockfiles).
issue=0
while IFS= read -r f; do
  [ -f "$f" ] || continue
  case "$f" in
    *.lock|*.png|*.jpg|*.jpeg|*.pyc|.venv/*|*.egg-info/*) continue ;;
  esac
  for pat in "${LEAK_PATTERNS[@]}"; do
    if grep -Piq "$pat" "$f" 2>/dev/null; then
      echo "  LEAK pattern matched in: $f" >&2
      issue=1
    fi
  done
done <<<"$FILES"

[ "$issue" -eq 0 ] || die "credential-shaped content found in staged/pushed files (list above)."
echo "FMO leak-scan: clean."
exit 0