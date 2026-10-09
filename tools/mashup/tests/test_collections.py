from __future__ import annotations

import pytest

from mashup.collections import get_collection, list_collections


def test_startups_collection_exposes_repeatable_angles():
    preset = get_collection("startups")

    assert preset.watermark == "STARTUPS"
    assert {"product-market-fit", "fundraising", "distribution"} <= set(preset.angles)
    assert list_collections() == [preset]


def test_collection_resolves_named_and_custom_prompts():
    preset = get_collection("startups")

    angle, prompt = preset.prompt_for("fundraising", None)
    assert angle == "fundraising"
    assert "fundraising" in prompt
    assert preset.prompt_for(None, "a sharp lesson about pricing") == (
        "custom",
        "a sharp lesson about pricing",
    )


def test_unknown_collection_angle_lists_valid_choices():
    with pytest.raises(ValueError, match="product-market-fit"):
        get_collection("startups").prompt_for("virality", None)
