# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A kernel-sandboxed replacement for `~/Documents/ai/colima-docker-ai`. Same three AI CLIs (Claude Code, OpenCode, Continue) plus the 1Password CLI (`op`), but wrapped in `nono` (Seatbelt on macOS, Landlock on Linux) instead of running inside a Colima/Docker VM.

The two repos are designed to coexist during a transition period. See `nono-config.md` for the design rationale and a side-by-side comparison.

## Common commands

| Task | Command |
| --- | --- |
| One-time setup (copy profiles + build npm prefix) | `make install` |
| Rebuild the isolated npm prefix (pulls latest CLIs) | `make update` |
| Copy versioned profiles into `~/.config/nono/profiles/` | `make profiles` |
| Run Claude Code in the sandbox | `make claude` |
| Run OpenCode in the sandbox | `make opencode` |
| Run Continue CLI (`cn`) in the sandbox | `make continue` |
| Smoke-test all three CLIs launch | `make check` |
| Wipe the npm prefix | `make clean` |

Prerequisites: `brew install nono` (the user installs this manually — do **not** script it; the user's global CLAUDE.md forbids installing packages on the host). `op` and `ollama` are assumed already brew-installed for the Docker setup.

## Architecture

Three artifacts, all small:

1. **Versioned nono profiles** in `profiles/*.jsonc`. These are the canonical source of truth for what each CLI is allowed to read, write, talk to, and which env vars/credentials it gets. `make profiles` copies them to `~/.config/nono/profiles/` (where nono resolves `extends:` references and `--profile <name>`).
2. **An isolated npm prefix** at `~/.local/share/nono-ai/npm` containing the three CLIs. `make update` rebuilds it from scratch each time with `npm install -g --ignore-scripts` *inside* the `nono-ai-install` profile. The prefix is never on the global PATH — CLIs are invoked through nono via `$PREFIX/bin/<cli>` so a compromised CLI can't shadow system binaries or modify its own files at runtime.
3. **Thin entry points** — the `Makefile` and `shell/nono-ai.zsh` — that just call `nono run --profile <name> -- <cli>`. nono itself fetches the 1Password service-account token via `env_credentials` (the `op://...` URI in `profiles/nono-ai-base.jsonc`), so there is no host-side `op read` step.

### Profile hierarchy

```
default                          (shipped by nono)
└── nono-ai-base                  (this repo: working dir, op, ollama, npm prefix, deny groups, env_credentials)
    ├── nono-ai-claude            (adds ~/.claude{,.json} + claude_code_macos group + claude-code network profile)
    ├── nono-ai-opencode          (adds OpenCode state dirs + OAuth listener port 49152)
    └── nono-ai-continue          (adds ~/.continue + OAuth listener port 49153)

default
└── nono-ai-install               (write-only to npm prefix, network only to registry.npmjs.org)
```

### What each profile gives the CLI

- **Filesystem allow**: `~/Documents/ai` (working dir), the CLI's own state dir, the npm prefix (read-only).
- **Filesystem deny** (via nono policy groups): `~/.ssh`, `~/.aws`, `~/.gnupg`, keychain DBs, browser data, shell history, `~/.zshrc` / `~/.bashrc`.
- **Network allow**: LLM API hosts (via `network_profile: claude-code` or explicit `allow_domain`), MCP OAuth providers, `my.1password.com` (for `op` CLI), loopback 11434 (Ollama) and 8765 (avs-rag).
- **Env vars**: PATH/HOME/USER/SHELL/TERM/LANG/LC_*/TMPDIR/OLLAMA_HOST allowed through. `OP_SERVICE_ACCOUNT_TOKEN` injected from 1Password by nono itself.

## Differences from colima-docker-ai

If you're familiar with that repo, the major architectural changes:

| What | colima-docker-ai | nono-ai |
| --- | --- | --- |
| Sandbox mechanism | Colima VM + Docker container | macOS Seatbelt via nono |
| Filesystem isolation | virtiofs mounts of allowlisted dirs | nono profile `filesystem.allow/read/write` |
| Network isolation | bridge network + three socat relays | nono profile `network.allow_domain` + `open_port` |
| Host loopback access | socat relay on bridge IP → 127.0.0.1 | direct (CLI runs on host) |
| `~/.claude.json` | separate file bind to container-claude.json | shared with host Claude Code install |
| OAuth callbacks | `make auth` with `--network host` | works in normal mode via `listen_port` |
| Dash MCP | stdio wrapped in mcp-proxy as HTTP | direct stdio, same as host Claude Code |
| 1Password token | `op read` on host, env-inherit to docker | nono `env_credentials` with `op://` URI |
| Update flow | `make build` (3-5 min, --no-cache --pull) | `make update` (npm install in sandbox) |
| Supply-chain protection | 1Password debsig signature verify; npm install scripts allowed | `--ignore-scripts` + sandboxed install (writes confined to npm prefix, network to registry.npmjs.org only) |

## Sync points

The Docker repo had four duplicate places that had to track mount/env/image-name changes (Makefile, shell helpers, docker-config.md, colima-ai mount allowlist). This repo collapses that to **one** canonical place per concern:

| Concern | Single source of truth |
| --- | --- |
| Filesystem allow-lists | `profiles/*.jsonc` |
| Network allow-lists | `profiles/*.jsonc` |
| Credential injection | `profiles/nono-ai-base.jsonc` (`env_credentials` key) |
| npm install prefix path | `Makefile` (`PREFIX :=`) — only used here and by the per-CLI run targets |
| Entry-point invocations | Either `Makefile` or `shell/nono-ai.zsh` — both call `nono run --profile <name>` so the profile is the source of truth, not the invocation |

The Makefile and shell helpers do duplicate the `nono run --profile X --` line per CLI. That's a 3-line redundancy across two files and is intentional (so the user can pick either entry point); if a profile name changes, both need an update.

## Update model

Same philosophy as colima-docker-ai: latest-and-rebuild. `make update` always wipes `$PREFIX` and runs `npm install -g --ignore-scripts @anthropic-ai/claude-code @continuedev/cli opencode-ai` (no version pins, no lockfile). If an upstream regression breaks a rebuild, **temporarily** pin the offending package in the Makefile (`@anthropic-ai/claude-code@2.1.89`) and remove the pin once upstream is fixed.

## Things that look removable but aren't

- The `read: ["$HOME/.local/share/nono-ai/npm"]` entry in `nono-ai-base.jsonc` looks redundant given each CLI's profile is referenced explicitly — but it's needed so the CLI binary itself (and its bundled `node_modules`) can be loaded by the runtime at startup. Removing this breaks all three CLIs immediately.
- The `allow_domain: ["claude.ai"]` in `nono-ai-claude.jsonc` next to `network_profile: "claude-code"` is belt-and-suspenders — we have not confirmed the bundled `claude-code` network profile includes the OAuth host. Keep both until verified.
- `nono-ai-up` only starts Ollama (vs colima-docker-ai's `ai-up` which also starts colima and dash-mcp). There is no VM and no Dash HTTP wrapper here, by design.
