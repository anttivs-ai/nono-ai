# Design notes

Companion to `CLAUDE.md` and `README.md`. CLAUDE.md is the operational reference for AI tools; this file explains *why* the design is shaped the way it is. nono-ai was branched from the sister `colima-docker-ai` repo, which still exists; cross-references compare design choices between the two.

## Threat model

A user-installed AI CLI (Claude Code, OpenCode, Continue) runs as the user. It inherits the user's filesystem access, including SSH keys, cloud credentials, browser sessions, every checked-out source tree, and `~/.zshrc`. Three things can go wrong:

1. **Supply-chain compromise**: a malicious version of the CLI (or one of its dependencies) lands via npm. A postinstall hook executes arbitrary code at install time; the runtime code does whatever it wants once invoked.
2. **Prompt injection**: an attacker-crafted document the agent reads (a README, an issue comment, a webpage) talks the agent into running arbitrary shell commands.
3. **Excessive scope**: even with a well-behaved CLI, the agent can stumble into `~/.ssh/id_rsa`, exfiltrate, or rewrite `~/.zshrc` to install a persistent backdoor.

`nono` constrains scope at the kernel level (Seatbelt on macOS, Landlock on Linux). The restrictions are irrevocable once applied: no API call from inside the process tree can widen them. Child processes (MCP servers spawned by Claude Code, npm postinstall, OAuth listener) inherit. There is no equivalent of Claude Code's built-in `dangerouslyDisableSandbox` escape hatch.

This addresses (3) directly and most of (1)/(2) by limiting what an in-flight compromise can reach. It does not provide memory isolation; a kernel exploit or a vulnerability in nono itself would defeat it. For mutually-untrusted workloads a VM is still the right tool.

## Why this design instead of the colima-docker-ai one

The sister `colima-docker-ai` repo wraps the same threat model in a Linux VM + Docker container. That design works and continues to be maintained there; it pays a heavier cost:

- A Linux VM running on the user's laptop, with separate filesystem, network, and process spaces.
- Three `socat` relays per session bridging the VM's bridge IP back to host loopback (Ollama, avs-rag, a Dash MCP HTTP wrapper).
- A separate `~/.claude.json` (file-bind from `~/.config/colima-docker-ai/container-claude.json`) because the host and container need different Dash MCP URLs.
- A `--network host` mode (`make auth`) used solely to complete OAuth flows whose localhost callbacks can't otherwise reach the container.
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

### Side-by-side mechanism comparison

| What | colima-docker-ai | nono-ai |
| --- | --- | --- |
| Sandbox | Colima VM + Docker container | macOS Seatbelt via nono |
| Filesystem isolation | virtiofs allowlist mounts | profile `filesystem.allow/read/write` |
| Network isolation | bridge + three socat relays | profile `network.allow_domain` + `open_port` |
| Host loopback | socat relay → 127.0.0.1 | direct (CLI is on host) |
| `~/.claude.json` | bind to container-claude.json | shared with host install |
| OAuth callbacks | `make auth` with `--network host` | `listen_port` (49152/49153) |
| Dash MCP | mcp-proxy HTTP wrapper | direct stdio (Claude Code only) |
| 1Password token | `op read` + env-inherit to docker | not injected; opt-in path required |
| Update | `make build` (3-5 min Docker rebuild) | `make update` (npm install + postinstall) |
| Supply-chain | debsig + npm install scripts allowed | `--ignore-scripts` + sandboxed install |

## Supply chain hardening for npm

Defense in depth, two layers:

1. **`--ignore-scripts`**: Disables npm lifecycle hooks (`preinstall`, `install`, `postinstall`). A malicious package that depends on its postinstall script to run cannot execute it. The three CLIs we install (Claude Code, OpenCode, Continue) are pure-JS bundled tools and do not need lifecycle scripts. If a future upstream regression makes one of them need a script, surface it in `make update` and decide per-package whether to drop the flag.
2. **Sandboxed install** via `nono-ai-install`: even if a script ran (or a runtime exploit triggers code execution during install), the process can only write to `$HOME/.local/share/nono-ai/npm` (not to `~/.ssh`, not to PATH-relevant locations, not to other repos), can only talk to `registry.npmjs.org` (not exfiltrate to an attacker server), and can only read what nono's `node_runtime` and `homebrew` groups allow (node + brew binaries — no credentials).

Runtime execution of the installed CLIs is then constrained again by the per-CLI profile. So a malicious package has to defeat three layers (`--ignore-scripts`, install sandbox, runtime sandbox) to do real damage.

