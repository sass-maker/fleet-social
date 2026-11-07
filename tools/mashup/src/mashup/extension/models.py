from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)


class Rights(StrictModel):
    title: str = Field(min_length=1, max_length=160)
    creator: str = Field(min_length=1, max_length=160)
    sourceUrl: str = Field(min_length=1, max_length=500)
    license: Literal["creator-owned", "licensed", "public-domain"]
    licenseUrl: str = Field(min_length=1, max_length=500)
    rightsConfirmed: Literal[True]

    @field_validator("rightsConfirmed", mode="before")
    @classmethod
    def explicit_rights(cls, value):
        if value is not True:
            raise ValueError("Rights must be explicitly confirmed with true")
        return value


class Composition(StrictModel):
    template: Literal["character-overlay@1"] = "character-overlay@1"
    background: str = Field(pattern=r"^asset-[a-f0-9]{64}$")
    foreground: str = Field(pattern=r"^asset-[a-f0-9]{64}$")
    duration: float = Field(gt=0, le=60)
    preset: Literal["portrait", "square", "landscape"] = "portrait"
    fps: Literal[24, 30] = 24
    x: float = Field(default=0.5, ge=0, le=1)
    y: float = Field(default=0.75, ge=0, le=1)
    width: float = Field(default=0.35, ge=0.05, le=1)
    loop: bool = True
    motion: Literal["none", "bob", "orbit", "slide"] = "none"


class Approval(StrictModel):
    status: Literal["approved"]
    approvedBy: str = Field(min_length=1, max_length=160)
    planHash: str = Field(pattern=r"^[a-f0-9]{64}$")


class TemplateSpec(StrictModel):
    id: str
    title: str
    description: str
    inputSchema: dict


def canonical_hash(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def now() -> str:
    return datetime.now(UTC).isoformat()


PRESETS = {"portrait": (720, 1280), "square": (1080, 1080), "landscape": (1280, 720)}
TEMPLATES = {
    "character-overlay@1": TemplateSpec(
        id="character-overlay@1",
        title="Character over a background",
        description=(
            "Composite an operator-supplied transparent image or animation over a local image "
            "or video. x/y locate the foreground center on the canvas; width is a canvas fraction. "
            "Foreground audio is omitted; background audio is preserved when present. "
            "Loop repeats the foreground; the background repeats to fill the approved duration. "
            "Motion presets move the foreground around its chosen center. No character is bundled."
            " PNG/GIF and alpha MOV are supported foregrounds; tagged WebM alpha is not supported."
        ),
        inputSchema=Composition.model_json_schema(),
    )
}
