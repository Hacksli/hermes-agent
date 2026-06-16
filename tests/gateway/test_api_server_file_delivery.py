"""
Tests for api_server file delivery — surfacing agent-generated files back
through the HTTP/SSE response so they route to the platform the user actually
spoke on (web/API), instead of always going to the Telegram gateway.

Covers:
- _build_file_payload: shape, mime/kind, base64 data URL, size cap, bad input
- _emit_file_to_sink: pushes to the active sink, no-ops without one
- send_document/send_image_file/send_voice/send_video: route to the sink when
  active, fall back to text-only base behaviour otherwise
- _deliver_files_via_sink: extracts MEDIA: paths and pushes classified payloads
"""

import asyncio
import os

import pytest

from gateway.config import PlatformConfig
from gateway.platforms import api_server as A
from gateway.platforms.api_server import APIServerAdapter, MAX_OUTBOUND_FILE_BYTES


def _adapter() -> APIServerAdapter:
    return APIServerAdapter(PlatformConfig(extra={"host": "127.0.0.1", "port": 8642, "key": "k"}))


class TestBuildFilePayload:
    def test_builds_data_url_payload(self, tmp_path):
        ad = _adapter()
        f = tmp_path / "report.pdf"
        f.write_bytes(b"%PDF-1.4 hello world")
        payload = ad._build_file_payload(str(f), "file", caption="травневий звіт")
        assert payload is not None
        assert payload["name"] == "report.pdf"
        assert payload["mime"] == "application/pdf"
        assert payload["kind"] == "file"
        assert payload["size"] == len(b"%PDF-1.4 hello world")
        assert payload["caption"] == "травневий звіт"
        assert payload["data"].startswith("data:application/pdf;base64,")

    def test_missing_file_returns_none(self, tmp_path):
        ad = _adapter()
        assert ad._build_file_payload(str(tmp_path / "nope.pdf"), "file") is None

    def test_empty_file_returns_none(self, tmp_path):
        ad = _adapter()
        f = tmp_path / "empty.txt"
        f.write_bytes(b"")
        assert ad._build_file_payload(str(f), "file") is None

    def test_oversized_file_returns_none(self, tmp_path, monkeypatch):
        ad = _adapter()
        f = tmp_path / "big.bin"
        f.write_bytes(b"x" * 1024)
        monkeypatch.setattr(A, "MAX_OUTBOUND_FILE_BYTES", 512)
        assert ad._build_file_payload(str(f), "file") is None


class TestEmitFileToSink:
    def test_no_sink_returns_false(self, tmp_path):
        ad = _adapter()
        f = tmp_path / "a.txt"
        f.write_bytes(b"hi")
        assert ad._emit_file_to_sink(str(f), "file") is False

    def test_pushes_to_active_sink(self, tmp_path):
        ad = _adapter()
        f = tmp_path / "a.png"
        f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 20)
        collected = []
        token = A._API_FILE_SINK.set(collected.append)
        try:
            assert ad._emit_file_to_sink(str(f), "image", caption="cap") is True
        finally:
            A._API_FILE_SINK.reset(token)
        assert len(collected) == 1
        assert collected[0]["kind"] == "image"
        assert collected[0]["caption"] == "cap"
        assert collected[0]["mime"] == "image/png"


