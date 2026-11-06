#!/usr/bin/env python3
"""Install a verified local Mashup bundle without changing the MCP host."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

BUNDLE_SCHEMA = "fleet.mashup-extension-bundle.v1"
RECEIPT_SCHEMA = "fleet.mashup-extension-install.v1"
REQUIRED_FILES = {
    "extension.json",
    "requirements.lock",
    "install.py",
    "smoke.py",
    "README.md",
    "mashup-media/SKILL.md",
    "mashup-media/character-overlay.json",
    "pace/mashup-media.skill.md",
}


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def identity(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@dataclass(frozen=True)
class BundleManifest:
    root: Path
    version: str
    wheel: str
    bundle_id: str
    files: dict[str, dict]

    @classmethod
    def load(cls, root: Path) -> BundleManifest:
        root = root.resolve(strict=True)
        manifest = root / "bundle.json"
        if manifest.is_symlink() or not manifest.is_file() or manifest.stat().st_size > 128_000:
            raise ValueError("Expected a regular bundle.json below the extracted bundle directory")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if (
            not isinstance(data, dict)
            or data.get("schema") != BUNDLE_SCHEMA
            or data.get("id") != "mashup-media"
        ):
            raise ValueError("Unsupported extension bundle")
        version = data.get("version", "")
        wheel = data.get("wheel", "")
        if (
            not isinstance(version, str)
            or not isinstance(wheel, str)
            or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version)
            or wheel != ("mashup-" + version + "-py3-none-any.whl")
        ):
            raise ValueError("Bundle wheel does not match its version")
        revision = data.get("sourceRevision", "")
        if not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{40}", revision):
            raise ValueError("Bundle must name an exact source revision")
        files = data.get("files", {})
        if not isinstance(files, dict) or set(files) != REQUIRED_FILES | {wheel}:
            raise ValueError("Bundle file inventory is incomplete or unsupported")
        for name, expected in files.items():
            path = root / name
            if any(part.is_symlink() for part in [path, *path.parents] if part != root.parent):
                raise ValueError("Bundle files must not be symlinks")
            if not path.is_file() or not isinstance(expected, dict):
                raise ValueError("Bundle file is missing: " + name)
            if path.stat().st_size != expected.get("bytes") or digest(path) != expected.get(
                "sha256"
            ):
                raise ValueError("Bundle integrity check failed: " + name)
        return cls(root, version, wheel, identity(data), files)


@dataclass(frozen=True)
class InstallReceipt:
    schema: str
    bundleId: str
    version: str
    interpreter: str
    runtimeFiles: dict[str, str]
    verification: dict

    @classmethod
    def load(cls, path: Path, bundle: BundleManifest, python: Path) -> InstallReceipt:
        try:
            receipt = cls(**json.loads(path.read_text(encoding="utf-8")))
        except (OSError, TypeError, ValueError) as exc:
            raise ValueError(
                "Incomplete or conflicting install; preserve it and choose a new destination"
            ) from exc
        if (
            receipt.schema != RECEIPT_SCHEMA
            or receipt.bundleId != bundle.bundle_id
            or receipt.version != bundle.version
            or receipt.interpreter != str(python)
            or not python.is_file()
            or receipt.runtimeFiles != runtime_files(python.parent.parent.parent)
        ):
            raise ValueError("Installed runtime changed; preserve it and choose a new destination")
        return receipt


def runtime_files(runtime: Path) -> dict[str, str]:
    return {
        str(path.relative_to(runtime)): "symlink:" + os.readlink(path)
        if path.is_symlink()
        else digest(path)
        for base in (runtime / "venv", runtime / "resources")
        for path in sorted(base.rglob("*"))
        if (path.is_file() or path.is_symlink())
        and "__pycache__" not in path.parts
        and path.suffix != ".pyc"
    }


def absolute_directory(value: str, *, exists: bool) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Use absolute directory paths without traversal")
    path = path.resolve(strict=exists)
    if (exists or path.exists()) and not path.is_dir():
        raise ValueError("Expected a directory: " + str(path))
    return path


def atomic_json(path: Path, value: dict) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    with os.fdopen(fd, "w", encoding="utf-8") as target:
        json.dump(value, target, indent=2)
        target.write("\n")
    os.replace(temporary, path)


def command(arguments: list, environment: dict, *, capture=False, timeout=180) -> str:
    result = subprocess.run(
        [str(item) for item in arguments],
        env=environment,
        cwd=tempfile.gettempdir(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE if capture else sys.stderr,
        stderr=sys.stderr,
        text=True,
        timeout=timeout,
        check=True,
    )
    return result.stdout if capture else ""


def install(args: argparse.Namespace) -> dict:
    bundle = BundleManifest.load(Path(__file__).parent)
    if sys.platform not in {"darwin", "linux"}:
        raise ValueError("This local extension supports macOS and Linux")
    destination = absolute_directory(args.destination, exists=False)
    state = absolute_directory(args.state_dir, exists=False)
    inputs = [absolute_directory(value, exists=True) for value in args.input_root]
    skills = [absolute_directory(value, exists=True) for value in args.skills_root]
    runtime = destination / "runtimes" / (bundle.version + "-" + bundle.bundle_id[:20])
    if state == runtime or runtime in state.parents:
        raise ValueError("Operator state must be outside the immutable runtime")
    binaries = {name: shutil.which(name) for name in ("uv", "ffmpeg", "ffprobe")}
    missing = [name for name, path in binaries.items() if path is None]
    if missing:
        raise ValueError("Install these prerequisites first: " + ", ".join(missing))
    runtime_path = os.pathsep.join(
        dict.fromkeys(
            [str(Path(binaries[name]).parent) for name in ("ffmpeg", "ffprobe")]
            + os.defpath.split(os.pathsep)
        )
    )
    environment = {
        "PATH": runtime_path,
        "LANG": "en_US.UTF-8",
        "PYTHONNOUSERSITE": "1",
        "UV_NO_CONFIG": "1",
    }
    for key in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
    ):
        if key in os.environ:
            environment[key] = os.environ[key]
    python = runtime / "venv" / "bin" / "python"
    server_args = ["-m", "mashup.extension.mcp", "--state-dir", str(state)]
    for flag, roots in (
        ("--input-root", inputs),
        ("--skills-root", [runtime / "resources", *skills]),
    ):
        for root in roots:
            server_args.extend([flag, str(root)])
    config = {
        "mcpServers": {
            "mashup-media": {
                "command": str(python),
                "args": server_args,
                "env": {"PATH": runtime_path, "PYTHONNOUSERSITE": "1"},
            }
        }
    }
    proposal = destination / "proposals" / bundle.bundle_id[:20] / identity(config)[:20]
    report = {
        "schema": "fleet.mashup-extension-install-result.v1",
        "bundleId": bundle.bundle_id,
        "runtime": str(runtime),
        "configuration": str(proposal / "pace-mcp.json"),
        "paceSkill": str(runtime / "resources" / "pace" / "mashup-media.skill.md"),
        "verificationScript": str(runtime / "resources" / "smoke.py"),
        "activation": "not-performed",
    }
    if args.dry_run:
        reused = runtime.exists()
        if reused:
            try:
                lock = (destination / ".install.lock").open("rb")
            except FileNotFoundError:
                lock = None
            if lock is None:
                InstallReceipt.load(runtime / "install-receipt.json", bundle, python)
            else:
                with lock:
                    try:
                        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
                    except BlockingIOError as exc:
                        raise ValueError(
                            "Another installation is active at this destination"
                        ) from exc
                    InstallReceipt.load(runtime / "install-receipt.json", bundle, python)
        return {**report, "action": "verify-and-reuse" if reused else "install"}
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (destination / ".install.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Another installation is active at this destination") from exc
        receipt_path = runtime / "install-receipt.json"
        reused = runtime.exists()
        if reused:
            receipt = InstallReceipt.load(receipt_path, bundle, python)
        else:
            runtime.mkdir(mode=0o700, parents=True)
            print("Installing isolated pinned runtime", file=sys.stderr)
            command([binaries["uv"], "venv", "--python", "3.11", runtime / "venv"], environment)
            command(
                [
                    binaries["uv"],
                    "pip",
                    "sync",
                    "--python",
                    python,
                    "--require-hashes",
                    "--only-binary",
                    ":all:",
                    bundle.root / "requirements.lock",
                ],
                environment,
            )
            resources = runtime / "resources"
            resources.mkdir()
            for name in (
                "mashup-media/SKILL.md",
                "mashup-media/character-overlay.json",
                "pace/mashup-media.skill.md",
                "smoke.py",
            ):
                target = resources / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(bundle.root / name, target)
            command(
                [
                    binaries["uv"],
                    "pip",
                    "install",
                    "--python",
                    python,
                    "--no-deps",
                    bundle.root / bundle.wheel,
                ],
                environment,
            )
        verification = Path(tempfile.mkdtemp(prefix="verify-", dir=runtime))
        print("Verifying installed MCP in isolated scratch state", file=sys.stderr)
        smoke = json.loads(
            command(
                [python, runtime / "resources" / "smoke.py", "--state-dir", verification / "state"],
                environment,
                capture=True,
                timeout=45,
            )
        )
        if reused:
            InstallReceipt.load(receipt_path, bundle, python)
        else:
            receipt = InstallReceipt(
                RECEIPT_SCHEMA,
                bundle.bundle_id,
                bundle.version,
                str(python),
                runtime_files(runtime),
                smoke,
            )
            atomic_json(receipt_path, asdict(receipt))
        proposal.mkdir(mode=0o700, parents=True, exist_ok=True)
        proposal_path = proposal / "pace-mcp.json"
        if proposal_path.exists() and json.loads(proposal_path.read_text()) != config:
            raise ValueError("Configuration proposal conflicts with existing data; preserved it")
        if not proposal_path.exists():
            atomic_json(proposal_path, config)
        return {**report, "action": "reused" if reused else "installed", "verification": smoke}


def main() -> None:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--input-root", action="append", default=[])
    parser.add_argument("--skills-root", action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        result = install(args)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(1, "Installation failed: " + str(exc) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
