"""A fresh static footer build must never replace the complete proof release."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location(
    "deploy_public_proof", SCRIPTS / "deploy_public_proof.py"
)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture_bundle(tmp_path):
    build, bundle = tmp_path / "build", tmp_path / "bundle"
    build.mkdir()
    bundle.mkdir()
    (build / "index.html").write_text("current footer")
    (bundle / "index.html").write_text("current footer")
    (bundle / "receipts").mkdir()
    for name in ("survive-technology", "operators"):
        (bundle / f"receipts/{name}.receipt.json").write_text(
            json.dumps(
                {
                    "output": {
                        "video": {"path": f"/media/{name}.mp4"},
                        "captions": {"path": f"/media/{name}.vtt"},
                    }
                }
            )
        )
    return build, bundle


def test_plain_build_cannot_be_published(tmp_path):
    build = tmp_path / "build"
    build.mkdir()
    (build / "index.html").write_text("current footer")
    with pytest.raises(FileNotFoundError):
        module.validate_static_bundle(build, build)


def test_old_staging_cannot_receive_current_source_tag(tmp_path):
    build, bundle = fixture_bundle(tmp_path)
    (bundle / "index.html").write_text("old footer")
    with pytest.raises(ValueError, match="stale"):
        module.validate_static_bundle(bundle, build)


@pytest.mark.parametrize("unexpected", ["editor/index.html", ".fleet-local/private.json"])
def test_operator_or_private_files_cannot_enter_release(tmp_path, unexpected):
    build, bundle = fixture_bundle(tmp_path)
    asset = bundle / unexpected
    asset.parent.mkdir(parents=True, exist_ok=True)
    asset.write_text("private")
    with pytest.raises(ValueError, match="operator route|unexpected file"):
        module.validate_static_bundle(bundle, build)


def test_current_public_static_bundle_is_accepted(tmp_path):
    build, bundle = fixture_bundle(tmp_path)
    module.validate_static_bundle(bundle, build)
