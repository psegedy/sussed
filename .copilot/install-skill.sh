#!/usr/bin/env bash
# Install the sussed-ai-review skill into Copilot CLI / Claude Code so it shows
# up in /skills and is auto-discoverable in chat.
#
# Usage:
#   ./.copilot/install-skill.sh
#
# What it does:
#   1. Symlinks .copilot/sussed-plugin into ~/.copilot/installed-plugins/sussed-marketplace/sussed
#      (and ~/.claude/plugins for Claude Code if that dir exists)
#   2. Enables the plugin in ~/.copilot/settings.json (idempotent)
#   3. Tells you to restart Copilot CLI
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLUGIN_SRC="$REPO_ROOT/.copilot/sussed-plugin"

if [[ ! -d "$PLUGIN_SRC" ]]; then
  echo "❌ Plugin source not found at $PLUGIN_SRC"
  exit 1
fi

# --- Copilot CLI install ---
COPILOT_INSTALL_ROOT="$HOME/.copilot/installed-plugins/sussed-marketplace"
COPILOT_TARGET="$COPILOT_INSTALL_ROOT/sussed"

mkdir -p "$COPILOT_INSTALL_ROOT"
if [[ -L "$COPILOT_TARGET" || -e "$COPILOT_TARGET" ]]; then
  rm -rf "$COPILOT_TARGET"
fi
ln -s "$PLUGIN_SRC" "$COPILOT_TARGET"
echo "✓ Symlinked $COPILOT_TARGET -> $PLUGIN_SRC"

# Enable in settings.json (jq if present, otherwise Python)
SETTINGS="$HOME/.copilot/settings.json"
if [[ -f "$SETTINGS" ]]; then
  if command -v jq >/dev/null 2>&1; then
    tmp=$(mktemp)
    jq '
      .extraKnownMarketplaces["sussed-marketplace"] //= {"source":{"source":"local","path":"'"$COPILOT_INSTALL_ROOT"'"}}
      | .enabledPlugins["sussed@sussed-marketplace"] = true
    ' "$SETTINGS" > "$tmp" && mv "$tmp" "$SETTINGS"
  else
    python3 - "$SETTINGS" "$COPILOT_INSTALL_ROOT" <<'PY'
import json, sys, pathlib
path = pathlib.Path(sys.argv[1])
install_root = sys.argv[2]
data = json.loads(path.read_text())
data.setdefault("extraKnownMarketplaces", {}).setdefault(
    "sussed-marketplace",
    {"source": {"source": "local", "path": install_root}},
)
data.setdefault("enabledPlugins", {})["sussed@sussed-marketplace"] = True
path.write_text(json.dumps(data, indent=2) + "\n")
PY
  fi
  echo "✓ Enabled sussed@sussed-marketplace in $SETTINGS"
else
  echo "⚠ $SETTINGS not found — open Copilot CLI once to create it, then re-run this script"
fi

# --- Claude Code install (optional, only if ~/.claude exists) ---
if [[ -d "$HOME/.claude" ]]; then
  CLAUDE_TARGET="$HOME/.claude/plugins/sussed"
  mkdir -p "$HOME/.claude/plugins"
  if [[ -L "$CLAUDE_TARGET" || -e "$CLAUDE_TARGET" ]]; then
    rm -rf "$CLAUDE_TARGET"
  fi
  ln -s "$PLUGIN_SRC" "$CLAUDE_TARGET"
  echo "✓ Symlinked $CLAUDE_TARGET -> $PLUGIN_SRC"
fi

cat <<'NEXT'

Done. Restart Copilot CLI (Ctrl+C twice, then `copilot`) or Claude Code.

Verify in Copilot CLI with:
  /skills
  /env

The skill should appear as `sussed-ai-review` and auto-activate when you
ask the agent to review/score/vibe-check saved sussed listings.

To uninstall:
  rm "$HOME/.copilot/installed-plugins/sussed-marketplace/sussed"
  # and remove "sussed@sussed-marketplace" from enabledPlugins in ~/.copilot/settings.json
NEXT

# Post-install verification — surface common breakage modes
echo
echo "--- Verification ---"
if [[ -L "$COPILOT_TARGET" && -d "$COPILOT_TARGET/skills/sussed-ai-review" ]]; then
  echo "✓ Plugin symlink resolves and skill directory is reachable"
else
  echo "✗ Plugin symlink broken or skill directory missing at $COPILOT_TARGET/skills/sussed-ai-review"
fi

if [[ -f "$SETTINGS" ]]; then
  if grep -q '"sussed@sussed-marketplace": true' "$SETTINGS"; then
    echo "✓ sussed@sussed-marketplace is enabled in settings.json"
  else
    echo "✗ sussed@sussed-marketplace is NOT enabled. Add this to enabledPlugins manually:"
    echo '    "sussed@sussed-marketplace": true'
  fi
fi
