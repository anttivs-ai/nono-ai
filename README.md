# nono-ai

Run Claude Code, OpenCode and Continue under a kernel-enforced sandbox (`nono`, Seatbelt on macOS) — same threat model as the sister repo `colima-docker-ai`, without the Colima VM, Docker container, socat relays, or rebuilt OCI image.

## Prerequisites

These are assumed already installed on the host (the user manages them manually — this repo never installs packages):

- `nono` — `brew install nono` (v0.57+)
- `node` / `npm` — for `make update` (brew-installed node)
- `op` — 1Password CLI, with the macOS desktop app providing auth
- `ollama` — for local model inference (the existing colima-docker-ai brew install is fine)
- `uv` / `uvx` — for `nono-ai-model` (`brew install uv`)

## Setup

```sh
git clone <this repo>  # already done if you're reading this
cd nono-ai
make install            # copies profiles into ~/.config/nono/profiles/
                        # AND builds the isolated npm prefix
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
- **Env vars** PATH/HOME/USER/SHELL/TERM/LANG/LC_*/TMPDIR/OLLAMA_HOST, plus an injected `OP_SERVICE_ACCOUNT_TOKEN` fetched from 1Password by nono itself.

Everything else — `~/.ssh`, `~/.aws`, keychain, browser data, shell history, `~/.zshrc`, the rest of `$HOME` — is blocked by the kernel. Even if a CLI tries, the syscall fails.

## Updating the CLIs

```sh
make update     # or: nono-ai-update
```

This wipes `~/.local/share/nono-ai/npm` and runs `npm install -g --ignore-scripts` for the three CLIs **inside** a nono sandbox that allows writes only to the npm prefix and network only to `registry.npmjs.org`. A malicious postinstall hook cannot run (`--ignore-scripts`) and even if it did it could not write outside the prefix or phone home.

## Ollama model management

Same UX as `colima-docker-ai`'s `ai-model`, but the wrapper is prefixed and the Continue `apiBase` is hardcoded to `127.0.0.1`:

```sh
nono-ai-model add    qwen3-coder:30b   # ollama pull + write to opencode.json + Continue config.yaml
nono-ai-model remove qwen3-coder:30b
nono-ai-model list
nono-ai-model select qwen3-coder:30b    # set as default in both configs
```

## OAuth callback ports

OpenCode listens on `127.0.0.1:49152` for its OAuth callback; Continue listens on `49153`. The browser callback URL the OAuth provider redirects to must match. If a CLI tries a different port, the sandbox blocks the bind and the OAuth flow fails with a connection-refused error.

To change a port, edit the `listen_port` array in `profiles/nono-ai-opencode.jsonc` (or `nono-ai-continue.jsonc`) and re-run `make profiles`.

## Coexistence with colima-docker-ai

Both setups can be sourced and used in parallel. There's one shared resource you have to pick one mode for at a time:

- `~/.continue/config.yaml` — the `apiBase` URL is either `http://host.docker.internal:11434` (Docker) or `http://127.0.0.1:11434` (nono). `nono-ai-model add` writes the nono form; the colima-docker-ai `ai-model` writes the Docker form. Use whichever's relevant to the sandbox you actually intend to run.

Both `ai-model` scripts are functionally identical except for that one constant, so swapping is just `nono-ai-model remove X && nono-ai-model add X`.

## Design

See `nono-config.md` for the design rationale and a side-by-side comparison with colima-docker-ai. `CLAUDE.md` documents the project for AI tools working in this repo.
