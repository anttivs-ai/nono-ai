PREFIX       := $(HOME)/.local/share/nono-ai/npm
PROFILES_DIR := $(HOME)/.config/nono/profiles

# bash with `set -eu -o pipefail` so any failure in a recipe aborts the line
# instead of silently continuing. There are no `op read` pipelines here
# (nono handles credential fetching), but the strict mode is still useful for
# the `update` recipe's two-step rm-and-mkdir.
SHELL       := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

.PHONY: install profiles update claude opencode continue check clean

# `make install` is the equivalent of the colima-docker-ai `make build`:
# materializes everything the user needs before first run.
install: profiles update

# Copy versioned profile templates into nono's user-profile dir. nono resolves
# `extends: nono-ai-base` by looking here.
profiles:
	mkdir -p $(PROFILES_DIR)
	cp profiles/*.json $(PROFILES_DIR)/

# Rebuild the dedicated, isolated npm prefix from scratch. The install runs
# inside the `nono-ai-install` profile:
#   * filesystem writes confined to $(PREFIX),
#   * network limited to registry.npmjs.org,
#   * --ignore-scripts blocks any postinstall hooks before they can even
#     attempt to do anything.
# A compromised postinstall hook thus has neither a way to run, nor a way
# to write outside $(PREFIX), nor a way to phone home.
update:
	rm -rf $(PREFIX) && mkdir -p $(PREFIX)
	nono run --profile nono-ai-install -- \
	  npm install --prefix=$(PREFIX) --cache=$(PREFIX)/cache --global --ignore-scripts \
	    @continuedev/cli opencode-ai

# Per-CLI run targets. No 1Password credentials are injected into the sandbox
# (nono blocks the master service-account token, and the desktop-CLI socket
# is not forwarded by default). To use `op` inside a sandboxed CLI, either
# add narrow per-secret entries under `env_credentials` in nono-ai-base.json
# or forward the 1Password desktop agent socket.
claude:
	nono run --profile nono-ai-claude    -- /opt/homebrew/bin/claude

opencode:
	nono run --profile nono-ai-opencode  -- $(PREFIX)/bin/opencode

continue:
	nono run --profile nono-ai-continue  -- $(PREFIX)/bin/cn

# Smoke-test that each CLI launches. Run after `make update`. These bypass
# nono on purpose: --version doesn't need any of the sandboxed paths, and
# a failure here means the install itself is broken (not a profile mistake).
check:
	/opt/homebrew/bin/claude --version
	$(PREFIX)/bin/opencode --version
	$(PREFIX)/bin/cn --version

# Wipe the npm prefix. The next `make update` rebuilds it.
clean:
	rm -rf $(PREFIX)
