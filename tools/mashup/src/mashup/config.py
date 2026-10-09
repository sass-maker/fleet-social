"""Runtime configuration.

Remote chat uses an explicitly configured OpenAI-compatible free-provider
endpoint. Local models remain the default on Apple silicon.

Embeddings default to a local model instead. They are re-run constantly while
tuning retrieval, they are the one stage where the gateway's provider
fallback can silently corrupt results, and a transformer small enough to
embed a whole archive in seconds removes both problems for free.
"""

from __future__ import annotations

import os
import platform
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DEFAULT_GATEWAY_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
DEFAULT_EMBED_MODEL = "gemini-embedding-001"
DEFAULT_CHAT_MODEL = "gemini-2.5-flash"
DEFAULT_EMBED_BACKEND = "local"
DEFAULT_LOCAL_EMBED_MODEL = "bge-base"
DEFAULT_LOCAL_CHAT_MODEL = "mlx-community/Qwen3-4B-Instruct-2507-4bit"


def _default_chat_backend() -> str:
    """Local chat runs on mlx, which is Apple silicon only.

    Defaulting to `local` everywhere would make the first command on a Linux
    box fail with an import error instead of doing the obvious thing.
    """
    if sys.platform == "darwin" and platform.machine() == "arm64":
        return "local"
    return "gateway"


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    gateway_url: str
    gateway_api_key: str
    project_id: str
    chat_model: str
    embed_model: str
    workdir: Path
    # "local" runs the model in-process; "gateway" calls out.
    embed_backend: str = DEFAULT_EMBED_BACKEND
    local_embed_model: str = DEFAULT_LOCAL_EMBED_MODEL
    chat_backend: str = "gateway"
    local_chat_model: str = DEFAULT_LOCAL_CHAT_MODEL

    @property
    def needs_gateway(self) -> bool:
        """Whether any stage will actually call out."""
        return "gateway" in (self.embed_backend, self.chat_backend)

    @property
    def db_path(self) -> Path:
        return self.workdir / "mashup.db"

    @property
    def cache_dir(self) -> Path:
        return self.workdir / "cache"

    def ensure_dirs(self) -> None:
        for d in (self.workdir, self.cache_dir):
            d.mkdir(parents=True, exist_ok=True)


def load_config(workdir: Path | str | None = None, *, require_key: bool = False) -> Config:
    """Read the environment. `require_key` is for callers that know they need
    the gateway; most commands no longer do, so it defaults off."""
    key = os.getenv("MASHUP_AI_API_KEY") or ""
    backend = os.getenv("MASHUP_EMBED_BACKEND") or DEFAULT_EMBED_BACKEND
    chat_backend = os.getenv("MASHUP_CHAT_BACKEND") or _default_chat_backend()
    if require_key and not key:
        raise ConfigError("No direct-provider key. Set MASHUP_AI_API_KEY.")
    wd = Path(workdir or os.getenv("MASHUP_WORKDIR") or ".mashup").expanduser().resolve()
    return Config(
        gateway_url=(os.getenv("MASHUP_AI_BASE_URL") or DEFAULT_GATEWAY_URL).rstrip("/"),
        gateway_api_key=key,
        project_id=os.getenv("MASHUP_PROJECT_ID") or "mashup",
        chat_model=os.getenv("MASHUP_CHAT_MODEL") or DEFAULT_CHAT_MODEL,
        embed_model=os.getenv("MASHUP_EMBED_MODEL") or DEFAULT_EMBED_MODEL,
        workdir=wd,
        embed_backend=backend,
        local_embed_model=(os.getenv("MASHUP_LOCAL_EMBED_MODEL") or DEFAULT_LOCAL_EMBED_MODEL),
        chat_backend=chat_backend,
        local_chat_model=(os.getenv("MASHUP_LOCAL_CHAT_MODEL") or DEFAULT_LOCAL_CHAT_MODEL),
    )
