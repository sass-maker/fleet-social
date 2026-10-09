"""HTTP-level tests for the editor backend.

These drive a real server over a real socket (`http.client` against port 0 in a
thread) rather than calling handler methods directly: the things most likely to
break — Range replies, status codes, atomic writes — only exist at the wire
level.

`MASHUP_SERVE_OFFLINE` is set for every test so nothing can reach the gateway.
"""

from __future__ import annotations

import http.client
import json
import threading
from dataclasses import dataclass
from pathlib import Path

import pytest

from mashup.config import Config
from mashup.models import EDL, Clip, Cue, Role, Segment, SegmentMeta, Source
from mashup.render.edl_io import save_edl
from mashup.serve import build_server
from mashup.store import Store

# Byte i == i % 256, so any slice can be asserted without holding the blob.
MEDIA_BYTES = bytes(range(256)) * 40
MEDIA_SIZE = len(MEDIA_BYTES)


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self):
        return json.loads(self.body.decode("utf-8"))


class Client:
    def __init__(self, port: int) -> None:
        self.port = port

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            raw = conn.getresponse()
            return Response(raw.status, dict(raw.getheaders()), raw.read())
        finally:
            conn.close()

    def get_json(self, path: str):
        return self.request("GET", path).json()


@dataclass
class Editor:
    client: Client
    edl_path: Path
    media_path: Path
    cfg: Config


def _segment(sid: str, start: float, end: float, text: str, **meta) -> Segment:
    return Segment(
        id=sid,
        source_id="ep1",
        start=start,
        end=end,
        text=text,
        cue_start=0,
        cue_end=1,
        meta=SegmentMeta(summary=text[:30], **meta),
    )


def _clip(index: int, seg: Segment, media: Path) -> Clip:
    return Clip(
        index=index,
        segment_id=seg.id,
        source_id=seg.source_id,
        source_path=str(media),
        start=seg.start,
        end=seg.end,
        render_start=seg.start,
        render_end=seg.end,
        text=seg.text,
        summary=seg.meta.summary,
        role=seg.meta.role,
        energy=seg.meta.energy,
    )


@pytest.fixture
def editor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MASHUP_SERVE_OFFLINE", "1")

    workdir = tmp_path / "work"
    cfg = Config(
        gateway_url="http://gateway.invalid",
        gateway_api_key="",
        project_id="mashup",
        chat_model="auto",
        embed_model="test-embed",
        workdir=workdir,
    )
    cfg.ensure_dirs()

    media = cfg.workdir / "ep1.mp4"
    media.write_bytes(MEDIA_BYTES)

    segments = [
        _segment("s1", 0.0, 30.0, "A dolphin walked into the bar and ordered tap water."),
        _segment("s2", 30.0, 70.0, "My landlord raised the rent again.", role=Role.DEVELOPMENT),
        _segment("s3", 70.0, 100.0, "The dolphin came back for it.", role=Role.PUNCHLINE),
    ]
    with Store(cfg.db_path) as store:
        store.upsert_source(
            Source(
                id="ep1",
                path=str(media),
                title="Episode 1",
                duration=100.0,
                has_video=True,
            ),
            [Cue(index=0, start=0.0, end=2.0, text="hello")],
        )
        store.replace_segments("ep1", segments)

    edl = EDL(
        strategy="chronological",
        prompt="dolphins and rent",
        target_duration=70.0,
        generated_at="2026-07-25T00:00:00+00:00",
        clips=[_clip(0, segments[0], media), _clip(1, segments[2], media)],
        score=0.5,
        weights={"duration_fit": 1.0},
    )
    edl_path = tmp_path / "output" / "chronological.json"
    save_edl(edl, edl_path)

    server = build_server(edl_path, cfg, port=0, web_dist=tmp_path / "no-dist")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield Editor(Client(server.server_address[1]), edl_path, media, cfg)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# ---- EDL round trip ------------------------------------------------------