class TestAdapterSendOverrides:
    def test_send_document_routes_to_sink(self, tmp_path):
        ad = _adapter()
        f = tmp_path / "x.pdf"
        f.write_bytes(b"%PDF-1.4 data")
        collected = []
        token = A._API_FILE_SINK.set(collected.append)
        try:
            res = asyncio.run(ad.send_document(chat_id="c", file_path=str(f), caption="cc"))
        finally:
            A._API_FILE_SINK.reset(token)
        assert res.success is True
        assert [c["kind"] for c in collected] == ["file"]
        assert collected[0]["caption"] == "cc"

    def test_send_image_file_routes_to_sink(self, tmp_path):
        ad = _adapter()
        f = tmp_path / "x.png"
        f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"y" * 10)
        collected = []
        token = A._API_FILE_SINK.set(collected.append)
        try:
            res = asyncio.run(ad.send_image_file(chat_id="c", image_path=str(f)))
        finally:
            A._API_FILE_SINK.reset(token)
        assert res.success is True
        assert collected[0]["kind"] == "image"

    def test_send_video_routes_to_sink(self, tmp_path):
        ad = _adapter()
        f = tmp_path / "x.mp4"
        f.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"z" * 20)
        collected = []
        token = A._API_FILE_SINK.set(collected.append)
        try:
            res = asyncio.run(ad.send_video(chat_id="c", video_path=str(f)))
        finally:
            A._API_FILE_SINK.reset(token)
        assert res.success is True
        assert collected[0]["kind"] == "video"

    def test_send_document_falls_back_without_sink(self, tmp_path):
        ad = _adapter()
        f = tmp_path / "x.pdf"
        f.write_bytes(b"%PDF-1.4 data")
        # No active sink → base behaviour, which routes to send() (disabled here).
        res = asyncio.run(ad.send_document(chat_id="c", file_path=str(f)))
        assert res.success is False


class TestDeliverFilesViaSink:
    def test_extracts_media_paths(self, tmp_path, monkeypatch):
        ad = _adapter()
        pdf = tmp_path / "report.pdf"
        pdf.write_bytes(b"%PDF-1.4 doc")
        ogg = tmp_path / "reply.ogg"
        ogg.write_bytes(b"OggS" + b"a" * 30)
        # Allow the temp dir so the media-delivery security validator accepts it.
        monkeypatch.setenv("HERMES_MEDIA_ALLOW_DIRS", str(tmp_path))

        # extract_media uses a regex tuned for POSIX paths; feed the paths in
        # directly so the test is platform-independent.
        monkeypatch.setattr(
            ad, "extract_media",
            lambda text: ([(str(pdf), False), (str(ogg), False)], text),
        )
        monkeypatch.setattr(ad, "extract_images", lambda text: ([], text))
        monkeypatch.setattr(ad, "extract_local_files", lambda text: ([], text))

        collected = []
        token = A._API_FILE_SINK.set(collected.append)
        try:
            ad._deliver_files_via_sink("ось ваші файли")
        finally:
            A._API_FILE_SINK.reset(token)

        kinds = {c["name"]: c["kind"] for c in collected}
        assert kinds.get("report.pdf") == "file"
        assert kinds.get("reply.ogg") == "audio"

    def test_no_sink_is_noop(self):
        ad = _adapter()
        # Must not raise when no request/sink is active.
        ad._deliver_files_via_sink("MEDIA:/tmp/whatever.pdf")


class TestRehydrate:
    """Transcript reload: re-serve already-stored files via the denylist path."""

    def test_collect_trust_existing_reads_old_file(self, tmp_path, monkeypatch):
        ad = _adapter()
        pdf = tmp_path / "report.pdf"
        pdf.write_bytes(b"%PDF-1.4 doc")
        png = tmp_path / "chart.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 20)
        # Make the files "old" so the strict recency validator would reject them.
        old = 1_000_000_000
        for f in (pdf, png):
            os.utime(f, (old, old))
        monkeypatch.setattr(
            ad, "extract_media",
            lambda text: ([(str(pdf), False)], text),
        )
        monkeypatch.setattr(ad, "extract_images", lambda text: ([], text))
        monkeypatch.setattr(ad, "extract_local_files", lambda text: ([str(png)], text))

        # Strict mode rejects (not under an allowed root, too old) → empty.
        assert ad._collect_file_payloads("text", trust_existing=False) == []
        # Trust mode re-serves the existing files.
        out = ad._collect_file_payloads("text", trust_existing=True)
        kinds = sorted(p["kind"] for p in out)
        assert kinds == ["file", "image"]

    def test_is_safe_rehydrate_path_blocks_credentials(self, tmp_path):
        ad = _adapter()
        f = tmp_path / "ok.pdf"
        f.write_bytes(b"%PDF data")
        assert ad._is_safe_rehydrate_path(str(f)) is True
        assert ad._is_safe_rehydrate_path("/etc/passwd") is False
        assert ad._is_safe_rehydrate_path(str(tmp_path / "missing.pdf")) is False
