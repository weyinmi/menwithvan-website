#!/usr/bin/env bash
set -euo pipefail

MESSAGE="${1:-Update Men With a Van website}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GITHUB_KEY="${GITHUB_KEY:-$HOME/.ssh/github_weyinmi}"
BUNDLED_PYTHON="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"

cd "$ROOT_DIR"

if [ ! -d .git ]; then
  echo "This folder is not linked to GitHub yet."
  exit 1
fi

if ! git status --short | grep -q .; then
  echo "No unpublished local changes found."
  exit 0
fi

if [ -x "$BUNDLED_PYTHON" ]; then
  "$BUNDLED_PYTHON" -m py_compile outputs/menwithvan-backend/app.py
elif command -v python3 >/dev/null 2>&1; then
  python3 -m py_compile outputs/menwithvan-backend/app.py
else
  echo "python3 not found; skipping backend syntax check."
fi

bash -n outputs/deploy-menwithvan-checkout-fix.sh
bash -n outputs/configure-menwithvan-gmail.sh

git add README.md .github .gitignore outputs synchronize
git commit -m "$MESSAGE"
GIT_SSH_COMMAND="ssh -i $GITHUB_KEY -o IdentitiesOnly=yes" git push

echo "Published to GitHub. GitHub Actions will deploy it to the VPS automatically."
