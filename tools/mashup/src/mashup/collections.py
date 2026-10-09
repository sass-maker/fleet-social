"""Checked-in editorial collections for repeatable operator workflows."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CollectionPreset(BaseModel):
    id: str
    name: str
    description: str
    watermark: str
    source_policy: str
    angles: dict[str, str] = Field(default_factory=dict)

    def prompt_for(self, angle: str | None, prompt: str | None) -> tuple[str, str]:
        if prompt and prompt.strip():
            return (angle.strip() if angle and angle.strip() else "custom", prompt.strip())
        key = (angle or "").strip()
        if not key:
            available = ", ".join(self.angles)
            raise ValueError(f"choose an angle for {self.id}: {available}, or provide --prompt")
        try:
            return key, self.angles[key]
        except KeyError as exc:
            available = ", ".join(self.angles)
            raise ValueError(
                f"unknown {self.id} angle {key!r}; choose one of: {available}"
            ) from exc


STARTUPS = CollectionPreset(
    id="startups",
    name="Startups",
    description=(
        "Source-faithful lessons for founders and early operators, selected from "
        "creator-owned, licensed, or public-domain conversations."
    ),
    watermark="STARTUPS",
    source_policy=(
        "Only ingest media the operator owns, has licensed, or can document as public domain."
    ),
    angles={
        "product-market-fit": (
            "a self-contained startup lesson about finding, measuring, or losing "
            "product-market fit, with a concrete signal or decision"
        ),
        "fundraising": (
            "a self-contained startup lesson about fundraising, investor conversations, "
            "runway, valuation, or deciding not to raise"
        ),
        "distribution": (
            "a self-contained startup lesson about distribution, customer acquisition, "
            "growth loops, sales, or reaching an initial market"
        ),
        "hiring": (
            "a self-contained startup lesson about early hiring, cofounders, team design, "
            "management, or letting someone go"
        ),
        "founder-failure": (
            "a self-contained startup lesson drawn from a founder mistake, failed bet, "
            "near-death moment, or difficult correction"
        ),
        "moats": (
            "a self-contained startup lesson about durable advantage, defensibility, "
            "network effects, switching costs, or why an apparent moat was weak"
        ),
        "contrarian-lessons": (
            "a self-contained, well-supported startup lesson that challenges common "
            "founder advice without relying on missing context"
        ),
    },
)

COLLECTIONS: dict[str, CollectionPreset] = {STARTUPS.id: STARTUPS}


def get_collection(collection_id: str) -> CollectionPreset:
    try:
        return COLLECTIONS[collection_id]
    except KeyError as exc:
        available = ", ".join(COLLECTIONS)
        raise ValueError(
            f"unknown collection {collection_id!r}; choose one of: {available}"
        ) from exc


def list_collections() -> list[CollectionPreset]:
    return list(COLLECTIONS.values())
