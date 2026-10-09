"""mashup — assemble themed mashups from a creator's own archive."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from mashup import pipeline
from mashup.agent import failure as agent_failure
from mashup.agent import read_agent_request, run_agent
from mashup.batch import build_batch, save_batch, save_review_html
from mashup.collections import get_collection, list_collections
from mashup.config import ConfigError, load_config
from mashup.feed import acquire as feed_acquire
from mashup.media_receipt import build_media_receipt, save_media_receipt
from mashup.models import EDL
from mashup.ordertest import DEFAULT_SHUFFLES
from mashup.pipeline import DEFAULT_POOL
from mashup.plan.prompt import parse_duration
from mashup.podcast_contract import export_podcast_edit, save_podcast_edit
from mashup.render import edl_to_transcript, load_edl, render, save_edl
from mashup.render.cut import has_subtitles_filter
from mashup.shorts import attach_visual_manifest, validate_short_duration

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help=__doc__,
    invoke_without_command=True,
)
console = Console()
err = Console(stderr=True)

WORKDIR_OPT = Annotated[
    Path | None, typer.Option("--workdir", help="State directory (default .mashup)")
]


def _progress(label: str):
    """Counter-style progress, for the staged pipeline commands."""

    def cb(done: int, total: int) -> None:
        err.print(f"  {label}: {done}/{total}", end="\r")

    return cb


def _status(label: str):
    """Message-style progress. `render()` reports status strings, not counts —
    passing the counter callback to it raises a TypeError deep in the render."""

    def cb(message: str) -> None:
        err.print(f"  {label}: {message}")

    return cb


def _config(workdir: Path | None, *, require_key: bool = True):
    try:
        return load_config(workdir, require_key=require_key)
    except ConfigError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc


def _runnable(
    workdir: Path | None,
    *,
    chat: bool = False,
    embed: bool = False,
):
    """Load config, requiring a key only from backends that will actually
    call out. With both backends local, every command runs uncredentialled."""
    cfg = _config(workdir, require_key=False)
    gateway_stages = [
        name
        for name, enabled, backend in (
            ("chat", chat, cfg.chat_backend),
            ("embed", embed, cfg.embed_backend),
        )
        if enabled and backend == "gateway"
    ]
    if gateway_stages and not cfg.gateway_api_key:
        which = [f"MASHUP_{n.upper()}_BACKEND=gateway" for n in gateway_stages]
        err.print(
            f"[red]{' and '.join(which)} needs MASHUP_AI_API_KEY.[/red]\n"
            "Unset it to run that stage locally instead."
        )
        raise typer.Exit(2)
    return cfg


def _notice(message: str) -> None:
    err.print(f"[yellow]{message}[/yellow]")


def _show_counts(counts: dict[str, int]) -> None:
    table = Table(show_header=False, box=None)
    for key, value in counts.items():
        table.add_row(key, str(value))
    console.print(table)


def _summarise(edl: EDL) -> None:
    console.print(
        f"[bold]{edl.strategy}[/bold]  "
        f"{len(edl.clips)} clips  {edl.duration:.0f}s  score {edl.score:.3f}"
    )
    terms = edl.terms.model_dump()
    console.print("  " + "  ".join(f"{k}={v:.2f}" for k, v in terms.items()))
    for line in edl.rationale:
        console.print(f"  [dim]{line}[/dim]")


# ---- stages -------------------------------------------------------------


@app.command(name="agent")
def agent_cmd(
    request: Annotated[
        Path | None, typer.Option("--request", help="JSON request file; defaults to stdin")
    ] = None,
) -> None:
    """Execute one strict machine-readable agent operation."""
    raw = None
    try:
        raw = read_agent_request(request)
        result = run_agent(raw)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    except Exception as exc:  # protocol boundary normalizes every failure
        print(json.dumps(agent_failure(raw, exc), sort_keys=True, separators=(",", ":")))
        raise typer.Exit(1) from exc


@app.command(name="feed")
def feed_cmd(
    url: Annotated[str, typer.Option("--url", "-u", help="Podcast RSS feed URL")],
    limit: Annotated[int, typer.Option("--limit", "-n", help="Episodes to show")] = 20,
    pages: Annotated[
        int, typer.Option("--pages", help="Max paginated feed documents to follow")
    ] = feed_acquire.DEFAULT_MAX_PAGES,
) -> None:
    """List the episodes in a podcast RSS feed.

    Nothing is downloaded. Pick a row and pass its GUID to `mashup
    fetch-episode`; the number is only stable while the feed is.
    """
    feed = _resolve_feed(url, pages)
    heading = f"[bold]{feed.title}[/bold]  {len(feed.episodes)} episodes"
    if feed.pages > 1:
        heading += f" across {feed.pages} pages"
    console.print(heading)

    try:
        console.print(f"  [dim]licence {feed_acquire.check_rights(feed)}[/dim]")
    except feed_acquire.RightsError as exc:
        _notice(f"{exc}")

    table = Table(box=None)
    table.add_column("#", justify="right")
    table.add_column("published")
    table.add_column("length", justify="right")
    table.add_column("title")
    table.add_column("guid")
    for number, episode in enumerate(feed.episodes[:limit], start=1):
        table.add_row(
            str(number),
            (episode.published or "")[:10] or "—",
            _clock(episode.duration),
            episode.title[:60],
            episode.guid[:40] + ("…" if len(episode.guid) > 40 else ""),
        )
    console.print(table)
    if len(feed.episodes) > limit:
        console.print(f"  [dim]…{len(feed.episodes) - limit} more; raise --limit[/dim]")
    _report_feed_problems(feed)


@app.command(name="fetch-episode")
def fetch_episode_cmd(
    url: Annotated[str, typer.Option("--url", "-u", help="Podcast RSS feed URL")],
    guid: Annotated[
        str | None, typer.Option("--guid", help="Episode GUID, as shown by `mashup feed`")
    ] = None,
    index: Annotated[
        int | None, typer.Option("--index", help="Episode number from `mashup feed` instead")
    ] = None,
    workdir: WORKDIR_OPT = None,
    pages: Annotated[
        int, typer.Option("--pages", help="Max paginated feed documents to follow")
    ] = feed_acquire.DEFAULT_MAX_PAGES,
    i_have_rights: Annotated[
        bool,
        typer.Option(
            "--i-have-rights",
            help="Skip the licence gate; for a feed you own or have cleared",
        ),
    ] = False,
) -> None:
    """Cache one episode's audio in the workdir, with an acquisition record.

    Re-running is free: a cached file whose hash still matches its record is
    not downloaded again. The audio lands under the workdir cache so
    `mashup ingest --input <that directory>` transcribes it through the
    existing path.
    """
    if (guid is None) == (index is None):
        err.print("[red]pass exactly one of --guid or --index[/red]")
        raise typer.Exit(2)

    cfg = _config(workdir, require_key=False)
    cfg.ensure_dirs()
    feed = _resolve_feed(url, pages)
    _report_feed_problems(feed)

    if guid is not None:
        episode = feed.find(guid)
        if episode is None:
            err.print(f"[red]no episode with guid {guid!r} in {feed.url}[/red]")
            raise typer.Exit(2)
    else:
        if not 1 <= index <= len(feed.episodes):
            err.print(f"[red]--index must be between 1 and {len(feed.episodes)}[/red]")
            raise typer.Exit(2)
        episode = feed.episodes[index - 1]

    try:
        license_url = feed_acquire.check_rights(feed, override=i_have_rights)
    except feed_acquire.RightsError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc

    if episode.guid_is_borrowed:
        _notice(
            f"{episode.title!r} publishes no <guid>; the cache key borrows its "
            f"{episode.guid_source} and will change if the publisher moves the file."
        )

    try:
        got = feed_acquire.acquire_episode(
            feed,
            episode,
            cache_dir=cfg.cache_dir,
            fetcher=_fetcher(),
            license_url=license_url,
            rights_override=i_have_rights,
        )
    except feed_acquire.AcquireError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    state = "cached" if got.cached else "downloaded"
    console.print(f"[bold]{episode.title}[/bold]  [dim]{state}[/dim]")
    # Printed as lines rather than a table: a cache path is long, and rich
    # truncates a table cell that will not fit.
    console.print(f"  {got.audio_path}")
    console.print(f"  [dim]{got.size_bytes} bytes  sha256 {got.sha256}[/dim]")
    console.print(f"[green]{got.record_path}[/green]")
    if got.audio_path.suffix not in feed_acquire.INGESTABLE_SUFFIXES:
        _notice(
            f"{got.audio_path.suffix} is not one of the extensions `mashup ingest` "
            "reads; convert it before ingesting."
        )
    else:
        console.print(f"  [dim]next: mashup ingest --input {got.audio_path.parent}[/dim]")


def _fetcher():
    """Split out so tests can substitute a fetcher without a live feed."""
    return feed_acquire.HttpFetcher()


def _resolve_feed(url: str, pages: int):
    """Fetch and parse a feed, mapping its failures onto the CLI exit codes."""
    import httpx

    from mashup.feed.parse import FeedError

    if pages < 1:
        err.print("[red]--pages must be at least 1[/red]")
        raise typer.Exit(2)
    try:
        fetcher = _fetcher()
        try:
            return feed_acquire.resolve_feed(url, fetcher=fetcher, max_pages=pages)
        finally:
            close = getattr(fetcher, "close", None)
            if close is not None:
                close()
    except FeedError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    except (httpx.HTTPError, feed_acquire.AcquireError, OSError) as exc:
        err.print(f"[red]{url}: {exc}[/red]")
        raise typer.Exit(1) from exc


def _report_feed_problems(feed) -> None:
    """Dropped entries are shown, never swallowed — a short list is a claim."""
    if feed.parse_warning:
        _notice(f"feed is not well-formed XML ({feed.parse_warning}); parsed what was readable")
    for drop in feed.dropped:
        _notice(f"skipped entry {drop.index + 1} ({drop.title[:50]}): {drop.reason}")


def _clock(seconds: float | None) -> str:
    if not seconds:
        return "—"
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


@app.command()
def ingest(
    input_dir: Annotated[Path, typer.Option("--input", "-i", help="Archive directory")],
    workdir: WORKDIR_OPT = None,
    transcribe: Annotated[
        bool, typer.Option("--transcribe/--no-transcribe", help="Generate missing subtitles")
    ] = True,
) -> None:
    """Ingest media + subtitles and split into segments."""
    cfg = _config(workdir, require_key=False)
    counts = pipeline.ingest(input_dir, cfg, allow_transcribe=transcribe, progress=_notice)
    _show_counts(counts)


@app.command()
def enrich(
    workdir: WORKDIR_OPT = None,
    concurrency: Annotated[int, typer.Option("--concurrency", "-c")] = 4,
) -> None:
    """Extract topic/role/energy/context metadata for each segment.

    Runs a local mlx model on Apple silicon by default; set
    MASHUP_CHAT_BACKEND=gateway to use the fleet gateway instead.
    """
    cfg = _runnable(workdir, chat=True)
    counts = pipeline.enrich(cfg, concurrency=concurrency, progress=_progress("enrich"))
    _show_counts(counts)


@app.command()
def embed(
    workdir: WORKDIR_OPT = None,
    reset: Annotated[
        bool,
        typer.Option("--reset", help="Drop existing vectors first (use after a model change)"),
    ] = False,
) -> None:
    """Embed segments for retrieval.

    Runs a local HuggingFace encoder by default. Set MASHUP_LOCAL_EMBED_MODEL
    to pick another (`mashup models` lists them), or MASHUP_EMBED_BACKEND=gateway
    to embed through the fleet gateway instead.
    """
    cfg = _runnable(workdir, embed=True)
    counts = pipeline.embed(cfg, progress=_progress("embed"), reset=reset, notice=_notice)
    _show_counts(counts)


@app.command()
def models(workdir: WORKDIR_OPT = None) -> None:
    """Show which models this run would use, and the local aliases available."""
    from mashup.embedding import DEFAULT_LOCAL_MODEL, LOCAL_MODELS

    cfg = _config(workdir, require_key=False)
    active = Table(box=None)
    active.add_column("stage")
    active.add_column("backend")
    active.add_column("model")
    active.add_row(
        "chat",
        cfg.chat_backend,
        cfg.local_chat_model if _local(cfg.chat_backend) else cfg.chat_model,
    )
    active.add_row(
        "embed",
        cfg.embed_backend,
        cfg.local_embed_model if _local(cfg.embed_backend) else cfg.embed_model,
    )
    console.print(active)
    console.print(f"\n[dim]gateway needed: {'yes' if cfg.needs_gateway else 'no'}[/dim]\n")

    table = Table(box=None)
    table.add_column("embed alias")
    table.add_column("repo")
    table.add_column("dim", justify="right")
    table.add_column("pooling")
    for alias, spec in LOCAL_MODELS.items():
        mark = " [green](default)[/green]" if alias == DEFAULT_LOCAL_MODEL else ""
        table.add_row(alias + mark, spec.repo, str(spec.dim), spec.pooling)
    console.print(table)
    console.print(
        "\n[dim]Any HuggingFace repo id also works for embeddings (mean pooling\n"
        "assumed). Chat takes any mlx-lm repo id via MASHUP_LOCAL_CHAT_MODEL.[/dim]"
    )


def _local(backend: str) -> bool:
    return backend == "local"


@app.command()
def status(workdir: WORKDIR_OPT = None) -> None:
    """Show what has been ingested, enriched, and embedded."""
    from mashup.store import Store

    cfg = _config(workdir, require_key=False)
    if not cfg.db_path.exists():
        err.print(f"No archive at {cfg.db_path}. Run `mashup ingest` first.")
        raise typer.Exit(1)
    with Store(cfg.db_path) as store:
        _show_counts(store.counts())
        # More than one line here means retrieval is comparing vectors from
        # different models, which is silently meaningless.
        used = store.embedding_models()
        if used:
            console.print()
            for model, n in sorted(used.items()):
                console.print(f"  {model or '[dim](model not recorded)[/dim]'}  {n}")
            if len(used) > 1:
                err.print("[red]Mixed embedding models — run `mashup embed --reset`.[/red]")


# ---- planning -----------------------------------------------------------


@app.command()
def build(
    prompt: Annotated[str, typer.Option("--prompt", "-p", help="What the mashup is about")],
    output: Annotated[Path, typer.Option("--output", "-o")] = Path("output"),
    duration: Annotated[float, typer.Option("--duration", "-d", help="Target seconds")] = 420.0,
    variants: Annotated[int, typer.Option("--variants", "-n", min=1, max=3)] = 3,
    workdir: WORKDIR_OPT = None,
    do_render: Annotated[bool, typer.Option("--render/--no-render")] = True,
    crossfade: Annotated[float, typer.Option("--crossfade", help="Seconds; 0 = hard cuts")] = 0.0,
    snap: Annotated[bool, typer.Option("--snap/--no-snap", help="Snap cuts to pauses")] = True,
    subtitles: Annotated[str, typer.Option("--subtitles", help="none|sidecar|burn")] = "sidecar",
    source_label: Annotated[
        bool,
        typer.Option(
            "--source-label/--no-source-label",
            help="Show source title and original timecode in the video",
        ),
    ] = True,
    watermark: Annotated[
        bool,
        typer.Option("--watermark/--no-watermark", help="Show a persistent watermark"),
    ] = True,
    watermark_text: Annotated[
        str,
        typer.Option("--watermark-text", help="Text shown in the persistent watermark"),
    ] = "MASHUP",
    baselines: Annotated[
        bool, typer.Option("--baselines", help="Also emit semantic + random controls")
    ] = False,
) -> None:
    """Plan mashup variants and render them."""
    # Planning needs embeddings; the brief parser degrades to regex without a
    # key, so with the local backend this runs entirely offline.
    cfg = _runnable(workdir, embed=True)
    target = parse_duration(prompt, duration)
    strategies = pipeline.AI_STRATEGIES[:variants]

    edls = pipeline.make_mashups(
        prompt,
        cfg,
        target=target,
        strategies=strategies,
        include_baselines=baselines,
        snap=snap,
        crossfade=crossfade,
    )

    output.mkdir(parents=True, exist_ok=True)
    for edl in edls:
        _summarise(edl)
        save_edl(edl, output / f"{edl.strategy}.json")
        if do_render:
            out = output / f"{edl.strategy}.mp4"
            render(
                edl,
                out,
                crossfade=crossfade,
                subtitles=subtitles,
                source_label=source_label,
                watermark=watermark,
                watermark_text=watermark_text,
                workdir=cfg.workdir,
                progress=_status(edl.strategy),
            )
            console.print(f"  -> {out}")
    console.print(f"\n[green]Wrote {len(edls)} variants to {output}[/green]")


@app.command()
def short(
    prompt: Annotated[str, typer.Option("--prompt", "-p", help="What the short is about")],
    output: Annotated[Path, typer.Option("--output", "-o")] = Path("output/short.mp4"),
    duration: Annotated[
        float, typer.Option("--duration", "-d", help="Target seconds, from 30 to 60")
    ] = 45.0,
    workdir: WORKDIR_OPT = None,
    visuals: Annotated[
        Path | None,
        typer.Option("--visuals", help="JSON manifest of clip-relative archival stills"),
    ] = None,
    do_render: Annotated[bool, typer.Option("--render/--no-render")] = True,
    subtitles: Annotated[str, typer.Option("--subtitles", help="none|sidecar|burn")] = "sidecar",
    source_label: Annotated[
        bool,
        typer.Option(
            "--source-label/--no-source-label",
            help="Show the persistent spoken-source heading",
        ),
    ] = True,
    watermark: Annotated[
        bool,
        typer.Option("--watermark/--no-watermark", help="Show a persistent watermark"),
    ] = True,
    watermark_text: Annotated[
        str,
        typer.Option("--watermark-text", help="Text shown in the persistent watermark"),
    ] = "MASHUP",
) -> None:
    """Make one complete 30–60 second source-faithful cut."""
    try:
        target = validate_short_duration(duration)
    except ValueError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc

    cfg = _runnable(workdir, embed=True)
    edl = pipeline.make_short(prompt, cfg, target=target)
    if visuals is not None:
        try:
            edl = attach_visual_manifest(edl, visuals)
        except ValueError as exc:
            err.print(f"[red]{exc}[/red]")
            raise typer.Exit(2) from exc

    output = output.with_suffix(".mp4")
    output.parent.mkdir(parents=True, exist_ok=True)
    save_edl(edl, output.with_suffix(".json"))
    _summarise(edl)
    if do_render:
        render(
            edl,
            output,
            subtitles=subtitles,
            source_label=source_label,
            watermark=watermark,
            watermark_text=watermark_text,
            workdir=cfg.workdir,
            progress=_status("short"),
        )
        console.print(f"[green]{output}[/green]")
    else:
        console.print(f"[green]{output.with_suffix('.json')}[/green]")


@app.command(name="collections")
def collections_cmd() -> None:
    """List checked-in editorial collections and their reusable angles."""
    for preset in list_collections():
        console.print(f"[bold]{preset.id}[/bold] — {preset.description}")
        console.print("  " + "  ".join(preset.angles))
        console.print(f"  [dim]{preset.source_policy}[/dim]")


@app.command(name="short-batch")
def short_batch(
    collection: Annotated[
        str, typer.Option("--collection", help="Editorial collection")
    ] = "startups",
    angle: Annotated[str | None, typer.Option("--angle", help="Named collection angle")] = None,
    prompt: Annotated[
        str | None,
        typer.Option("--prompt", "-p", help="Free-form angle override"),
    ] = None,
    count: Annotated[int, typer.Option("--count", "-n", min=3, max=5)] = 5,
    duration: Annotated[
        float,
        typer.Option("--duration", "-d", help="Target seconds per clip, from 30 to 60"),
    ] = 45.0,
    output: Annotated[Path, typer.Option("--output", "-o")] = Path("output/startups"),
    workdir: WORKDIR_OPT = None,
    do_render: Annotated[bool, typer.Option("--render/--no-render")] = True,
) -> None:
    """Prepare one distinct, vertical social batch for local human review."""
    try:
        preset = get_collection(collection)
        angle_id, resolved_prompt = preset.prompt_for(angle, prompt)
        target = validate_short_duration(duration)
    except ValueError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc

    if do_render and not has_subtitles_filter():
        err.print(
            "[red]The Startups social profile requires burned captions, but this FFmpeg "
            "build has no libass subtitles filter.[/red]\n"
            "Install a libass-enabled FFmpeg build, or validate the corpus now with --no-render."
        )
        raise typer.Exit(2)

    cfg = _runnable(workdir, embed=True)
    try:
        edls = pipeline.make_short_batch(
            resolved_prompt,
            cfg,
            count=count,
            target=target,
        )
    except ValueError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc

    output = output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    for index, edl in enumerate(edls, start=1):
        stem = f"clip-{index:02d}"
        save_edl(edl, output / f"{stem}.json")
        _summarise(edl)
        if do_render:
            render(
                edl,
                output / f"{stem}.mp4",
                subtitles="burn",
                source_label=True,
                watermark=True,
                watermark_text=preset.watermark,
                profile="social",
                workdir=cfg.workdir,
                progress=_status(stem),
            )

    batch = build_batch(
        edls,
        collection=preset.id,
        collection_name=preset.name,
        angle=angle_id,
        prompt=resolved_prompt,
        source_policy=preset.source_policy,
        output_dir=output,
    )
    manifest = save_batch(batch, output / "batch.json")
    review = save_review_html(batch, output / "index.html")
    console.print(f"\n[green]Batch manifest:[/green] {manifest}")
    console.print(f"[green]Review desk:[/green] {review}")


@app.command()
def preview(edl_path: Annotated[Path, typer.Argument()]) -> None:
    """Print the assembled transcript with source timestamps."""
    console.print(edl_to_transcript(load_edl(edl_path)))


@app.command(name="export-podcast-edit")
def export_podcast_edit_cmd(
    edl_path: Annotated[Path, typer.Argument(help="Existing Mashup EDL JSON")],
    output: Annotated[Path, typer.Option("--output", "-o")],
    provenance: Annotated[
        Path,
        typer.Option(
            "--provenance",
            help="Rights/provenance JSON covering every source file in the EDL",
        ),
    ],
    edit_id: Annotated[str | None, typer.Option("--id", help="Stable edit identity")] = None,
    approval: Annotated[
        str,
        typer.Option("--approval", help="proposed|approved|rejected"),
    ] = "proposed",
    approved_by: Annotated[str | None, typer.Option("--approved-by")] = None,
    watermark_text: Annotated[str, typer.Option("--watermark-text")] = "MASHUP",
) -> None:
    """Export a source-backed EDL for Mashup approval and rendering."""
    edl = load_edl(edl_path)
    try:
        payload = export_podcast_edit(
            edl,
            edit_id=edit_id or edl_path.stem,
            provenance_path=provenance,
            approval_status=approval,
            approved_by=approved_by,
            watermark_text=watermark_text,
        )
    except ValueError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    save_podcast_edit(payload, output)
    console.print(f"[green]{output}[/green]")


@app.command(name="render")
def render_cmd(
    edl_path: Annotated[Path, typer.Argument()],
    output: Annotated[Path, typer.Option("--output", "-o")],
    workdir: WORKDIR_OPT = None,
    crossfade: Annotated[float, typer.Option("--crossfade")] = 0.0,
    subtitles: Annotated[str, typer.Option("--subtitles")] = "sidecar",
    source_label: Annotated[
        bool,
        typer.Option(
            "--source-label/--no-source-label",
            help="Show source title and original timecode in the video",
        ),
    ] = True,
    watermark: Annotated[
        bool,
        typer.Option("--watermark/--no-watermark", help="Show a persistent watermark"),
    ] = True,
    watermark_text: Annotated[
        str,
        typer.Option("--watermark-text", help="Text shown in the persistent watermark"),
    ] = "MASHUP",
    profile: Annotated[str, typer.Option("--profile", help="source|social")] = "source",
) -> None:
    """Render an (optionally hand-edited) EDL to MP4."""
    cfg = _config(workdir, require_key=False)
    edl = load_edl(edl_path)
    if profile not in {"source", "social"}:
        err.print("[red]profile must be source or social[/red]")
        raise typer.Exit(2)
    render(
        edl,
        output,
        crossfade=crossfade,
        subtitles=subtitles,
        source_label=source_label,
        watermark=watermark,
        watermark_text=watermark_text,
        profile=profile,
        workdir=cfg.workdir,
        progress=_status("render"),
    )
    console.print(f"[green]{output}[/green]")


@app.command(name="media-receipt")
def media_receipt_cmd(
    podcast_edit_path: Annotated[Path, typer.Argument(help="Approved fleet.podcast-edit.v1 JSON")],
    video: Annotated[Path, typer.Option("--video", help="Completed local MP4")],
    output: Annotated[Path, typer.Option("--output", "-o", help="Receipt JSON destination")],
    duration: Annotated[float, typer.Option("--duration", help="Artifact duration in seconds")],
    width: Annotated[int, typer.Option("--width")],
    height: Annotated[int, typer.Option("--height")],
    captions: Annotated[Path | None, typer.Option("--captions")] = None,
) -> None:
    """Create the independent finished-media handoff without reading Mashup state."""
    try:
        payload = json.loads(podcast_edit_path.read_text(encoding="utf-8"))
        receipt = build_media_receipt(
            payload,
            video_path=video,
            captions_path=captions,
            duration_seconds=duration,
            width=width,
            height=height,
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    save_media_receipt(receipt, output)
    console.print(f"[green]{output}[/green]")


@app.command(name="serve")
def serve_cmd(
    edl_path: Annotated[Path, typer.Argument(help="EDL JSON to edit")],
    workdir: WORKDIR_OPT = None,
    host: Annotated[str, typer.Option("--host", help="Loopback only")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port")] = 8765,
) -> None:
    """Open the transcript editor for an EDL (Ctrl-C to stop)."""
    from mashup.serve import serve as serve_editor

    cfg = _config(workdir, require_key=False)
    if not edl_path.exists():
        err.print(f"[red]No EDL at {edl_path}[/red]")
        raise typer.Exit(1)
    console.print(f"[green]http://{host}:{port}[/green]  editing {edl_path}")
    try:
        serve_editor(edl_path, cfg, host=host, port=port)
    except ValueError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc


# ---- validation ---------------------------------------------------------


def _report_order_stats(outdir: Path) -> None:
    """Show whether the planner's order is even ahead on its own objective.

    Printed before anyone is recruited: if the planned arm is mid-pack among
    arbitrary orders, viewers cannot be expected to prefer it, and the thing
    to fix is the planner rather than the study.
    """
    import json as _json

    stats = _json.loads((outdir / "KEY.json").read_text()).get("order_stats")
    if not stats:
        return
    pct = stats["planned_percentile"]
    console.print(
        f"\n[dim]planner order scores {stats['planned_score']:.4f}, ahead of {pct:.0f}% of "
        f"{stats['shuffles']} arbitrary orders of the same clips\n"
        f"comparator (median shuffle) scores {stats['shuffled_score']:.4f}[/dim]"
    )
    if pct < 75:
        err.print(
            f"[yellow]the planner's own objective ranks its order in the top {100 - pct:.0f}% "
            f"only — a null result here would be the planner's fault, not the thesis's.[/yellow]"
        )


@app.command()
def coverage(
    prompt: Annotated[str, typer.Option("--prompt", "-p")],
    workdir: WORKDIR_OPT = None,
) -> None:
    """Does the archive hold material on this topic, or only noise?

    Run this before an experiment. Similarity from an asymmetric embedding
    model has a floor well above zero, so a brief the archive cannot serve
    still returns confident-looking clips.
    """
    cfg = _runnable(workdir, embed=True)
    cov = pipeline.check_coverage(prompt, cfg)
    verdict = (
        "[green]archive supports this brief[/green]"
        if cov.viable
        else "[red]not supported by this archive[/red]"
    )
    console.print(f"{verdict}\n  {cov.explain()}")
    console.print(
        f"\n  top {cov.top_k} mean {cov.top_k_mean:.3f}   "
        f"nonsense floor {cov.floor:.3f}   lift {cov.lift:+.3f}"
    )
    if not cov.viable:
        raise typer.Exit(1)


@app.command()
def experiment(
    prompt: Annotated[str, typer.Option("--prompt", "-p")],
    output: Annotated[Path, typer.Option("--output", "-o")] = Path("experiment"),
    duration: Annotated[float, typer.Option("--duration", "-d")] = 420.0,
    workdir: WORKDIR_OPT = None,
    seed: Annotated[int, typer.Option("--seed", help="Shuffles the blind labels")] = 0,
    pool: Annotated[
        int,
        typer.Option(
            "--pool",
            help="Candidates retrieved before planning; widen if a variant comes back short",
        ),
    ] = DEFAULT_POOL,
    matched: Annotated[
        bool,
        typer.Option(
            "--matched",
            help="Two arms with identical clips in different orders — tests sequencing alone",
        ),
    ] = False,
    strategy: Annotated[
        str, typer.Option("--strategy", help="Which planner the matched pair uses")
    ] = "escalation",
    do_render: Annotated[bool, typer.Option("--render/--no-render")] = True,
    subtitles: Annotated[str, typer.Option("--subtitles")] = "sidecar",
) -> None:
    """Generate the blind conditions for the validation experiment.

    Five conditions by default, which measures the pipeline end to end.
    `--matched` generates two instead, from one clip set in two orders, which
    is the only design here that attributes a preference to sequencing.
    """
    from mashup.experiment import run_experiment, run_matched_experiment

    cfg = _runnable(workdir, embed=True)
    target = parse_duration(prompt, duration)

    cov = pipeline.check_coverage(prompt, cfg)
    if not cov.viable:
        err.print(f"[red]this archive cannot serve that brief[/red]\n  {cov.explain()}")
        err.print("  Run `mashup coverage --prompt ...` to find one it can.")
        raise typer.Exit(1)
    err.print(f"[dim]coverage: {cov.explain()}[/dim]")

    if matched:
        blinds = run_matched_experiment(
            prompt, cfg, outdir=output, target=target, strategy=strategy, seed=seed, pool=pool
        )
        _report_order_stats(output)
    else:
        blinds = run_experiment(prompt, cfg, outdir=output, target=target, seed=seed, pool=pool)

    for blind in blinds:
        edl = load_edl(blind.edl_path)
        console.print(f"[bold]{blind.label}[/bold]  {len(edl.clips)} clips  {edl.duration:.0f}s")
        if do_render:
            out = output / f"{blind.label}.mp4"
            render(
                edl,
                out,
                subtitles=subtitles,
                workdir=cfg.workdir,
                progress=_status(blind.label),
            )
    console.print(
        f"\n[green]Wrote {len(blinds)} blind variants to {output}[/green]\n"
        f"Rate them in {output / 'ratings.csv'} — do not open KEY.json first.\n"
        f"Hand raters the .mp4 files only: the .json and .srt name the condition.\n"
        f"Then run: mashup evaluate {output}"
    )


@app.command(name="order-test")
def order_test(
    prompt: Annotated[str | None, typer.Option("--prompt", "-p")] = None,
    study: Annotated[
        Path | None,
        typer.Option("--study", help="Audit an existing blind set instead of planning a new one"),
    ] = None,
    duration: Annotated[float, typer.Option("--duration", "-d")] = 420.0,
    strategy: Annotated[str, typer.Option("--strategy")] = "escalation",
    pool: Annotated[int, typer.Option("--pool")] = DEFAULT_POOL,
    sweep_it: Annotated[
        bool, typer.Option("--sweep", help="Every strategy at every pool, to choose a study arm")
    ] = False,
    pools: Annotated[
        str, typer.Option("--pools", help="Comma-separated pools for --sweep")
    ] = "40,80,160",
    shuffles: Annotated[int, typer.Option("--shuffles")] = DEFAULT_SHUFFLES,
    workdir: WORKDIR_OPT = None,
) -> None:
    """Does the planner's order beat an arbitrary one? Run before recruiting.

    Shuffles a clip set many times and reports where the planner's own order
    lands. Near the median means the planner did not order those clips well,
    and a blind study built on it would test the planner rather than the
    thesis.
    """
    from mashup import ordertest

    cfg = _runnable(workdir, embed=True)
    if study is not None:
        _audit_study(study, cfg, shuffles)
        return
    if prompt is None:
        err.print("[red]need --prompt, or --study to audit an existing set[/red]")
        raise typer.Exit(2)

    target = parse_duration(prompt, duration)
    if not sweep_it:
        null = ordertest.test_configuration(
            prompt, cfg, target=target, strategy=strategy, pool=pool, shuffles=shuffles
        )
        _show_null(f"{strategy} @ pool {pool}", null)
        raise typer.Exit(0 if null.confident else 1)

    try:
        pool_list = tuple(int(p) for p in pools.split(",") if p.strip())
    except ValueError as exc:
        err.print(f"[red]--pools must be comma-separated integers: {pools!r}[/red]")
        raise typer.Exit(2) from exc

    table = Table(box=None)
    for col, just in (
        ("pool", "right"),
        ("strategy", "left"),
        ("clips", "right"),
        ("planned", "right"),
        ("median", "right"),
        ("pctile", "right"),
        ("gap", "right"),
    ):
        table.add_column(col, justify=just)
    results = ordertest.sweep(
        prompt,
        cfg,
        target=target,
        strategies=pipeline.AI_STRATEGIES,
        pools=pool_list,
        shuffles=shuffles,
        progress=lambda p, s, _n: err.print(f"[dim]  planned {s} @ pool {p}[/dim]"),
    )
    for pool_used, null in results:
        table.add_row(
            str(pool_used),
            null.strategy,
            str(null.clips),
            f"{null.actual:.4f}",
            f"{null.median:.4f}",
            f"{null.percentile:.1f}%",
            f"{null.gap:+.4f}",
        )
    console.print(table)

    best = ordertest.best_of(results)
    if best is None:
        return
    best_pool, best_null = best
    console.print(
        f"\n[bold]{best_null.strategy} at pool {best_pool}[/bold] — ahead of "
        f"{best_null.percentile:.0f}% of arbitrary orders, {best_null.gap:+.4f} on the median."
    )
    console.print(
        "[dim]Choosing on the objective before anyone has rated anything is\n"
        "pre-registration, not p-hacking. Record the choice.[/dim]"
    )


def _show_null(label: str, null) -> None:
    console.print(
        f"[bold]{label}[/bold]  {null.clips} clips, {len(null.scores)} shuffles\n"
        f"  planner order   {null.actual:.4f}\n"
        f"  median shuffle  {null.median:.4f}\n"
        f"  worst / best    {null.worst:.4f} / {null.best:.4f}\n"
        f"  percentile      {null.percentile:.1f}%   gap {null.gap:+.4f}"
    )
    blind = null.order_blind_weight()
    console.print(f"  [dim]{blind:.0%} of this objective cannot see order at all[/dim]")
    if not null.confident:
        err.print(
            "[yellow]not confidently ahead of chance — a study on this configuration\n"
            "would be testing the planner, not the thesis.[/yellow]"
        )


def _audit_study(study: Path, cfg, shuffles: int) -> None:
    from mashup import ordertest

    err.print("[yellow]this reveals which variant is which — not for a rater's screen[/yellow]")
    try:
        audit = ordertest.audit_study(study, cfg, shuffles=shuffles)
    except (OSError, RuntimeError, ValueError) as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    console.print(f"[bold]{study}[/bold]  prompt {audit.prompt!r}\n")

    console.print("[bold]shared material[/bold]  (Jaccard on clip sets)")
    for (a, b), jac in sorted(audit.overlap.items(), key=lambda kv: -kv[1]):
        console.print(f"  {a:<15}{b:<15}{jac:>6.2f}")
    isolated = audit.isolated()
    if isolated:
        names = ", ".join(f"{c} ({v:.2f})" for c, v in isolated)
        err.print(
            f"\n[yellow]{names} share almost nothing with any other variant, so a\n"
            "preference involving them is about selection, not ordering.\n"
            "See `experiment --matched`.[/yellow]"
        )

    blind = set(audit.order_blind())
    console.print("\n[bold]planner order vs arbitrary orders of the same clips[/bold]")
    for cond, null in audit.nulls.items():
        note = "  [dim]<- ending penalty only[/dim]" if cond in blind else ""
        console.print(
            f"  {cond:<15}{null.actual:>8.4f}  median {null.median:.4f}  "
            f"{null.percentile:>5.1f}%{note}"
        )
    if blind:
        console.print(
            f"\n[dim]{', '.join(sorted(blind))} are scored under a profile with no "
            "order-sensitive terms.\nA shuffle can only change whether they end on a "
            "`can_end` clip, so their\npercentile is the 6% ending penalty, not evidence "
            "about ordering.[/dim]"
        )

    dead = audit.dead_terms()
    if dead and audit.is_matched():
        console.print(
            "\n[dim]every variant holds the same clips, so the order-invariant terms\n"
            "are identical by construction rather than by defect.[/dim]"
        )
    elif dead:
        console.print("\n[bold]terms that barely vary across conditions[/bold]")
        for term, spread in dead:
            console.print(f"  {term:<24} spread {spread:.3f}")
        console.print("\n[bold]share of each score from those terms[/bold]")
        for cond in audit.variants:
            console.print(f"  {cond:<15}{audit.constant_share(cond):>6.0%}")


@app.command()
def evaluate(
    outdir: Annotated[Path, typer.Argument(help="Experiment directory")],
) -> None:
    """Unblind a completed rating sheet and check the PRD's criteria."""
    import json as _json

    from mashup.experiment import summarise_matched, summarise_ratings

    try:
        design = _json.loads((outdir / "KEY.json").read_text()).get("design", "five-condition")
        result = (summarise_matched if design == "matched" else summarise_ratings)(outdir)
    except (OSError, RuntimeError, ValueError) as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    if design == "matched":
        console.print(f"viewers: {result['viewers']}  ({result['decided']} expressed a preference)")
        console.print(f"[bold]{result['verdict']}[/bold]")
        mark = (
            "[green]significant[/green]"
            if result["significant"]
            else "[yellow]not significant[/yellow]"
        )
        console.print(f"  {mark} at p < 0.05")
        return

    console.print(f"viewers: {result['viewers']}")
    console.print("beats semantic baseline: " + str(result["beats_semantic"]))
    console.print(f"best AI condition: [bold]{result['best_ai_condition']}[/bold]")
    for name, passed in result["criteria"].items():
        mark = "[green]PASS[/green]" if passed else "[red]FAIL[/red]"
        console.print(f"  {mark}  {name}")


