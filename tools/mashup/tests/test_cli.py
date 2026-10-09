from __future__ import annotations

import pytest
import typer


def test_render_gets_a_message_callback_not_a_counter() -> None:
    """`render()` reports status strings; the staged commands report counts.

    Passing the counter callback to render raised
    `TypeError: cb() missing 1 required positional argument: 'total'` deep
    inside the render, after every clip had already been encoded.
    """
    from mashup.cli import _progress, _status

    _status("x")("some message")  # one positional arg, as render calls it
    _progress("x")(1, 2)  # two, as enrich/embed call it

    with pytest.raises(TypeError):
        _progress("x")("some message")


def test_gateway_key_is_required_only_for_the_stage_being_run(monkeypatch, tmp_path) -> None:
    from mashup.cli import _runnable

    monkeypatch.delenv("MASHUP_GATEWAY_API_KEY", raising=False)
    monkeypatch.delenv("GATEWAY_API_KEY", raising=False)
    monkeypatch.setenv("MASHUP_CHAT_BACKEND", "gateway")
    monkeypatch.setenv("MASHUP_EMBED_BACKEND", "local")

    assert _runnable(tmp_path, embed=True).embed_backend == "local"
    with pytest.raises(typer.Exit):
        _runnable(tmp_path, chat=True)


def test_full_pipeline_checks_both_backends(monkeypatch, tmp_path) -> None:
    from mashup.cli import _runnable

    monkeypatch.delenv("MASHUP_GATEWAY_API_KEY", raising=False)
    monkeypatch.delenv("GATEWAY_API_KEY", raising=False)
    monkeypatch.setenv("MASHUP_CHAT_BACKEND", "local")
    monkeypatch.setenv("MASHUP_EMBED_BACKEND", "gateway")

    with pytest.raises(typer.Exit):
        _runnable(tmp_path, chat=True, embed=True)