The npm prefix `$HOME/.local/share/nono-ai/npm` is **not** on the user's `$PATH`. The Makefile and shell helpers invoke binaries by absolute path (`$PREFIX/bin/claude`). This prevents a compromised CLI from leaving a malicious shim that gets picked up by an unsandboxed shell.

## Why no 1Password credentials are injected by default

The original design here was symmetric to colima-docker-ai's: inject `OP_SERVICE_ACCOUNT_TOKEN` via nono's `env_credentials` so sandboxed CLIs could call `op` for ad-hoc secret retrieval, with the token never touching the host shell environment. nono v0.57 rejects this outright — it has a hardcoded list of "dangerous environment variables" (master credentials whose presence in a sandboxed process effectively voids the sandbox), and `OP_SERVICE_ACCOUNT_TOKEN` is on that list. There is no override flag.

The current profile therefore omits `env_credentials` entirely. Two ways to put `op` access back if it becomes blocking:

1. **Per-secret narrow mappings.** Replace the would-be master-token entry with one mapping per secret the sandboxed CLI actually needs, each going to its own env var: `{"op://AI/anthropic-api/key": "ANTHROPIC_API_KEY"}`. Sandbox compromise leaks only the listed secrets, not the vault. Requires enumerating what each CLI needs.

2. **Desktop CLI socket forwarding.** Enable "Connect with 1Password CLI" in the 1Password desktop app's Developer settings, then grant `~/Library/Group Containers/2BUA8C4S2C.com.1password/t/s.sock` in `nono-ai-base.json`. `op` inside the sandbox calls the desktop app via the socket; each call prompts Touch ID on the host. No long-lived token in sandbox memory.

Option 1 is simpler to wire up; option 2 is closer to the principle of "the sandbox holds no usable credentials at rest."

## Per-CLI profile composition

All three CLIs extend `nono-ai-base` which sets the project-wide rules. Per-CLI profiles add only:

- The CLI's state directory.
- LLM/MCP domain allow-list additions.
- For OpenCode and Continue, a pinned OAuth callback port.

Claude Code additionally pulls in nono's bundled `claude_code_macos` policy group (which adds reasonable Claude Code defaults that we don't have to maintain). The bundled `claude-code` network profile is **not** used: it registered managed-credential routes for anthropic/github/gitlab that produced unsilenceable "credential not found" warnings on every run (Claude Code uses OAuth, not API key, and we don't push to git from the sandbox). Instead the profile lists Anthropic / claude.ai / MCP-gateway hosts explicitly in `allow_domain`.

The profiles are plain JSON (nono v0.57's parser rejects JSONC `//` comments), under 50 lines each. Profile-internal documentation lives in `meta.description`. Inheritance is shallow — one level deep. There is no profile-of-profiles or runtime override; if a project needs a tighter or looser policy, create a new profile that extends `nono-ai-base` and run `nono run --profile <new-name> -- <cli>` directly.

## Open verification items

These were not fully confirmed against the nono docs at design time and should be checked during the first end-to-end run:

- Whether `listen_port` accepts a fixed integer (assumed) or a range. If ranges work, widen OpenCode/Continue to a small range (e.g., `49152-49199`) for easier interleaving; if not, pinned single ports are the alternative.
- Whether nono's bundled `claude-code` network profile already includes `claude.ai`. The explicit `allow_domain` in `nono-ai-claude.json` covers this regardless.
- Whether the `homebrew_macos` policy group grants read-only access to `/opt/homebrew`. If it grants writes, replace with a narrower explicit entry.
- (resolved) `env_credentials` was rejected by nono for `OP_SERVICE_ACCOUNT_TOKEN` regardless of source; the mapping was removed. See "Why no 1Password credentials are injected by default" above.
- (resolved) nono v0.57 silently drops not-yet-existing host paths from a profile's effective capabilities — confirmed during the first end-to-end opencode run, which EPERMed on `~/.cache/opencode` even though the profile listed it. The Makefile's `state` target (run as part of `make install`) pre-creates the per-CLI XDG dirs to make this deterministic.
- (resolved) opencode-ai's npm tarball ships only stub binaries; the published `bin/opencode` and `bin/opencode.exe` are 479-byte error stubs printed by an error script. The real ~107 MB platform binary is placed by `postinstall.mjs`, which `--ignore-scripts` blocks. The Makefile's `update` target now invokes that postinstall explicitly under `nono-ai-install` after the npm install completes.

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
| Env-inheritance / stdin-pipe token transport | nono blocks the master service-account token outright; no replacement injection. Sandboxed `op` is opt-in (see "Why no 1Password credentials are injected by default"). |