@app.command()
def churn(
    original: Annotated[Path, typer.Argument(help="Generated EDL")],
    edited: Annotated[Path, typer.Argument(help="EDL after human editing")],
) -> None:
    """Measure how much of the generated timeline a creator had to change."""
    from mashup.experiment import timeline_churn

    result = timeline_churn(load_edl(original), load_edl(edited))
    _show_counts({k: v for k, v in result.items() if isinstance(v, int)})
    verdict = (
        "[green]within the kill criterion[/green]"
        if result["passes_kill_criterion"]
        else "[red]exceeds the 30% kill criterion[/red]"
    )
    console.print(f"churn {result['churn']:.1%} — {verdict}")


@app.callback()
def main(
    ctx: typer.Context,
    input_dir: Annotated[Path | None, typer.Option("--input", "-i")] = None,
    prompt: Annotated[str | None, typer.Option("--prompt", "-p")] = None,
    duration: Annotated[float, typer.Option("--duration", "-d")] = 420.0,
    variants: Annotated[int, typer.Option("--variants", "-n", min=1, max=3)] = 3,
    output: Annotated[Path, typer.Option("--output", "-o")] = Path("output"),
    workdir: WORKDIR_OPT = None,
) -> None:
    """Run the whole pipeline in one shot when called without a subcommand.

    mashup --input ./archive --prompt "..." --duration 420 --variants 3
    """
    if ctx.invoked_subcommand is not None:
        return
    if input_dir is None or prompt is None:
        console.print(ctx.get_help())
        raise typer.Exit(0)

    cfg = _runnable(workdir, chat=True, embed=True)
    err.print("[dim]ingesting…[/dim]")
    pipeline.ingest(input_dir, cfg)
    err.print("[dim]enriching…[/dim]")
    pipeline.enrich(cfg, progress=_progress("enrich"))
    err.print("[dim]embedding…[/dim]")
    pipeline.embed(cfg, progress=_progress("embed"), notice=_notice)

    target = parse_duration(prompt, duration)
    edls = pipeline.make_mashups(
        prompt, cfg, target=target, strategies=pipeline.AI_STRATEGIES[:variants]
    )
    output.mkdir(parents=True, exist_ok=True)
    for edl in edls:
        _summarise(edl)
        save_edl(edl, output / f"{edl.strategy}.json")
        out = output / f"{edl.strategy}.mp4"
        render(edl, out, workdir=cfg.workdir, progress=_status(edl.strategy))
        console.print(f"  -> {out}")


if __name__ == "__main__":
    app()