class TestEdlDocument:
    def test_get_returns_the_loaded_edl(self, editor: Editor):
        doc = editor.client.get_json("/api/edl")
        assert doc["strategy"] == "chronological"
        assert [c["segment_id"] for c in doc["clips"]] == ["s1", "s3"]
        assert doc["target_duration"] == 70.0

    def test_put_round_trips_and_rescores(self, editor: Editor):
        doc = editor.client.get_json("/api/edl")
        # Drop the first clip: 60s of material against a 70s target.
        doc["clips"] = doc["clips"][1:]
        response = editor.client.request(
            "PUT",
            "/api/edl",
            body=json.dumps(doc).encode(),
            headers={"Content-Type": "application/json"},
        )
        assert response.status == 200
        saved = response.json()

        # Indices are renumbered to positions.
        assert [c["index"] for c in saved["clips"]] == [0]
        # duration_fit is one of the terms recomputable without embeddings.
        assert response.headers["X-Mashup-Score-Mode"] == "partial"
        assert "duration_fit" in response.headers["X-Mashup-Score-Recomputed"]
        assert saved["terms"]["duration_fit"] == pytest.approx(1 - abs(30 - 70) / 70)
        assert saved["score"] == pytest.approx(saved["terms"]["duration_fit"])

        # And it landed on disk, still valid.
        on_disk = EDL.model_validate_json(editor.edl_path.read_text())
        assert [c.segment_id for c in on_disk.clips] == ["s3"]
        assert editor.client.get_json("/api/edl")["score"] == pytest.approx(saved["score"])

    def test_put_rejects_a_malformed_body(self, editor: Editor):
        before = editor.edl_path.read_text()
        doc = editor.client.get_json("/api/edl")
        doc["clips"][0]["role"] = "not-a-role"
        doc["clips"][1].pop("source_path")

        response = editor.client.request("PUT", "/api/edl", body=json.dumps(doc).encode())
        assert response.status == 422
        payload = response.json()
        assert payload["error"] == "EDL failed validation"
        fields = {".".join(str(p) for p in item["loc"]) for item in payload["detail"]}
        assert "clips.0.role" in fields
        assert "clips.1.source_path" in fields
        # A rejected edit must not touch the document.
        assert editor.edl_path.read_text() == before

    def test_put_rejects_non_json(self, editor: Editor):
        response = editor.client.request("PUT", "/api/edl", body=b"{nope")
        assert response.status == 400


# ---- media streaming -----------------------------------------------------


class TestMediaRange:
    def test_full_body_advertises_range_support(self, editor: Editor):
        response = editor.client.request("GET", "/api/media/ep1?start=0&end=30")
        assert response.status == 200
        assert response.headers["Accept-Ranges"] == "bytes"
        assert int(response.headers["Content-Length"]) == MEDIA_SIZE
        assert response.body == MEDIA_BYTES

    def test_byte_range_returns_206_with_content_range(self, editor: Editor):
        response = editor.client.request(
            "GET", "/api/media/ep1", headers={"Range": "bytes=100-199"}
        )
        assert response.status == 206
        assert response.headers["Content-Range"] == f"bytes 100-199/{MEDIA_SIZE}"
        assert response.headers["Content-Length"] == "100"
        assert response.body == MEDIA_BYTES[100:200]

    def test_open_ended_range_runs_to_eof(self, editor: Editor):
        start = MEDIA_SIZE - 64
        response = editor.client.request(
            "GET", "/api/media/ep1", headers={"Range": f"bytes={start}-"}
        )
        assert response.status == 206
        assert response.headers["Content-Range"] == f"bytes {start}-{MEDIA_SIZE - 1}/{MEDIA_SIZE}"
        assert response.body == MEDIA_BYTES[start:]

    def test_suffix_range(self, editor: Editor):
        response = editor.client.request("GET", "/api/media/ep1", headers={"Range": "bytes=-50"})
        assert response.status == 206
        assert response.body == MEDIA_BYTES[-50:]

    def test_unsatisfiable_range_is_416(self, editor: Editor):
        response = editor.client.request(
            "GET", "/api/media/ep1", headers={"Range": f"bytes={MEDIA_SIZE + 10}-"}
        )
        assert response.status == 416
        assert response.headers["Content-Range"] == f"bytes */{MEDIA_SIZE}"

    def test_head_reports_size_without_a_body(self, editor: Editor):
        response = editor.client.request("HEAD", "/api/media/ep1")
        assert response.status == 200
        assert response.headers["Content-Length"] == str(MEDIA_SIZE)
        assert response.body == b""


