#!/usr/bin/env python3
"""ai-model — manage local Ollama models and keep the two host configs in
sync that the nono-ai sandboxed CLIs consume.

Touches:
  * Ollama itself        — `ollama pull` / `ollama rm`
  * ~/.config/opencode/opencode.json   — provider.ollama.models map
  * ~/.continue/config.yaml            — models: list (preserving comments)

Usage:
  ai-model add    <model[:tag]>            # ollama pull, add to both configs
  ai-model remove <model[:tag]>            # remove from both configs, ollama rm
  ai-model select <model> [<small_model>]  # set defaults: OpenCode model +
                                           # small_model; Continue list head.
                                           # Both must already be configured.
  ai-model list                            # show currently-configured models

`add` uses conservative Continue defaults (chat/edit/apply/autocomplete roles,
16k ctx, temp 0.2). Embedding-only models or finer-tuned roles need a manual
follow-up edit. `remove` does NOT update opencode.json's top-level `model` /
`small_model` defaults — if you remove the currently-default model the CLI
will fail at launch until you point those at a different one.
"""
# The `from __future__ import annotations` line defers annotation evaluation so
# we can use `str | None` style unions on Python 3.11 without runtime cost
# (PEP 604 is fine at runtime in 3.10+ but this keeps things forward-portable
# and avoids any cycle-import surprises).
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

# ruamel.yaml is the YAML library we use because it preserves comments,
# blank lines, and key ordering on round-trip — critical for editing the
# user's hand-curated ~/.continue/config.yaml without losing structure.
# PyYAML, the more common library, would strip all comments. We don't ship
# ruamel as an installed dependency; the `ai-model` zsh wrapper invokes us
# through `uvx --with ruamel.yaml`, so the package is fetched into uvx's
# cache on first run and reused thereafter (no permanent install).
try:
    from ruamel.yaml import YAML
except ImportError:
    # Standalone invocation (`python ai-model.py …`) without uvx will land
    # here. Give a clear hint about the supported invocation rather than
    # letting Python's default ImportError traceback confuse the user.
    sys.exit(
        "ai-model: needs ruamel.yaml — invoke via the `ai-model` zsh wrapper "
        "which runs this through `uvx --with ruamel.yaml`."
    )

# Canonical host paths for the two config files. Same paths the nono profiles
# allow read/write access to. We resolve from $HOME rather than hardcoded
# /Users/avs so the script works on any developer's box.
OPENCODE_CONFIG = Path.home() / ".config/opencode/opencode.json"
CONTINUE_CONFIG = Path.home() / ".continue/config.yaml"

# Continue's `apiBase` for any new Ollama model entry. The cn binary runs
# directly on the host under nono, so host loopback is reachable as
# 127.0.0.1 — no bridge hostname needed. nono-ai-base grants outbound
# loopback on port 11434, which is what permits the request to reach the
# host's Ollama daemon from inside the sandbox.
CONTINUE_API_BASE = "http://127.0.0.1:11434"


def _yaml() -> YAML:
    # Build a YAML instance configured for round-trip mode (the default).
    # `preserve_quotes` keeps quote-style on string scalars; without it ruamel
    # may strip quotes the user explicitly wrote. `width = 4096` effectively
    # disables line wrapping so long URLs (e.g. MCP gateway endpoints)
    # don't get folded across lines, which would change diff noise on edits.
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    return y


# ---------- OpenCode ----------
#
# Shape of ~/.config/opencode/opencode.json that we edit:
#
#     {
#       "$schema": "https://opencode.ai/config.json",
#       "provider": {
#         "ollama": {
#           "npm": "@ai-sdk/openai-compatible",
#           "name": "Ollama (host loopback)",
#           "options": {"baseURL": "http://127.0.0.1:11434/v1"},
#           "models": {
#             "qwen3-coder:30b": {"name": "qwen3-coder:30b"},
#             "llama3.2:3b":     {"name": "llama3.2:3b"}
#           }
#         }
#       },
#       "model":       "ollama/qwen3-coder:30b",     # default for main calls
#       "small_model": "ollama/qwen3-coder:30b",     # default for title-gen etc.
#       "share":       "disabled",
#       "autoupdate":  false,
#       "mcp":         { ... }                       # untouched here
#     }
#
# `opencode_*` helpers all operate on `provider.ollama.models` (a dict keyed
# by model tag) and the top-level `model` / `small_model` defaults.

