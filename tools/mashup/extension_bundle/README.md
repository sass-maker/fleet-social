# Mashup media extension

This bundle installs local video templates and read-only canonical skill discovery for Pace or another stdio MCP host. The runtime is independent of the source checkout and the extracted bundle after installation. No host configuration is activated automatically.

## Build

From the canonical `tools/mashup` source, use Python 3.11+ and uv:

```bash
python3 scripts/build_extension.py --output /absolute/bundles
```

The command creates an extracted bundle and a `.tar.gz` with its SHA-256. `bundle.json` names the source commit and hashes every payload. The wheel and dependency lock exclude local model extras. A normal build requires committed source; `--allow-dirty` is for development and records that qualification in the manifest. Integrity hashes detect changed bytes; they are not authenticity signatures.

## Install

Extract the bundle anywhere. The installer accepts system Python 3.9+ and requires `uv`, `ffmpeg`, and `ffprobe` on PATH. uv installs Python 3.11 when needed and downloads pinned, hash-verified dependency wheels from PyPI. This is not an offline distribution.

```bash
python3 /absolute/bundle/install.py --destination /absolute/extensions/mashup-media --state-dir /absolute/media-state --input-root /absolute/media --skills-root /absolute/canonical-skills
```

Repeat input and skills roots as needed. Configure the canonical target directories of skill symlinks, including system skills separately if needed. `--dry-run` verifies the payload and prerequisites and prints the proposed locations without installing. The installer never reads or writes the host's MCP configuration or the operator's state directory.

Each bundle gets its own immutable runtime. Installation verifies the real stdio protocol in new scratch state before writing the completion receipt. An identical install checks its runtime hashes and reuses it. A failed, incomplete, or altered runtime is preserved and refused; choose a new destination to retry, then inspect or recover the previous directory separately. An upgrade leaves prior runtimes and proposals in place.

## Connect Pace

The installer returns `configuration` and `paceSkill` paths. Merge the `mcpServers.mashup-media` entry from that proposed JSON into Pace's existing MCP configuration, preserving its other entries. The entry uses an absolute installed interpreter and explicit FFmpeg PATH, so no checkout, bundle path, shell activation, or working directory is needed. Keep the venv interpreter path rather than resolving its symlink to base Python.

The returned `.skill.md` is a small optional Pace procedure that discovers the installed canonical usage guide through MCP. Copy it into Pace's user skills directory only when activating the connection. Other computer and agent skills stay in the explicitly configured canonical directories; this bundle does not duplicate them or grant their execution permissions.

Pace currently has a fixed built-in MCP catalog. This bundle uses its existing generic MCP configuration interface; it does not add a one-click native catalog row. Host action approvals and execution permissions still apply.

## Use and verify

Read the installed [usage skill](mashup-media/SKILL.md) and [composition example](mashup-media/character-overlay.json). Supported execution is the finite `character-overlay@1` template. RAEA, Cinetic, narration, computer use, and agent workflows can be discovered as host guidance when their canonical roots are configured. Their engines, models, credentials, and external generation providers are not installed or qualified by this bundle.

For an additional real render check, run the returned `verificationScript` with the installed interpreter and a new scratch path. This script is copied into the installed runtime, so verification also works after moving or removing the extracted bundle. The fixture is an original procedural clip and transparent animation. It uses the same approval, detached-job, and receipt path as a real overlay.

```bash
/absolute/installed/venv/bin/python /absolute/installed/resources/smoke.py --state-dir /absolute/new-smoke-state --render
```

A successful installation only verifies MCP discovery. The optional render smoke verifies its own generated fixture; it does not prove Pace's native planner/action flow until the connection is activated and exercised there. Mashup never publishes media.
