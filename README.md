# nono-ai

Run Claude Code, OpenCode and Continue under a kernel-enforced sandbox (`nono`, Seatbelt on macOS) — same threat model as the sister `colima-docker-ai` repo this was branched from, without the Colima VM, Docker container, socat relays, or rebuilt OCI image.

## Prerequisites

These are assumed already installed on the host (the user manages them manually — this repo never installs packages):

- `nono` — `brew install nono` (v0.57+)
- `node` / `npm` — for `make update` (brew-installed node)
- `op` — 1Password CLI, with the macOS desktop app providing auth
- `ollama` — for local model inference (`brew install ollama`)
- `uv` / `uvx` — for `nono-ai-model` (`brew install uv`)

## Setup

```sh
git clone <this repo>  # already done if you're reading this
cd nono-ai
make install            # 1. pre-creates per-CLI state/cache dirs (nono drops
                        #    non-existent paths from a profile's capabilities,
                        #    so dirs MUST exist before launch)
                        # 2. copies profiles into ~/.config/nono/profiles/
                        # 3. builds the isolated npm prefix and runs
                        #    opencode-ai's postinstall under the install sandbox
                        #    so the real ~107 MB platform binary lands in place
                        #    (npm install was --ignore-scripts'd, which would
                        #    otherwise leave only a 479-byte error stub)
make check              # confirms all three CLIs launch
```

Optionally source the shell helpers from `~/.zshrc`:

```zsh
[[ -r "$HOME/Documents/ai/nono-ai/shell/nono-ai.zsh" ]] && \
  source "$HOME/Documents/ai/nono-ai/shell/nono-ai.zsh"
```

## Daily use

```sh
nono-ai-up            # start Ollama (idempotent)

nono-claude           # Claude Code, sandboxed
nono-opencode         # OpenCode, sandboxed
nono-cn               # Continue CLI, sandboxed

nono-ai-down          # stop Ollama
```

Or, equivalently, the Makefile targets from this repo: `make claude`, `make opencode`, `make continue`.

The sandboxed Claude Code shares `~/.claude.json` and `~/.claude/` with the host-installed Claude Code, so MCP server lists, OAuth credentials, and project state are all one source.

## What's allowed

Each CLI sees:

- **Read+write** to `~/Documents/ai` (working dir), its own state dir, and the npm prefix (read-only).
- **Network** to its LLM provider, MCP OAuth hosts, 1Password, and host loopback (Ollama on 127.0.0.1:11434).
- **Env vars** PATH/HOME/USER/SHELL/TERM/LANG/LC_*/TMPDIR/OLLAMA_HOST. No 1Password credentials are injected by default (nono blocks the master service-account token); sandboxed `op` requires either desktop-CLI socket forwarding or per-secret narrow `env_credentials` entries.

Everything else — `~/.ssh`, `~/.aws`, keychain, browser data, shell history, `~/.zshrc`, the rest of `$HOME` — is blocked by the kernel. Even if a CLI tries, the syscall fails.

## Updating the CLIs

```sh
make update     # or: nono-ai-update
```

This wipes `~/.local/share/nono-ai/npm` and runs `npm install -g --ignore-scripts` for OpenCode and Continue **inside** a nono sandbox that allows writes only to the npm prefix and network only to `registry.npmjs.org`. A malicious postinstall hook cannot run (`--ignore-scripts`) and even if it did it could not write outside the prefix or phone home. Claude Code is updated separately via `brew upgrade claude-code`, not by `make update`.

After the npm install completes, `make update` runs opencode-ai's own `postinstall.mjs` under the same install sandbox. That script is what puts the real ~107 MB platform binary in place — without it, `--ignore-scripts` leaves only a 479-byte error stub at `$PREFIX/bin/opencode`. Running it under nono-ai-install keeps writes confined to the npm prefix and any optional-dep fallback fetch confined to `registry.npmjs.org`.

## Ollama model management

The CLIs run on the host under nono, so the Continue `apiBase` and the OpenCode provider `baseURL` both point at host loopback `127.0.0.1:11434`. `nono-ai-model` pulls a model with Ollama and writes matching entries into both config files in one step:

```sh
nono-ai-model add    qwen3-coder:30b   # ollama pull + write to opencode.json + Continue config.yaml
nono-ai-model remove qwen3-coder:30b
nono-ai-model list
nono-ai-model select qwen3-coder:30b    # set as default in both configs
```

## OAuth callback ports

OpenCode listens on `127.0.0.1:49152` for its OAuth callback; Continue listens on `49153`. The browser callback URL the OAuth provider redirects to must match. If a CLI tries a different port, the sandbox blocks the bind and the OAuth flow fails with a connection-refused error.

To change a port, edit the `listen_port` array in `profiles/nono-ai-opencode.json` (or `nono-ai-continue.json`) and re-run `make profiles`.

## Design

See `nono-config.md` for the design rationale and a side-by-side comparison with the sister `colima-docker-ai` repo. `CLAUDE.md` documents the project for AI tools working in this repo.