def opencode_add(model: str) -> None:
    # File-missing is a soft error: we print a hint and continue (the caller
    # may still want the Continue side updated even if OpenCode isn't set up
    # on this host).
    if not OPENCODE_CONFIG.exists():
        print(f"ai-model: {OPENCODE_CONFIG} missing — skipping OpenCode", file=sys.stderr)
        return
    # Plain json.load is fine here — JSON has no comments to preserve and the
    # file is small. Order of keys is preserved by Python 3.7+ dicts on dump.
    data = json.loads(OPENCODE_CONFIG.read_text())
    # `setdefault` chain creates the provider.ollama.models tree if any link
    # is missing — defensive against a fresh opencode.json that doesn't yet
    # have the provider configured. This matches the install-from-scratch
    # path documented in README.md.
    models = data.setdefault("provider", {}).setdefault("ollama", {}).setdefault("models", {})
    # Idempotent: skip cleanly if the user already ran `add` for this model.
    # We could overwrite, but that risks blowing away custom `name` strings
    # the user set by hand.
    if model in models:
        print(f"ai-model: {model} already in OpenCode config")
        return
    # Default `name` to the model tag itself — purely cosmetic in OpenCode's
    # UI; user can change it with a manual edit later if they want a friendly
    # name like "Qwen3 Coder 30B".
    models[model] = {"name": model}
    # Trailing newline mirrors how the file was originally written by hand,
    # keeping diffs minimal.
    OPENCODE_CONFIG.write_text(json.dumps(data, indent=2) + "\n")
    print(f"ai-model: added {model} to OpenCode ({OPENCODE_CONFIG})")


def opencode_remove(model: str) -> None:
    # Silent on missing file — symmetric with opencode_add, but also because
    # this function may be called during a cleanup pass where best-effort is
    # the right behavior.
    if not OPENCODE_CONFIG.exists():
        return
    data = json.loads(OPENCODE_CONFIG.read_text())
    # Plain `.get(...)` chain (not setdefault) — we don't want to mutate the
    # file just because we tried to remove from a non-existent provider tree.
    models = data.get("provider", {}).get("ollama", {}).get("models", {})
    # No-op branch when the model isn't there. Distinct from the "skipping"
    # message in `opencode_add`: here we tell the user the model simply
    # wasn't in the config, which is useful when reconciling drift.
    if model not in models:
        print(f"ai-model: {model} not in OpenCode config")
        return
    del models[model]
    OPENCODE_CONFIG.write_text(json.dumps(data, indent=2) + "\n")
    print(f"ai-model: removed {model} from OpenCode")
    # After removing, the top-level `model` / `small_model` fields may still
    # reference the removed name. We don't fix that automatically (the user
    # may want to pick a specific replacement), but we surface a warning so
    # they don't discover it only when OpenCode crashes on next launch.
    for key in ("model", "small_model"):
        # `ollama/<tag>` is OpenCode's provider-qualified form for our local
        # provider. Compare against that, not the bare tag.
        if data.get(key) == f"ollama/{model}":
            print(
                f"ai-model: WARNING — opencode.json {key!r} still points at "
                f"'ollama/{model}'; edit it before next launch.",
                file=sys.stderr,
            )


def opencode_list() -> list[str]:
    # Returns the configured Ollama model tags (in insertion order, since
    # JSON dicts are ordered). Used both by `cmd_list` and as a precondition
    # check in `cmd_select` (to refuse selecting a not-yet-added model).
    if not OPENCODE_CONFIG.exists():
        return []
    data = json.loads(OPENCODE_CONFIG.read_text())
    # The `or {}` guard handles the edge case where `models` is explicitly
    # `null` in the JSON — without it, `.keys()` would raise on None.
    return list((data.get("provider", {}).get("ollama", {}).get("models", {}) or {}).keys())


