# CLAUDE.md

Guidance for Claude Code working in this repo.

## What this repo is

A lighter-weight, kernel-sandboxed branch of `~/Documents/ai/colima-docker-ai` (sister repo, still maintained). Same three AI CLIs (Claude Code, OpenCode, Continue) plus `op`, wrapped in `nono` (Seatbelt on macOS, Landlock on Linux) instead of a Colima/Docker VM.

- **Claude Code** — host Homebrew at `/opt/homebrew/bin/claude`.
- **OpenCode + Continue** — isolated npm prefix at `~/.local/share/nono-ai/npm/` (not on `$PATH`), built by `make update`.
- All launched only through `nono run --profile <name>`. See `nono-config.md` for design rationale and full side-by-side with colima-docker-ai.

## Common commands

| Task | Command |
| --- | --- |
| Fresh-host setup (state dirs + profiles + npm prefix + opencode-ai postinstall) | `make install` |
| Rebuild npm prefix + re-run opencode-ai postinstall + refresh dash-mcp-server uvx cache | `make update` |
| Refresh dash-mcp-server uvx cache only (runs uvx outside nono so it can reach PyPI) | `make dash-mcp-cache` |
| Pre-create per-CLI state/cache dirs | `make state` |
| Deploy profiles to `~/.config/nono/profiles/` | `make profiles` |
| Run Claude / OpenCode / cn in the sandbox | `make claude` / `make opencode` / `make continue` |
| Re-auth an OpenCode MCP (browser, full OAuth) | `make opencode-auth MCP=consensus` |
| Run cn with LaunchServices on (in-session MCP OAuth) | `make continue-auth` |
| Smoke-test all three CLIs launch | `make check` |
| Wipe npm prefix | `make clean` |

Prerequisites: `brew install nono` — manual, do **not** script (global CLAUDE.md forbids host installs). `op` and `ollama` assumed already brew-installed.

## Architecture

Four artifacts:

1. **Versioned nono profiles** in `profiles/*.json` — canonical source of truth for filesystem / network / env / credentials per CLI. `make profiles` deploys to `~/.config/nono/profiles/` and removes stale `nono-ai-*.jsonc` (nono v0.57's parser rejects `//` comments). Profile-internal docs go in `meta.description`.

2. **Isolated npm prefix** at `~/.local/share/nono-ai/npm` for OpenCode + Continue. `make update` wipes and reinstalls under the `nono-ai-install` profile, then re-runs opencode-ai's `postinstall.mjs` under the same profile. **Mandatory**: opencode-ai's tarball ships only a 479-byte error stub at `bin/opencode.exe`; the postinstall hard-links the real ~107 MB binary from the optional-dep package (e.g. `opencode-darwin-arm64`) into place. Prefix never on `$PATH`; binaries invoked by absolute path through nono. Claude Code lives at `/opt/homebrew/bin/claude` and uses the same profile mechanism.

3. **Pre-created state dirs** via `make state`. nono v0.57 silently drops non-existent paths from a profile's effective capabilities — a profile allowing `$HOME/.cache/opencode` on a host where that dir doesn't yet exist will not grant access, and the CLI EPERMs on its first `mkdir`. Keep `STATE_DIRS` in the Makefile in sync with `filesystem.allow` entries that name not-yet-existing paths.

4. **Thin entry points** — `Makefile` and `shell/nono-ai.zsh`, both calling `nono run --profile <name> -- <cli>`. No 1Password credentials injected: nono blocks `OP_SERVICE_ACCOUNT_TOKEN` (master-credential class). To restore sandboxed `op`, either grant the 1Password desktop CLI socket (`~/Library/Group Containers/2BUA8C4S2C.com.1password/t/s.sock`) in `nono-ai-base.json`, or add narrow per-secret `env_credentials` entries.

The user's `~/.zshrc` sources `shell/nono-ai.zsh`, makes plain `claude` route through `nono-claude`, and blocks unprefixed `opencode` / `cn`. The `nono-` launchers always work.

### Profile hierarchy

```
default
└── nono-ai-base          working dir, ollama, npm prefix, deny groups, no creds
    ├── nono-ai-claude    + ~/.claude{,.json}, claude_code_macos group, allow_domain ["*"]
    ├── nono-ai-opencode  + OpenCode XDG dirs, OAuth port 49152, MCP hosts + open_urls
    └── nono-ai-continue  + ~/.continue, OAuth port 49153, MCP hosts + open_urls

default
└── nono-ai-install       writes only to npm prefix; network only to registry.npmjs.org
```

### What each profile gives the CLI

- **Filesystem allow**: `~/Documents/ai`, the CLI's state dir, npm prefix (read-only).
- **Filesystem deny** (policy groups): `~/.ssh`, `~/.aws`, `~/.gnupg`, keychains, browser data, shell history, `~/.zshrc` / `~/.bashrc`.
- **Network allow**: LLM API hosts, MCP gateways + their OAuth issuers (see Sharp edges), `my.1password.com`, loopback 11434 (Ollama) and 8765 (avs-rag).
- **Env vars**: PATH, HOME, USER, SHELL, TERM, LANG, LC_*, TMPDIR, OLLAMA_HOST. No 1Password.

## Sync points

| Concern | Single source of truth |
| --- | --- |
| Filesystem & network allow-lists | `profiles/*.json` |
| State/cache dirs that must pre-exist | `Makefile` `STATE_DIRS` — mirror `filesystem.allow` entries that name not-yet-existing paths |
| Credential injection | none by default; `env_credentials` in `nono-ai-base.json` if re-enabled |
| npm prefix path | `Makefile` `PREFIX :=` |
| Entry-point invocations | `Makefile` OR `shell/nono-ai.zsh` — both call `nono run --profile <name>` (profile is authoritative, not the wrapper) |

The `nono run --profile X --` line is intentionally duplicated per CLI across `Makefile` and `shell/nono-ai.zsh` (user picks either). Profile-name changes require updates in both.

## Update model

Latest-and-rebuild. `make update` wipes `$PREFIX`, runs `npm install -g --ignore-scripts @continuedev/cli opencode-ai` under `nono-ai-install`, then runs opencode-ai's `postinstall.mjs` (Architecture §2). If an upstream regression breaks the rebuild, **temporarily** pin in the Makefile (`opencode-ai@<version>`); remove once fixed. Claude Code updates via `brew upgrade claude-code`, not `make update`.

## Sharp edges (things that look removable but aren't)

- **`read: ["$HOME/.local/share/nono-ai/npm"]` in `nono-ai-base.json`** — load-bearing: OpenCode/Continue load their `node_modules` from the prefix at startup. Claude Code is loaded from `/opt/homebrew/` via the `homebrew_macos` policy group instead.

- **`"allow_domain": ["*"]` in `nono-ai-claude.json`** — the bare `*` is honored at runtime (verified via `nono why`); the proxy passes any host. Right shape for Claude Code's open-ended WebFetch + claude.ai connector-mediated MCP gateways. **Do not** confuse with `network_profile: null` — that disables the proxy entirely and kills all network. Filesystem deny groups remain the primary defense.

- **`--config $HOME/.continue/config.yaml` in the `continue` / `nono-cn` launcher** — load-bearing. Without it, cn defaults to its hub-based assistant flow and shows the "Log in with Continue / Enter Anthropic API key" onboarding gate even when the local YAML exists and is well-formed.

- **`-auth` variants** (`make opencode-auth`, `make continue-auth`, `nono-opencode-auth`, `nono-cn-auth`) pair `--allow-launch-services` (CLI flag) with the profile's `allow_launch_services: true`, letting the CLI call `open <URL>` for the full OAuth browser dance. Without it: `_LSOpenURLsWithCompletionHandler() error -54`, "Starting OAuth flow" hangs. Kept out of default launchers because LaunchServices widens the sandbox beyond URL-opening (file-handler registrations); nono explicitly warns against daily use.

- **OAuth MCP profile rules** — silent refresh (refresh token still valid) POSTs to the OAuth **issuer** host, not the MCP host; these are often different (e.g. Consensus issuer `consensus.app` vs MCP `mcp.consensus.app`). The profile's `allow_domain` must list both, or silent refresh fails and the CLI falls through to the browser dance. nono `allow_domain` is exact-host match — no subdomain rolling.
