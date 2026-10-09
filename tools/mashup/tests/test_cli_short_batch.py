from __future__ import annotations

from typer.testing import CliRunner

from mashup import cli


def test_short_batch_preflights_burned_caption_support(monkeypatch) -> None:
    monkeypatch.setattr(cli, "has_subtitles_filter", lambda: False)
    monkeypatch.setattr(
        cli.pipeline,
        "make_short_batch",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("planning should not run")),
    )

    result = CliRunner().invoke(
        cli.app,
        ["short-batch", "--collection", "startups", "--angle", "fundraising"],
    )

    assert result.exit_code == 2
    assert "requires burned captions" in result.output
    assert "--no-render" in result.output


def test_short_batch_reports_an_unready_corpus_without_a_traceback(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_runnable", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        cli.pipeline,
        "make_short_batch",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ValueError("No embedded segments. Run `mashup embed` first.")
        ),
    )

    result = CliRunner().invoke(
        cli.app,
        [
            "short-batch",
            "--collection",
            "startups",
            "--angle",
            "fundraising",
            "--no-render",
        ],
    )

    assert result.exit_code == 2
    assert "No embedded segments" in result.output
    assert "Traceback" not in result.output
