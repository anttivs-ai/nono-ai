# nono-ai.zsh — host shell functions for the nono-ai sandbox.
#
# Source this from ~/.zshrc with:
#   [[ -r "$HOME/Documents/ai/nono-ai/shell/nono-ai.zsh" ]] && \
#     source "$HOME/Documents/ai/nono-ai/shell/nono-ai.zsh"
#
# Provides:
#
#   nono-claude    [args]    Launch Claude Code under the nono-ai-claude profile.
#   nono-opencode  [args]    Launch OpenCode under the nono-ai-opencode profile.
#   nono-cn        [args]    Launch Continue CLI under the nono-ai-continue profile.
#
#   nono-opencode-auth <mcp>    Re-authenticate an OpenCode MCP server (browser flow).
#                               Pairs --allow-launch-services with the profile so the
#                               sandboxed CLI may open a URL via macOS LaunchServices.
#   nono-cn-auth   [args]       cn equivalent — same LaunchServices gate enabled.
#                               Use these sparingly: --allow-launch-services widens
#                               the sandbox beyond URL-opening. Switch back to plain
#                               nono-opencode / nono-cn once tokens are refreshed.
#
#   nono-ai-up               Start Ollama on the host (idempotent). The CLIs
#                            run on the host under nono, so they reach the
#                            daemon directly on 127.0.0.1:11434 — no VM and
#                            no relays in the path.
#   nono-ai-down             Stop Ollama on the host.
#   nono-ai-update           Rebuild the isolated npm prefix (calls `make update`
#                            in the repo, which runs npm install inside nono
#                            and then runs opencode-ai's postinstall under the
#                            same install profile so its real platform binary
#                            replaces the published stub).
#   nono-ai-model add|remove|select|list
#                            Manage local Ollama models and sync to both
#                            ~/.config/opencode/opencode.json and
#                            ~/.continue/config.yaml. The `nono-ai-` prefix is
#                            kept purely as a namespacing convention.
#
# All entry points are thin wrappers around `nono run --profile <name> --`.
# No 1Password credentials are injected into the sandbox by default: nono
# blocks the master OP_SERVICE_ACCOUNT_TOKEN as a dangerous variable, and
# the desktop CLI socket is not forwarded. `op` inside a sandboxed CLI
# therefore won't work until either the desktop agent socket is granted
# in nono-ai-base.json or narrow per-secret env_credentials entries are
# added (see CLAUDE.md for context).

# Remember our own directory so nono-ai-model can find its sibling Python
# script regardless of where the repo is cloned. ${(%):-%x} expands to the
# path of the file currently being sourced; :A:h gives its absolute directory.
typeset -g _NONO_AI_DIR="${${(%):-%x}:A:h}"
typeset -g _NONO_AI_PREFIX="$HOME/.local/share/nono-ai/npm"

# Per-CLI launchers. Pass any args straight through to the underlying CLI so
# `nono-claude /login`, `nono-opencode mcp auth consensus`, etc. work as
# expected. nono itself prints a brief banner unless --silent.
#
# Source of each CLI binary:
#   nono-claude    -> /opt/homebrew/bin/claude (Homebrew package `claude-code`).
#                     The Homebrew install is the single canonical Claude Code
#                     on this host; the npm prefix no longer carries a copy.
#   nono-opencode  -> $_NONO_AI_PREFIX/bin/opencode (isolated npm prefix).
#   nono-cn        -> $_NONO_AI_PREFIX/bin/cn       (isolated npm prefix).
nono-claude() {
  nono run --profile nono-ai-claude    -- /opt/homebrew/bin/claude            "$@"
}

nono-opencode() {
  nono run --profile nono-ai-opencode  -- "$_NONO_AI_PREFIX/bin/opencode" "$@"
}

# OAuth-aware variant: pairs the profile's allow_launch_services: true with
# the `--allow-launch-services` CLI gate so opencode can call `open <URL>`
# during the full OAuth dance. Only needed when an MCP's refresh-token
# grant has also expired (the silent refresh path doesn't need this).
# Usage:  nono-opencode-auth consensus
nono-opencode-auth() {
  nono run --profile nono-ai-opencode --allow-launch-services -- "$_NONO_AI_PREFIX/bin/opencode" mcp auth "$@"
}

nono-cn() {
  # `--config` is passed unconditionally to bypass cn's hub-based assistant
  # onboarding (the "Log in with Continue / Enter Anthropic API key" prompt
  # that ignores ~/.continue/config.yaml until you've authenticated to the
  # hub or supplied an Anthropic key). Pointing at the local YAML makes cn
  # use it directly. Anything in "$@" follows and can override.
  nono run --profile nono-ai-continue  -- "$_NONO_AI_PREFIX/bin/cn" --config "$HOME/.continue/config.yaml" "$@"
}

# OAuth-aware variant for cn. cn has no explicit `auth` subcommand; OAuth
# fires when an MCP returns 401 mid-session. Use this when you know an
# MCP refresh has fully expired and the full browser dance is needed.
# Pass the prompt (or no args for interactive) as usual.
nono-cn-auth() {
  nono run --profile nono-ai-continue --allow-launch-services -- "$_NONO_AI_PREFIX/bin/cn" --config "$HOME/.continue/config.yaml" "$@"
}

# Rebuild the npm prefix. Delegates to the Makefile so the install profile
# stays in one place. ${_NONO_AI_DIR:h} is the parent of shell/, i.e. the
# repo root where the Makefile lives.
nono-ai-update() {
  make -C "${_NONO_AI_DIR:h}" update
}

# Start Ollama on the host. Idempotent — already-listening Ollama is a no-op.
# No colima, no dash-mcp wrapper, no socat relays: under nono everything runs
# on the host so loopback is reachable as 127.0.0.1 directly.
nono-ai-up() {
  if lsof -nP -iTCP:11434 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "nono-ai-up: ollama already listening on 127.0.0.1:11434"
  else
    brew services start ollama || {
      echo "nono-ai-up: 'brew services start ollama' failed; start it manually" >&2
      return 1
    }
  fi
  echo "nono-ai-up: ready (launch a CLI with nono-claude / nono-opencode / nono-cn)"
}

# Stop Ollama. Try brew services first (covers the `nono-ai-up` path), then
# kill whatever's still bound to 127.0.0.1:11434 (covers a plain `ollama serve`
# or a launchd-managed service that brew doesn't know about).
nono-ai-down() {
  brew services stop ollama 2>/dev/null
  local ollama_pid
  ollama_pid=$(lsof -nP -iTCP:11434 -sTCP:LISTEN -t 2>/dev/null | head -1)
  if [[ -n $ollama_pid ]]; then
    kill "$ollama_pid" 2>/dev/null
    echo "nono-ai-down: ollama (pid $ollama_pid) terminated"
  else
    echo "nono-ai-down: ollama not running"
  fi
}

# Pull / remove local Ollama models and sync them into the two host config
# files the sandboxed CLIs consume. ai-model.py writes `apiBase:
# http://127.0.0.1:11434` into Continue entries — the CLIs run directly on
# the host under nono, so host loopback is the right address.
nono-ai-model() {
  command -v uvx >/dev/null || {
    echo "nono-ai-model: uvx not installed (brew install uv)" >&2
    return 1
  }
  uvx --quiet --with ruamel.yaml python3 \
    "$_NONO_AI_DIR/ai-model.py" "$@"
}
