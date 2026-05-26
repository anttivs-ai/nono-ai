PREFIX       := $(HOME)/.local/share/nono-ai/npm
PROFILES_DIR := $(HOME)/.config/nono/profiles

# Per-CLI state/cache/config dirs that the profiles `allow`. nono silently
# drops non-existent paths from a profile's effective capabilities at runtime
# (verified on v0.57), so any path the CLI will try to mkdir at startup must
# already exist on the host or the CLI crashes with EPERM. The `state` target
# pre-creates them; keep this list in sync with profiles/*.json.
STATE_DIRS := \
  $(HOME)/.config/opencode \
  $(HOME)/.local/share/opencode \
  $(HOME)/.local/state/opencode \
  $(HOME)/.cache/opencode \
  $(HOME)/.continue \
  $(HOME)/.mcp-auth

# bash with `set -eu -o pipefail` so any failure in a recipe aborts the line
# instead of silently continuing. There are no `op read` pipelines here
# (nono handles credential fetching), but the strict mode is still useful for
# the multi-step `update` and `state` recipes.
SHELL       := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

.PHONY: install profiles state update opencode-postinstall dash-mcp-cache claude opencode opencode-auth continue continue-auth check clean

# `make install` is everything a fresh host needs before first run:
#   1. state    — mkdir the per-CLI state/cache dirs so nono can grant them
#   2. profiles — copy the versioned profile templates to ~/.config/nono/profiles
#   3. update   — build the isolated npm prefix and run opencode-ai's postinstall
install: state profiles update

# Pre-create the dirs listed in STATE_DIRS. Idempotent; safe to re-run.
state:
	mkdir -p $(STATE_DIRS)

