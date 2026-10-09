"""Configuration.

Backend selection decides whether a command needs credentials at all, so
getting it wrong is the difference between "runs offline" and "fails on a
machine with no key". The platform-dependent chat default is the one piece of
configuration in this project that is not the same everywhere, which makes it
worth pinning.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mashup import config as config_module
from mashup.config import Config, ConfigError, load_config

MODEL_ENV = (
    "MASHUP_AI_API_KEY",
    "MASHUP_AI_BASE_URL",
    "MASHUP_CHAT_BACKEND",
    "MASHUP_EMBED_BACKEND",
    "MASHUP_LOCAL_CHAT_MODEL",
    "MASHUP_LOCAL_EMBED_MODEL",
    "MASHUP_CHAT_MODEL",
    "MASHUP_EMBED_MODEL",
    "MASHUP_WORKDIR",
)


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    for name in MODEL_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MASHUP_WORKDIR", str(tmp_path / "work"))
    return monkeypatch


# ---- backend defaults ----------------------------------------------------


def test_embeddings_default_to_local_everywhere(clean_env) -> None:
    assert load_config().embed_backend == "local"


def test_chat_defaults_to_local_on_apple_silicon(clean_env) -> None:
    clean_env.setattr(config_module.sys, "platform", "darwin")
    clean_env.setattr(config_module.platform, "machine", lambda: "arm64")
    assert load_config().chat_backend == "local"


@pytest.mark.parametrize(
    ("platform_name", "machine"),
    [("linux", "x86_64"), ("darwin", "x86_64"), ("win32", "AMD64")],
)
def test_chat_falls_back_to_the_gateway_where_mlx_cannot_run(
    clean_env, platform_name: str, machine: str
) -> None:
    """mlx is Apple-silicon only. Defaulting to local everywhere would make
    the first command on any other machine die on an import error."""
    clean_env.setattr(config_module.sys, "platform", platform_name)
    clean_env.setattr(config_module.platform, "machine", lambda: machine)
    assert load_config().chat_backend == "gateway"


def test_an_explicit_backend_beats_the_platform_default(clean_env) -> None:
    clean_env.setattr(config_module.sys, "platform", "linux")
    clean_env.setenv("MASHUP_CHAT_BACKEND", "local")
    assert load_config().chat_backend == "local"


# ---- what actually needs a key -------------------------------------------


def base(**over) -> Config:
    args = {
        "gateway_url": "http://gateway.invalid",
        "gateway_api_key": "",
        "project_id": "test",
        "chat_model": "auto",
        "embed_model": "stub",
        "workdir": Path("/tmp/mashup-test"),
    }
    return Config(**{**args, **over})


@pytest.mark.parametrize(
    ("chat", "embed", "expected"),
    [
        ("local", "local", False),
        ("gateway", "local", True),
        ("local", "gateway", True),
        ("gateway", "gateway", True),
    ],
)
def test_needs_gateway_tracks_the_backends(chat: str, embed: str, expected: bool) -> None:
    assert base(chat_backend=chat, embed_backend=embed).needs_gateway is expected


def test_a_fully_local_config_needs_no_key(clean_env) -> None:
    clean_env.setattr(config_module.sys, "platform", "darwin")
    clean_env.setattr(config_module.platform, "machine", lambda: "arm64")
    cfg = load_config()
    assert not cfg.gateway_api_key
    assert not cfg.needs_gateway


# ---- the key requirement -------------------------------------------------


def test_loading_does_not_demand_a_key_by_default(clean_env) -> None:
    """Most commands no longer touch the gateway, so the default must not
    refuse to build a config without credentials."""
    assert load_config().gateway_api_key == ""


def test_require_key_still_works_for_callers_that_need_it(clean_env) -> None:
    with pytest.raises(ConfigError, match="No direct-provider key"):
        load_config(require_key=True)


def test_the_direct_provider_env_var_is_accepted(clean_env) -> None:
    clean_env.setenv("MASHUP_AI_API_KEY", "direct-key")
    assert load_config(require_key=True).gateway_api_key == "direct-key"


# ---- model selection -----------------------------------------------------


def test_local_model_overrides_are_read(clean_env) -> None:
    clean_env.setenv("MASHUP_LOCAL_CHAT_MODEL", "mlx-community/something-else")
    clean_env.setenv("MASHUP_LOCAL_EMBED_MODEL", "minilm")
    cfg = load_config()
    assert cfg.local_chat_model == "mlx-community/something-else"
    assert cfg.local_embed_model == "minilm"


def test_workdir_paths_hang_off_the_workdir(tmp_path) -> None:
    cfg = base(workdir=tmp_path)
    assert cfg.db_path == tmp_path / "mashup.db"
    assert cfg.cache_dir == tmp_path / "cache"
