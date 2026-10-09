"""Publish only a complete approved proof bundle matching a fresh site build."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from check_public_proof import PROOFS, validate_bundle

REPO = Path(__file__).resolve().parents[1]
EXCLUDED = {"editor", "visual-lab"}


def validate_static_bundle(bundle: Path, build: Path) -> None:
    bundle, build = bundle.resolve(), build.resolve()
    if not (build / "index.html").is_file():
        raise ValueError("missing fresh site build")
    allowed = set()
    for asset in build.rglob("*"):
        relative = asset.relative_to(build)
        if relative.parts[0] in EXCLUDED or not asset.is_file():
            continue
        if asset.is_symlink() or (bundle / relative).is_symlink():
            raise ValueError(f"symlink in static bundle: {relative}")
        if asset.read_bytes() != (bundle / relative).read_bytes():
            raise ValueError(f"stale or altered build asset: {relative}")
        allowed.add(relative.as_posix())
    for name in PROOFS:
        receipt_path = f"receipts/{name}.receipt.json"
        receipt = json.loads((bundle / receipt_path).read_text())
        allowed.add(receipt_path)
        for kind in ("video", "captions"):
            allowed.add(urlparse(receipt["output"][kind]["path"]).path.lstrip("/"))
    allowed.add("receipts/survive-technology.score.json")
    for asset in bundle.rglob("*"):
        relative = asset.relative_to(bundle)
        if asset.is_symlink():
            raise ValueError(f"symlink in public bundle: {relative}")
        if relative.parts[0] in EXCLUDED:
            raise ValueError(f"operator route in public bundle: {relative}")
        if asset.is_file() and relative.as_posix() not in allowed:
            raise ValueError(f"unexpected file in public bundle: {relative}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "bundle", type=Path, help="explicit complete staging directory, not web/dist"
    )
    args = parser.parse_args()
    bundle = args.bundle.resolve()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    subprocess.run(["git", "diff", "--quiet", "HEAD"], cwd=REPO, check=True)
    untracked = subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard"], cwd=REPO, text=True
    ).strip()
    if untracked:
        parser.exit(1, "Refusing public deployment: checkout has unpublished source files\n")
    main_ref = subprocess.check_output(
        ["git", "ls-remote", "origin", "refs/heads/main"], cwd=REPO, text=True
    ).split()
    if not main_ref or main_ref[0] != commit:
        parser.exit(1, "Refusing public deployment: checkout is not the published main commit\n")
    # Build and deployment use the same checkout; old staging cannot acquire a new commit tag.
    subprocess.run(["pnpm", "build"], cwd=REPO / "web", check=True)
    try:
        validate_static_bundle(bundle, REPO / "web/dist")
        assets = validate_bundle(bundle)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Refusing public deployment: {error}\n")
    print(json.dumps({"commit": commit, "bundle": str(bundle), "assets": assets}), flush=True)
    subprocess.run(
        [
            "pnpm",
            "dlx",
            "--package=wrangler@4.120.0",
            "wrangler",
            "pages",
            "deploy",
            str(bundle),
            "--project-name=mashup",
            "--branch=main",
            f"--commit-hash={commit}",
        ],
        cwd=REPO / "web",
        check=True,
    )


if __name__ == "__main__":
    main()