# Copy versioned profile templates into nono's user-profile dir. nono resolves
# `extends: nono-ai-base` by looking here. We also delete any stale
# `nono-ai-*.jsonc` left over from before the v0.57 JSONC→JSON migration —
# nono v0.57's parser rejects `//` comments, and having both *.json and
# *.jsonc in the dir is confusing even if nono ignores the latter.
profiles:
	mkdir -p $(PROFILES_DIR)
	rm -f $(PROFILES_DIR)/nono-ai-*.jsonc
	cp profiles/*.json $(PROFILES_DIR)/

# Rebuild the dedicated, isolated npm prefix from scratch. The npm install
# runs inside the `nono-ai-install` profile:
#   * filesystem writes confined to $(PREFIX) and $(TMPDIR),
#   * network limited to registry.npmjs.org,
#   * --ignore-scripts blocks any postinstall hooks before they can run.
# A compromised postinstall hook thus has no way to run during npm install,
# no way to write outside $(PREFIX), and no way to phone home.
#
# However: opencode-ai ships only stub binaries in the published tarball; the
# real ~107 MB platform binary is fetched/copied into place by its
# `postinstall.mjs` script. With --ignore-scripts that step is skipped and
# `$(PREFIX)/bin/opencode` is a 479-byte error stub. We therefore run that
# script explicitly under the same install sandbox (`opencode-postinstall`)
# so its filesystem and network are still confined.
update:
	rm -rf $(PREFIX) && mkdir -p $(PREFIX)
	nono run --profile nono-ai-install -- \
	  npm install --prefix=$(PREFIX) --cache=$(PREFIX)/cache --global --ignore-scripts \
	    @continuedev/cli opencode-ai mcp-remote
	$(MAKE) opencode-postinstall
	$(MAKE) dash-mcp-cache

# Run opencode-ai's postinstall.mjs under the install profile so the real
# platform binary replaces the stub. On a normally-laid-out fresh install,
# npm has already placed the platform-specific package (e.g.
# `opencode-darwin-arm64`) under opencode-ai/node_modules/ as an
# optionalDependency, so the script just hard-links its binary across — no
# extra network round-trip needed. On a host where that didn't happen, the
# script falls back to `npm install` of the platform package, which is
# why this still runs under nono-ai-install (network → registry.npmjs.org).
#
# The postinstall ends with a `verifyBinary()` that spawns the new binary
# with --version. That spawn inherits the install-profile sandbox, which
# does NOT grant opencode's XDG state/cache dirs (those are runtime concerns,
# kept out of the install profile to preserve its narrow scope). So even on
# a successful copy the script throws "package manager failed to install"
# and exits 1. The `-` prefix tells make to tolerate that exit code; the
# trailing size check is the authoritative success signal — a real binary
# is ~107 MB, the stub is 479 bytes.
OPENCODE_BIN := $(PREFIX)/lib/node_modules/opencode-ai/bin/opencode.exe
opencode-postinstall:
	-nono run --profile nono-ai-install -- \
	  node $(PREFIX)/lib/node_modules/opencode-ai/postinstall.mjs
	@size=$$(/usr/bin/stat -f%z $(OPENCODE_BIN) 2>/dev/null || echo 0); \
	if [ $$size -lt 1000000 ]; then \
	  echo "opencode-postinstall: $(OPENCODE_BIN) is still a stub ($$size bytes) — install failed" >&2; \
	  exit 1; \
	fi; \
	echo "opencode-postinstall: $(OPENCODE_BIN) is $$size bytes (real platform binary)"

# Refresh the uvx-managed dash-mcp-server cache so subsequent sandboxed
# `uvx --offline dash-mcp-server` invocations (OpenCode and Continue use
# this as a local stdio MCP) pick up the latest published version. Runs
# OUTSIDE nono so uvx can reach PyPI directly — the per-CLI sandbox
# profiles intentionally do not list PyPI in allow_domain. Non-fatal if
# offline: any previously cached version stays usable via --offline.
dash-mcp-cache:
	@if command -v uvx >/dev/null 2>&1; then \
	  if uvx dash-mcp-server --help >/dev/null 2>&1; then \
	    echo "dash-mcp-cache: uvx cache for dash-mcp-server refreshed"; \
	  else \
	    echo "dash-mcp-cache: WARNING — uvx couldn't refresh dash-mcp-server (offline?); existing cache will be used" >&2; \
	  fi; \
	else \
	  echo "dash-mcp-cache: WARNING — uvx not on PATH; skipping (install uv via brew if you want stdio Dash MCP)" >&2; \
	fi

# Per-CLI run targets. No 1Password credentials are injected into the sandbox
# (nono blocks the master service-account token, and the desktop-CLI socket
# is not forwarded by default). To use `op` inside a sandboxed CLI, either
# add narrow per-secret entries under `env_credentials` in nono-ai-base.json
# or forward the 1Password desktop agent socket.
claude:
	nono run --profile nono-ai-claude    -- /opt/homebrew/bin/claude

opencode:
	nono run --profile nono-ai-opencode  -- $(PREFIX)/bin/opencode

# OAuth-aware variant: pairs the profile's `allow_launch_services: true`
# with the `--allow-launch-services` CLI gate so opencode can call
# `open <URL>` during the full OAuth dance (browser opens, user
# authenticates, callback hits 127.0.0.1:49152). Only needed when the
# refresh-token grant has also expired — the silent refresh path doesn't
# require LaunchServices. nono prints a warning when this gate is on;
# use this target sparingly, since LaunchServices widens the sandbox
# beyond URL-opening (file handlers via registered apps).
# Usage: make opencode-auth MCP=consensus
opencode-auth:
	nono run --profile nono-ai-opencode --allow-launch-services -- $(PREFIX)/bin/opencode mcp auth $(MCP)

# cn defaults to its hub-based assistant flow and shows the "Log in with
# Continue / Enter Anthropic API key" onboarding even when ~/.continue/
# config.yaml exists. `--config` forces it to load the local YAML and
# skips the gate. Override with your own `--config` on the command line
# if you want a hub assistant for a particular session.
continue:
	nono run --profile nono-ai-continue  -- $(PREFIX)/bin/cn --config $(HOME)/.continue/config.yaml

# OAuth-aware variant for cn. cn has no explicit `auth` subcommand;
# the OAuth flow fires when an MCP returns 401 mid-session. Launch this
# when you know an MCP refresh has fully expired and a browser round-trip
# is required. Once tokens are refreshed, switch back to `make continue`.
# Same LaunchServices caveat as opencode-auth.
continue-auth:
	nono run --profile nono-ai-continue --allow-launch-services -- $(PREFIX)/bin/cn --config $(HOME)/.continue/config.yaml

# Smoke-test that each CLI launches. Run after `make update`. These bypass
# nono on purpose: --version doesn't need any of the sandboxed paths, and
# a failure here means the install itself is broken (not a profile mistake).
check:
	/opt/homebrew/bin/claude --version
	$(PREFIX)/bin/opencode --version
	$(PREFIX)/bin/cn --version

# Wipe the npm prefix. The next `make update` rebuilds it (including the
# opencode-ai postinstall step). State dirs created by `make state` are
# left alone — they hold per-CLI history/cache, not install artifacts.
clean:
	rm -rf $(PREFIX)
