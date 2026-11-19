#!/usr/bin/env bash
# Deploy the growfood skill to a remote host running Hermes.
#
# Configuration (env vars or positional args):
#   HOST      — user@host to deploy to (required; or pass as $1)
#   KEY       — path to SSH private key (default: ~/.ssh/id_rsa)
#   SKILL_DIR — remote skills directory  (default: ~/.hermes/skills)
#
# Examples:
#   HOST=pi@192.168.1.50 ./deploy.sh
#   ./deploy.sh pi@192.168.1.50
#   HOST=pi@10.0.0.5 KEY=~/.ssh/id_pi SKILL_DIR=/opt/hermes/skills ./deploy.sh
set -euo pipefail

HOST="${HOST:-${1:-}}"
KEY="${KEY:-$HOME/.ssh/id_rsa}"
SKILL_DIR="${SKILL_DIR:-$HOME/.hermes/skills}"

if [[ -z "$HOST" ]]; then
  cat >&2 <<'USAGE'
Usage: deploy.sh [user@host]

Deploy the growfood skill to a remote Hermes installation.

Set HOST as an env var or pass it as the first argument.
Optional env vars:
  KEY       — SSH private key path  (default: ~/.ssh/id_rsa)
  SKILL_DIR — remote skills path    (default: ~/.hermes/skills)
USAGE
  exit 1
fi

DIR="$(cd "$(dirname "$0")" && pwd)"
SSH=(ssh -o IdentitiesOnly=yes -i "$KEY" "$HOST")

# --- Upload the skill directory ---
echo "→ copying skill to $HOST:$SKILL_DIR/growfood"
"${SSH[@]}" "mkdir -p '$SKILL_DIR/growfood'"
COPYFILE_DISABLE=1 tar --no-xattrs --exclude '__pycache__' --exclude '.git' \
  -C "$DIR" -czf - SKILL.md scripts docs \
  | "${SSH[@]}" "tar -C '$SKILL_DIR/growfood' -xzf - 2>/dev/null; chmod +x '$SKILL_DIR/growfood/scripts/growfood.py'"

# --- Install the 'gf' convenience command ---
echo "→ installing gf command"
"${SSH[@]}" 'mkdir -p ~/.local/bin
cat > ~/.local/bin/gf <<EOF
#!/bin/sh
exec python3 "'"$SKILL_DIR"'/growfood/scripts/growfood.py" "\$@"
EOF
chmod +x ~/.local/bin/gf
grep -q "growfood: PATH" ~/.bashrc 2>/dev/null || echo "export PATH=\"\$HOME/.local/bin:\$PATH\"  # growfood: PATH" >> ~/.bashrc'

# --- Verify installation ---
echo "→ verifying"
"${SSH[@]}" '~/.local/bin/gf --help >/dev/null && echo "gf ok"; \
  if [ -f ~/.config/growfood/token.json ]; then ~/.local/bin/gf orders; else echo "No token yet — run: gf set-token"; fi'

cat <<'EOF'

Done. On the remote host (open a NEW ssh session so PATH picks up gf):
  gf set-token        # paste token, Enter
  gf review
EOF
