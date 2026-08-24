#!/usr/bin/env bash
#
# Repository cleanup — removes dead code and de-duplicates the vendored agents.
#
#   ./scripts/cleanup_repo.sh          # dry run, shows what would change
#   ./scripts/cleanup_repo.sh --apply  # actually does it
#
# Dry run by default because two of these steps delete thousands of lines, and
# you should read the list before it happens.
#
# Every deletion below was verified against the import graph — nothing removed
# here is imported by main.py or anything reachable from it. Re-verify after any
# merge; `grep -rn "import voice_agent"` is the check that matters.

set -euo pipefail

cd "$(dirname "$0")/.."
APPLY=false
[[ "${1:-}" == "--apply" ]] && APPLY=true

say()  { printf '\n\033[1m%s\033[0m\n%s\n' "$1" "────────────────────────────────────────────────────────"; }
act()  { if $APPLY; then eval "$1"; printf '  done: %s\n' "$2"; else printf '  would: %s\n' "$2"; fi; }

$APPLY || printf '\n\033[33mDRY RUN — nothing will be modified. Re-run with --apply.\033[0m\n'

# ---------------------------------------------------------------------------
say "1. Rotate the leaked GitHub token"
# ---------------------------------------------------------------------------
# README.md line 21 contains a live personal access token inside a clone URL.
# This step scrubs the file, but that is only half the job: the token is in git
# history and must be revoked at
#   github.com -> Settings -> Developer settings -> Personal access tokens
# Do that FIRST. Scrubbing the working copy while the token stays valid is
# strictly worse than leaving it visible, because it stops anyone noticing.
if grep -q 'ghp_' README.md 2>/dev/null; then
  printf '  \033[31mLEAKED TOKEN STILL PRESENT in README.md\033[0m\n'
  printf '  Revoke it on GitHub before running --apply.\n'
  act "sed -i 's#https://ghp_[A-Za-z0-9]*@github.com#https://github.com#g' README.md" \
      "scrub the token from README.md"
else
  printf '  clean — no token in README.md\n'
fi

# ---------------------------------------------------------------------------
say "2. Remove superseded voice agent"
# ---------------------------------------------------------------------------
# voice_agent.py is 2,104 lines and 17 route decorators, imported by nothing.
# It is a fork of multiligual_call.py, not a copy — only 5 of 24 function names
# overlap — so it cannot be diffed away; it is simply the previous generation.
if [[ -f voice_agent.py ]]; then
  if grep -rqs --include='*.py' -E '(from|import) +voice_agent' .; then
    printf '  \033[33mSKIPPED — something imports voice_agent, investigate first:\033[0m\n'
    grep -rns --include='*.py' -E '(from|import) +voice_agent' . | sed 's/^/    /'
  else
    act "git rm -q voice_agent.py 2>/dev/null || rm -f voice_agent.py" \
        "delete voice_agent.py (2,104 lines, zero importers)"
  fi
fi

# ---------------------------------------------------------------------------
say "3. De-duplicate the vendored social/browser agents"
# ---------------------------------------------------------------------------
# social_agent/ and browser_agent_v2-main/ are forks of each other: ~2/3 of
# their shared files are byte-identical and the rest have drifted. Keeping both
# guarantees that a fix applied to one never reaches the other.
#
# This step only REPORTS. Which fork wins is a judgement call about which
# divergences you want to keep, and a script cannot make it for you.
if [[ -d social_agent && -d browser_agent_v2-main ]]; then
  same=0; diff_count=0
  while IFS= read -r f; do
    rel="${f#social_agent/}"
    other="browser_agent_v2-main/$rel"
    [[ -f "$other" ]] || continue
    if cmp -s "$f" "$other"; then same=$((same+1)); else
      diff_count=$((diff_count+1)); printf '    DIVERGED  %s\n' "$rel"
    fi
  done < <(find social_agent -name '*.py')
  printf '\n  %s identical file(s), %s diverged.\n' "$same" "$diff_count"
  printf '  Pick one fork as canonical, port the diverged files into it, and delete\n'
  printf '  the other. Diff a specific file with:\n'
  printf '    diff -u social_agent/<path> browser_agent_v2-main/<path>\n'
fi

# ---------------------------------------------------------------------------
say "4. Remove unreferenced Windows binaries"
# ---------------------------------------------------------------------------
# RNNOISESHARE.dll and prompts.cp310-win_amd64.pyd are referenced by no Python
# source in the tree, and the .pyd is a CPython 3.10 Windows extension that
# cannot load on the Linux runtime image regardless.
if [[ -d dll_file ]]; then
  if grep -rqsi --include='*.py' -E 'rnnoise|dll_file' .; then
    printf '  \033[33mSKIPPED — something references dll_file, check first.\033[0m\n'
  else
    act "git rm -rq dll_file 2>/dev/null || rm -rf dll_file" \
        "delete dll_file/ (267KB, no references)"
  fi
fi

# ---------------------------------------------------------------------------
say "5. Un-ignore the Dockerfile"
# ---------------------------------------------------------------------------
# .gitignore contains `dockerfile`, which is why docker-compose.yml's
# `build: .` and the CI workflow have nothing to build from on a fresh clone.
if grep -qix 'dockerfile' .gitignore 2>/dev/null; then
  act "sed -i '/^dockerfile$/Id' .gitignore" "remove 'dockerfile' from .gitignore"
else
  printf '  clean — Dockerfile is not ignored\n'
fi

# ---------------------------------------------------------------------------
say "6. Strip caches and build debris"
# ---------------------------------------------------------------------------
count=$(find . -type d -name '__pycache__' -not -path './.git/*' | wc -l)
act "find . -type d -name '__pycache__' -not -path './.git/*' -prune -exec rm -rf {} +" \
    "remove ${count} __pycache__ director(ies)"
act "find . -type f \( -name '*.pyc' -o -name '*.pyo' -o -name '.DS_Store' \) -delete" \
    "remove .pyc/.pyo/.DS_Store"

# ---------------------------------------------------------------------------
say "7. Format and lint"
# ---------------------------------------------------------------------------
# Config lives in pyproject.toml. Note that `ruff check --fix` is NOT run over
# the legacy files by default: multiligual_call.py and batching.py have 100+
# broad excepts each, and auto-fixing them in one pass produces a diff nobody
# can review against a system that places real phone calls. Format first, land
# that, then fix lints module by module.
if command -v ruff >/dev/null 2>&1; then
  act "ruff format ." "ruff format across the tree"
  act "ruff check LeadAI/ demo/ config_guard.py --fix" "ruff --fix on LeadAI + new code only"
  printf '  Remaining legacy lint (report only):\n'
  $APPLY && ruff check multiligual_call.py batching.py --statistics 2>/dev/null | head -12 || true
else
  printf '  ruff not installed:  pip install ruff\n'
fi

# ---------------------------------------------------------------------------
say "Summary"
# ---------------------------------------------------------------------------
if $APPLY; then
  printf '  Applied. Review with: git status && git diff --stat\n\n'
else
  printf '  Dry run finished. Re-run with --apply once the token is revoked.\n\n'
fi