def opencode_set_defaults(model: str, small_model: str) -> None:
    # Called only by `cmd_select`, after preconditions are verified — so we
    # don't bother re-checking that the file exists or the model is in the
    # provider tree. Just overwrite the two top-level fields.
    data = json.loads(OPENCODE_CONFIG.read_text())
    data["model"] = f"ollama/{model}"
    data["small_model"] = f"ollama/{small_model}"
    OPENCODE_CONFIG.write_text(json.dumps(data, indent=2) + "\n")
    print(f"ai-model: OpenCode  model='ollama/{model}'  small_model='ollama/{small_model}'")


# ---------- Continue ----------
#
# Shape of ~/.continue/config.yaml that we edit:
#
#     name: Local Assistant
#     version: 1.0.0
#     schema: v1
#
#     models:                                          # the list we mutate
#       - name: qwen-coder 30B (chat)                  # human label
#         provider: ollama
#         model: qwen3-coder:30b                       # tag that matches ollama
#         apiBase: http://127.0.0.1:11434
#         roles: [chat, edit, apply, autocomplete]
#         capabilities: [tool_use]
#         defaultCompletionOptions:
#           contextLength: 16384
#           temperature: 0.2
#           keepAlive: -1
#       - name: Nomic Embed
#         provider: ollama
#         model: nomic-embed-text
#         apiBase: http://127.0.0.1:11434
#         roles: [embed]
#
#     mcpServers:                                      # left alone here
#       - name: dash
#         type: stdio                                  # nono-ai uses Dash via
#         command: dash-mcp-server                     # stdio, no HTTP wrapper
#
# Continue picks the first entry in `models:` with role `chat` as the default
# chat model; that's the property `continue_set_default` exploits.

def continue_add(model: str) -> None:
    # Same soft-skip pattern as opencode_add: missing file → warn and return.
    if not CONTINUE_CONFIG.exists():
        print(f"ai-model: {CONTINUE_CONFIG} missing — skipping Continue", file=sys.stderr)
        return
    yaml = _yaml()
    # `yaml.load(path)` reads + parses; the returned `data` is a
    # ruamel.yaml.comments.CommentedMap that behaves like a dict but tracks
    # the comments, blank lines, and key order so they can be re-emitted.
    data = yaml.load(CONTINUE_CONFIG)
    # `models:` is a list of dicts (CommentedSeq of CommentedMaps); add the
    # key with an empty list if it doesn't exist yet.
    models = data.setdefault("models", [])
    # Idempotent guard: scan the existing entries for a matching
    # (provider="ollama", model=<tag>) pair. Skip the add if found so we
    # don't end up with duplicate entries.
    if any(
        isinstance(m, dict) and m.get("model") == model and m.get("provider") == "ollama"
        for m in models
    ):
        print(f"ai-model: {model} already in Continue config")
        return
    # Sensible defaults for a coding-oriented chat model. The four roles
    # cover the common Continue features (chat conversation, inline edit,
    # apply edits, and autocomplete). `tool_use` capability is needed for
    # MCP tool-calling. The context length and temperature are conservative
    # — the user can hand-tune later via the YAML file directly.
    entry = {
        "name": model,
        "provider": "ollama",
        "model": model,
        "apiBase": CONTINUE_API_BASE,
        "roles": ["chat", "edit", "apply", "autocomplete"],
        "capabilities": ["tool_use"],
        "defaultCompletionOptions": {
            "contextLength": 16384,
            "temperature": 0.2,
            # keepAlive: -1 tells Ollama to keep the model resident in
            # VRAM/RAM indefinitely after the first request. With one model
            # at a time (OLLAMA_MAX_LOADED_MODELS=1), this means we pay the
            # warm-up cost once per session instead of per request.
            "keepAlive": -1,
        },
    }
    models.append(entry)
    # `yaml.dump(data, file)` serializes back, preserving the original
    # comments + ordering captured during `load`. We open the file in
    # write-text mode and let ruamel emit.
    with CONTINUE_CONFIG.open("w") as f:
        yaml.dump(data, f)
    print(f"ai-model: added {model} to Continue ({CONTINUE_CONFIG})")


