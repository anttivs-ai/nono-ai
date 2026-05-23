# Design notes

Companion to `CLAUDE.md` and `README.md`. CLAUDE.md is the operational reference for AI tools; this file explains *why* the design is shaped the way it is. Analogue of `docker-config.md` in the sister repo.

## Threat model

A user-installed AI CLI (Claude Code, OpenCode, Continue) runs as the user. It inherits the user's filesystem access, including SSH keys, cloud credentials, browser sessions, every checked-out source tree, and `~/.zshrc`. Three things can go wrong:

1. **Supply-chain compromise**: a malicious version of the CLI (or one of its dependencies) lands via npm. A postinstall hook executes arbitrary code at install time; the runtime code does whatever it wants once invoked.
2. **Prompt injection**: an attacker-crafted document the agent reads (a README, an issue comment, a webpage) talks the agent into running arbitrary shell commands.
3. **Excessive scope**: even with a well-behaved CLI, the agent can stumble into `~/.ssh/id_rsa`, exfiltrate, or rewrite `~/.zshrc` to install a persistent backdoor.

`nono` constrains scope at the kernel level (Seatbelt on macOS, Landlock on Linux). The restrictions are irrevocable once applied: no API call from inside the process tree can widen them. Child processes (MCP servers spawned by Claude Code, npm postinstall, OAuth listener) inherit. There is no equivalent of Claude Code's built-in `dangerouslyDisableSandbox` escape hatch.

This addresses (3) directly and most of (1)/(2) by limiting what an in-flight compromise can reach. It does not provide memory isolation; a kernel exploit or a vulnerability in nono itself would defeat it. For mutually-untrusted workloads a VM is still the right tool.

## Why this is preferable to the colima-docker-ai design for this use case

The colima-docker-ai repo wraps the same threat model in a Linux VM + Docker container. That works, but pays heavy costs:

- A Linux VM running on the user's laptop, with separate filesystem, network, and process spaces.
- Three `socat` relays per session bridging the VM's bridge IP back to host loopback (Ollama, avs-rag, a Dash MCP HTTP wrapper).
- A separate `~/.claude.json` (file-bind from `~/.config/colima-docker-ai/container-claude.json`) because the host and container needed different Dash MCP URLs.
- A `--network host` mode (`make auth`) used solely to complete OAuth flows whose localhost callbacks couldn't otherwise reach the container.
- A `--no-cache --pull` Docker rebuild on every update (3-5 minutes).
- A UID/GID dance (501:20) so virtiofs preserves file ownership.

nono runs the CLI directly on the host with a kernel-enforced allow-list. As a result:

- No VM. Host loopback IS reachable as `127.0.0.1` directly — Ollama, avs-rag, Dash MCP all work natively without relays.
- No HTTP wrapper around Dash MCP — Claude Code calls it via stdio just like the host installation does.
- No separate `.claude.json` — sandboxed Claude Code shares the host's. Same MCP servers, same credentials, same project state.
- No `--network host` mode for OAuth — the listener binds to `127.0.0.1:<port>` directly on the host (allowed by `listen_port`), and the browser callback lands.
- Updates are an `npm install`, not a full Docker rebuild.
- No UID/GID gymnastics — all processes run as the user.