class TestMediaSafety:
    @pytest.mark.parametrize(
        "target",
        [
            "/api/media/../../../etc/passwd",
            "/api/media/%2e%2e%2f%2e%2e%2fetc%2fpasswd",
            "/api/media/..",
            "/api/media/%2Fetc%2Fpasswd",
        ],
    )
    def test_traversal_is_refused(self, editor: Editor, target: str):
        response = editor.client.request("GET", target)
        assert response.status == 403
        assert b"root:" not in response.body

    def test_absolute_path_outside_the_archive_is_refused(self, editor: Editor, tmp_path: Path):
        secret = tmp_path / "secret.mp4"
        secret.write_bytes(b"not yours")
        response = editor.client.request("GET", f"/api/media/{secret}")
        assert response.status == 403
        assert b"not yours" not in response.body

    def test_unknown_source_is_404(self, editor: Editor):
        assert editor.client.request("GET", "/api/media/ep404").status == 404


# ---- candidates ----------------------------------------------------------


class TestCandidates:
    def test_falls_back_to_substring_without_embeddings(self, editor: Editor):
        payload = editor.client.get_json("/api/candidates?q=dolphin")
        assert payload["mode"] == "substring"
        ids = [r["id"] for r in payload["results"]]
        assert ids == ["s1", "s3"]

    def test_result_shape_covers_the_replace_panel(self, editor: Editor):
        result = editor.client.get_json("/api/candidates?q=landlord%20rent")["results"][0]
        assert result["id"] == "s2"
        assert result["source_id"] == "ep1"
        assert result["start"] == 30.0
        assert result["end"] == 70.0
        assert result["duration"] == 40.0
        assert result["role"] == "development"
        assert 0.0 <= result["energy"] <= 1.0
        assert result["summary"]
        assert "landlord" in result["text"]
        # Needed to build a replacement Clip, which requires a source path.
        assert result["source_path"] == str(editor.media_path)

    def test_no_match_returns_nothing(self, editor: Editor):
        assert editor.client.get_json("/api/candidates?q=quantumchromodynamics")["results"] == []

    def test_limit_is_honoured(self, editor: Editor):
        payload = editor.client.get_json("/api/candidates?q=the&limit=1")
        assert len(payload["results"]) == 1

    def test_empty_query_browses_the_archive(self, editor: Editor):
        payload = editor.client.get_json("/api/candidates?q=")
        assert payload["mode"] == "browse"
        assert len(payload["results"]) == 3


# ---- segment detail (the "extend" control) -------------------------------


class TestSegmentDetail:
    def test_exposes_neighbours_in_the_same_source(self, editor: Editor):
        payload = editor.client.get_json("/api/segment/s2")
        assert payload["segment"]["id"] == "s2"
        assert payload["prev"]["id"] == "s1"
        assert payload["next"]["id"] == "s3"
        assert payload["source"]["has_video"] is True

    def test_edges_have_no_neighbour(self, editor: Editor):
        assert editor.client.get_json("/api/segment/s1")["prev"] is None
        assert editor.client.get_json("/api/segment/s3")["next"] is None

    def test_unknown_segment_is_404(self, editor: Editor):
        assert editor.client.request("GET", "/api/segment/nope").status == 404


# ---- static shell --------------------------------------------------------


class TestStatic:
    def test_unbuilt_ui_explains_itself(self, editor: Editor):
        response = editor.client.request("GET", "/")
        assert response.status == 503
        assert b"pnpm build" in response.body

    def test_unknown_api_route_is_json_404(self, editor: Editor):
        response = editor.client.request("GET", "/api/nope")
        assert response.status == 404
        assert response.json()["error"]