def continue_remove(model: str) -> None:
    if not CONTINUE_CONFIG.exists():
        return
    yaml = _yaml()
    data = yaml.load(CONTINUE_CONFIG)
    # `or []` defends against `models:` being explicitly null in the YAML.
    models = data.get("models", []) or []
    # Filter out the matching Ollama entry. We use a list comprehension
    # rather than .remove() because the latter is by-identity for dicts and
    # would also raise if not found; comprehension is clearer and lets us
    # detect no-op via the length comparison below.
    kept = [
        m for m in models
        if not (
            isinstance(m, dict) and m.get("model") == model and m.get("provider") == "ollama"
        )
    ]
    # If nothing was filtered out, the model wasn't there — tell the user
    # and return without touching disk (idempotent + minimal diff).
    if len(kept) == len(models):
        print(f"ai-model: {model} not in Continue config")
        return
    # Reassign rather than mutate: this is fine for our usage because
    # nothing else holds a reference to the old list inside `data`.
    data["models"] = kept
    with CONTINUE_CONFIG.open("w") as f:
        yaml.dump(data, f)
    print(f"ai-model: removed {model} from Continue")


def continue_list() -> list[str]:
    if not CONTINUE_CONFIG.exists():
        return []
    yaml = _yaml()
    data = yaml.load(CONTINUE_CONFIG)
    # Return tags of Ollama-provider entries only. `m.get("model")` falsy
    # guard skips malformed entries that lack the required field.
    return [
        m["model"] for m in (data.get("models") or [])
        if isinstance(m, dict) and m.get("provider") == "ollama" and m.get("model")
    ]


def continue_set_default(model: str) -> None:
    """Move the Ollama entry for `model` to the head of the models: list."""
    yaml = _yaml()
    data = yaml.load(CONTINUE_CONFIG)
    models = data.get("models") or []
    # Walk the list once, splitting it into "the entry we want first" and
    # "everything else, in original order". We stop accepting the selected
    # entry once we've grabbed one — if the user has accidentally duplicated
    # a model, only the first match is promoted and any duplicates remain
    # in their original positions.
    selected, others = None, []
    for m in models:
        if (
            isinstance(m, dict) and m.get("provider") == "ollama"
            and m.get("model") == model and selected is None
        ):
            # This branch fires exactly once per call (guarded by `selected
            # is None`), making the result stable for duplicated entries.
            selected = m
        else:
            # Everything else — non-matching, non-dict, wrong provider, or
            # the duplicate copy of the selected — keeps its original spot
            # relative to the others.
            others.append(m)
    # Defensive: `cmd_select` already verified the model is in
    # `continue_list()`, so this should never fire in normal use. If it
    # does, refuse to write — better to bail than silently corrupt the file.
    if selected is None:
        sys.exit(f"ai-model: {model} not found in Continue config when reordering")
    data["models"] = [selected, *others]
    with CONTINUE_CONFIG.open("w") as f:
        yaml.dump(data, f)
    print(f"ai-model: Continue  default → '{model}' (moved to head of models list)")


# ---------- Ollama ----------

def ollama_pull(model: str) -> int:
    # `subprocess.call` runs `ollama pull <model>` inheriting our stdio so
    # the user sees the live progress bar. Return code propagates up so
    # `cmd_add` can refuse to mutate configs if the pull failed (e.g. typo
    # in tag, network error).
    return subprocess.call(["ollama", "pull", model])


def ollama_rm(model: str) -> int:
    # Symmetric to ollama_pull; `ollama rm` is quick and quiet on success.
    return subprocess.call(["ollama", "rm", model])


# ---------- command dispatch ----------

def cmd_add(model: str) -> int:
    # Pull first, then mutate configs only on success. This ordering matters:
    # we don't want a stale config entry pointing at a model that doesn't
    # actually exist in the local Ollama install.
    rc = ollama_pull(model)
    if rc != 0:
        # Ollama already printed a meaningful error; we add context about
        # why this aborts the whole `add` workflow.
        print(
            f"ai-model: 'ollama pull {model}' exited {rc}; not modifying configs",
            file=sys.stderr,
        )
        return rc
    opencode_add(model)
    continue_add(model)
    return 0