The trade-off: kernel sandboxing controls filesystem and network access but not memory; a Docker container provides slightly stronger isolation against kernel-adjacent attacks. For the threat model described above (a CLI doing things it shouldn't, not a hostile attacker with arbitrary code execution targeting the kernel), this is the right trade.

## Supply chain hardening for npm

Defense in depth, two layers:

1. **`--ignore-scripts`**: Disables npm lifecycle hooks (`preinstall`, `install`, `postinstall`). A malicious package that depends on its postinstall script to run cannot execute it. The three CLIs we install (Claude Code, OpenCode, Continue) are pure-JS bundled tools and do not need lifecycle scripts. If a future upstream regression makes one of them need a script, surface it in `make update` and decide per-package whether to drop the flag.
2. **Sandboxed install** via `nono-ai-install`: even if a script ran (or a runtime exploit triggers code execution during install), the process can only write to `$HOME/.local/share/nono-ai/npm` (not to `~/.ssh`, not to PATH-relevant locations, not to other repos), can only talk to `registry.npmjs.org` (not exfiltrate to an attacker server), and can only read what nono's `node_runtime` and `homebrew` groups allow (node + brew binaries — no credentials).

Runtime execution of the installed CLIs is then constrained again by the per-CLI profile. So a malicious package has to defeat three layers (`--ignore-scripts`, install sandbox, runtime sandbox) to do real damage.

The npm prefix `$HOME/.local/share/nono-ai/npm` is **not** on the user's `$PATH`. The Makefile and shell helpers invoke binaries by absolute path (`$PREFIX/bin/claude`). This prevents a compromised CLI from leaving a malicious shim that gets picked up by an unsandboxed shell.

## Why `env_credentials` over env-inheritance

The Docker setup captures `OP_SERVICE_ACCOUNT_TOKEN` via `op read` on the host shell and passes it to the container via env-var inheritance (`docker run -e OP_SERVICE_ACCOUNT_TOKEN`, no `=value`). This is the safest pattern docker offers because the token never appears on a command line.

nono offers something better: `env_credentials` reads an `op://...` URI at process startup and injects the resulting secret as a named env var inside the sandbox. The token never enters the user's shell environment, never appears in `op read` output, and never crosses any IPC boundary the user controls. The 1Password desktop app's existing auth (the same one used by the colima-docker-ai `op read`) provides the credential.

The cost: the `op://...` URI is hardcoded in `profiles/nono-ai-base.jsonc`. Users with a different vault entry edit the profile. The Docker setup's `OP_ITEM` runtime override is lost — but the cleaner static configuration is worth it.

## Per-CLI profile composition

All three CLIs extend `nono-ai-base` which sets the project-wide rules. Per-CLI profiles add only:

- The CLI's state directory.
- LLM/MCP domain allow-list additions.
- For OpenCode and Continue, a pinned OAuth callback port.

Claude Code additionally pulls in nono's bundled `claude_code_macos` policy group (which adds reasonable Claude Code defaults that we don't have to maintain) and the `claude-code` network profile.

The profiles are JSONC (JSON with comments), under 50 lines each. Inheritance is shallow — one level deep. There is no profile-of-profiles or runtime override; if a project needs a tighter or looser policy, create a new profile that extends `nono-ai-base` and run `nono run --profile <new-name> -- <cli>` directly.

## Open verification items

These were not fully confirmed against the nono docs at design time and should be checked during the first end-to-end run:

- Whether `listen_port` accepts a fixed integer (assumed) or a range. If ranges work, widen OpenCode/Continue to a small range (e.g., `49152-49199`) for easier interleaving; if not, pinned single ports are the alternative.
- Whether nono's bundled `claude-code` network profile already includes `claude.ai`. The explicit `allow_domain` in `nono-ai-claude.jsonc` covers this regardless.
- Whether the `homebrew` policy group grants read-only access to `/opt/homebrew`. If it grants writes, replace with a narrower explicit entry.
- Whether `env_credentials` resolves the `op://` URI via the macOS 1Password desktop app and not via `OP_SERVICE_ACCOUNT_TOKEN` (which would be circular, since that's the variable being injected).

## What is NOT carried over from colima-docker-ai

| Dropped | Why |
| --- | --- |
| `Dockerfile`, `docker/entrypoint.sh` | No image to build. |
| `colima-setup.md`, `colima-ai` shell function | No VM. |
| Three socat relays | Host loopback reachable natively. |
| `dash-mcp` shell function and `mcp-proxy` HTTP wrapper | Sandboxed Claude calls Dash via stdio just like host Claude does. |
| `docker-ai-auth` / `make auth` | OAuth callbacks land natively under `listen_port`. |
| Separate `container-claude.json` file bind | `~/.claude.json` shared with host install. |
| UID/GID matching (501:20) | Processes run as the user. |
| 1Password debsig signature verification | `op` is host-installed via brew. |
| Env-inheritance / stdin-pipe token transport | `env_credentials` does this natively. |
