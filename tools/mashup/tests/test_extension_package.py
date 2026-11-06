from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "mashup_bundle_installer", PROJECT / "extension_bundle" / "install.py"
)
installer = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = installer
spec.loader.exec_module(installer)


def execute(*arguments, cwd, check=True, environment=None, timeout=120):
    return subprocess.run(
        [str(item) for item in arguments],
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
        env=environment,
        timeout=timeout,
    )


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    if not all(shutil.which(name) for name in ("uv", "ffmpeg", "ffprobe")):
        pytest.skip("uv, FFmpeg and FFprobe are required to verify the installed package")
    output = tmp_path_factory.mktemp("extension-build")
    result = execute(
        sys.executable,
        PROJECT / "scripts" / "build_extension.py",
        "--output",
        output,
        "--allow-dirty",
        cwd=output,
    )
    return json.loads(result.stdout)


def test_bundle_tampering_refuses_before_installing(bundle, tmp_path):
    copied = tmp_path / "bundle"
    shutil.copytree(bundle["bundle"], copied)
    (copied / "requirements.lock").write_text("changed dependency bytes")
    with pytest.raises(ValueError, match="integrity check failed"):
        installer.BundleManifest.load(copied)
    result = execute(
        sys.executable,
        copied / "install.py",
        "--destination",
        tmp_path / "install",
        "--state-dir",
        tmp_path / "state",
        cwd=tmp_path,
        check=False,
    )
    assert result.returncode != 0 and "integrity check failed" in result.stderr
    assert not (tmp_path / "install").exists()


def test_existing_archive_tampering_is_not_reported_as_a_success(bundle, tmp_path):
    rebuilt = execute(
        sys.executable,
        PROJECT / "scripts" / "build_extension.py",
        "--output",
        tmp_path,
        "--allow-dirty",
        cwd=tmp_path,
    )
    data = json.loads(rebuilt.stdout)
    Path(data["archive"]).write_bytes(b"changed archive bytes")
    result = execute(
        sys.executable,
        PROJECT / "scripts" / "build_extension.py",
        "--output",
        tmp_path,
        "--allow-dirty",
        cwd=tmp_path,
        check=False,
    )
    assert result.returncode != 0 and "Existing archive changed" in result.stderr
    assert Path(data["archive"]).read_bytes() == b"changed archive bytes"


def test_standalone_install_reuse_and_real_render_preserve_operator_state(bundle, tmp_path):
    copied = tmp_path / "relocated-bundle"
    shutil.copytree(bundle["bundle"], copied)
    destination = tmp_path / "extensions"
    operator = tmp_path / "operator-state"
    operator.mkdir()
    sentinel = operator / "existing-operator-data.txt"
    sentinel.write_text("preserve existing state and never launch its jobs during installation")
    media = tmp_path / "media"
    media.mkdir()
    guides = tmp_path / "guides"
    (guides / "example").mkdir(parents=True)
    (guides / "example" / "SKILL.md").write_text("---\nname: example\ndescription: Fixture\n---\n")
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    environment = {
        "PATH": os.environ.get("PATH", os.defpath),
        "PYTHONPATH": "/nonexistent/project-that-must-not-be-used",
    }
    command = [
        sys.executable,
        copied / "install.py",
        "--destination",
        destination,
        "--state-dir",
        operator,
        "--input-root",
        media,
        "--skills-root",
        guides,
    ]
    plan = json.loads(execute(*command, "--dry-run", cwd=unrelated, environment=environment).stdout)
    assert plan["action"] == "install" and not destination.exists()
    installed = json.loads(execute(*command, cwd=unrelated, environment=environment).stdout)
    assert installed["action"] == "installed"
    assert installed["activation"] == "not-performed"
    assert installed["verification"]["discoveryVerified"] is True
    assert installed["verification"]["renderVerified"] is False
    assert list(operator.iterdir()) == [sentinel]
    configuration = json.loads(Path(installed["configuration"]).read_text())["mcpServers"][
        "mashup-media"
    ]
    python = Path(configuration["command"])
    assert python.is_absolute() and python.parent.parent.name == "venv"
    assert str(copied) not in json.dumps(configuration)
    assert str(PROJECT) not in json.dumps(configuration)
    assert str(Path(installed["runtime"]) / "resources") in configuration["args"]
    assert Path(installed["paceSkill"]).is_file()
    reused = json.loads(execute(*command, cwd=unrelated, environment=environment).stdout)
    assert reused["action"] == "reused"
    assert reused["configuration"] == installed["configuration"]
    assert list(operator.iterdir()) == [sentinel]
    relocated = copied.with_name("bundle-moved-after-install")
    copied.rename(relocated)
    smoke = execute(
        python,
        installed["verificationScript"],
        "--state-dir",
        tmp_path / "render-check",
        "--render",
        cwd=unrelated,
        environment={"PATH": configuration["env"]["PATH"]},
    )
    result = json.loads(smoke.stdout)
    assert result["renderVerified"] is True
    assert Path(result["artifact"]["receipt"]["path"]).is_file()
    assert list(operator.iterdir()) == [sentinel]
    relocated.rename(copied)
    installed_guide = Path(installed["runtime"]) / "resources" / "mashup-media" / "SKILL.md"
    installed_guide.write_text("altered installed resource")
    refused = execute(*command, cwd=unrelated, environment=environment, check=False)
    assert refused.returncode != 0 and "runtime changed" in refused.stderr
    assert installed_guide.read_text() == "altered installed resource"
    assert list(operator.iterdir()) == [sentinel]


def test_incomplete_runtime_is_preserved_and_refused(bundle, tmp_path):
    destination = tmp_path / "install"
    command = [
        sys.executable,
        Path(bundle["bundle"]) / "install.py",
        "--destination",
        destination,
        "--state-dir",
        tmp_path / "state",
    ]
    plan = json.loads(execute(*command, "--dry-run", cwd=tmp_path).stdout)
    runtime = Path(plan["runtime"])
    runtime.mkdir(parents=True)
    evidence = runtime / "incomplete-setup.txt"
    evidence.write_text("keep this recovery data")
    result = execute(*command, cwd=tmp_path, check=False)
    assert result.returncode != 0 and "Incomplete or conflicting install" in result.stderr
    assert evidence.read_text() == "keep this recovery data"
    assert not Path(plan["configuration"]).exists()