def cmd_remove(model: str) -> int:
    # Reverse order from `add`: remove from configs first (cheap, can't fail
    # in ways that matter), then drop the model from Ollama. Even if
    # `ollama rm` fails (already gone, etc.), the configs are already clean.
    opencode_remove(model)
    continue_remove(model)
    rc = ollama_rm(model)
    if rc != 0:
        # Don't treat this as a hard failure — the most common cause is
        # "model wasn't installed in the first place", which is fine.
        print(
            f"ai-model: 'ollama rm {model}' exited {rc} (model may already be gone)",
            file=sys.stderr,
        )
    return 0


def cmd_list() -> int:
    # Two-line summary so the user can eyeball drift between configs.
    # `.name` shows just the filename for compactness (the full path is in
    # the script's docstring).
    print(f"OpenCode  ({OPENCODE_CONFIG.name}): {opencode_list()}")
    print(f"Continue  ({CONTINUE_CONFIG.name}): {continue_list()}")
    return 0


def cmd_select(model: str, small_model: str | None) -> int:
    """Set OpenCode `model` + `small_model` and promote `model` to first in
    Continue's models list. Both must already be configured."""
    # When `small_model` is omitted, use the main `model` for both. This is
    # the common case for a single-model local setup.
    small_model = small_model or model

    # Hard error on missing config files — `cmd_select` is a precise op,
    # not a best-effort cleanup, so we want a clear failure if either file
    # is absent.
    if not OPENCODE_CONFIG.exists():
        sys.exit(f"ai-model: {OPENCODE_CONFIG} missing")
    if not CONTINUE_CONFIG.exists():
        sys.exit(f"ai-model: {CONTINUE_CONFIG} missing")

    # Precondition: both names must be configured in OpenCode. We check
    # before mutating either config so a typo in arg 2 doesn't leave the
    # main model partially set.
    oc_models = opencode_list()
    missing_oc = [m for m in (model, small_model) if m not in oc_models]
    if missing_oc:
        sys.exit(
            "ai-model: missing from OpenCode config: "
            + ", ".join(missing_oc)
            + ".  Run `ai-model add <name>` for each first."
        )

    # Continue only cares about the main `model` (it doesn't have the
    # small-model concept). Verify and bail early if missing.
    cn_models = continue_list()
    if model not in cn_models:
        sys.exit(
            f"ai-model: {model} missing from Continue config.  "
            f"Run `ai-model add {model}` first."
        )

    # All preconditions met — do the two writes.
    opencode_set_defaults(model, small_model)
    continue_set_default(model)
    return 0


def main() -> int:
    # argparse setup. RawDescriptionHelpFormatter preserves the multi-line
    # usage block in the module docstring (default formatter would re-wrap
    # it and lose the columnar alignment).
    p = argparse.ArgumentParser(
        prog="ai-model",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("verb", choices=["add", "remove", "list", "select"])
    # Both positional args are optional at the parser level; the per-verb
    # logic below enforces what each verb actually needs. This is simpler
    # than configuring subparsers when there are only four verbs and the
    # validation rules differ slightly per verb.
    p.add_argument("model", nargs="?", help="model name, e.g. qwen3-coder:30b")
    p.add_argument(
        "small_model", nargs="?",
        help="(select only) optional small_model; defaults to <model>",
    )
    args = p.parse_args()

    # `list` is the only verb that ignores all positional args.
    if args.verb == "list":
        return cmd_list()
    # Everything else needs a model name. `p.error()` prints usage + exits 2.
    if not args.model:
        p.error(f"{args.verb} requires a model name")
    # Dispatch table — short enough to keep as explicit branches.
    if args.verb == "add":
        return cmd_add(args.model)
    if args.verb == "remove":
        # `remove` shouldn't accept a second positional; catch a likely
        # typo (e.g., `ai-model remove a b` when the user meant `select`).
        if args.small_model:
            p.error("remove takes only one model name")
        return cmd_remove(args.model)
    # Final fall-through: `select` (small_model may be None — cmd_select
    # handles defaulting).
    return cmd_select(args.model, args.small_model)


if __name__ == "__main__":
    sys.exit(main())
