"""File helpers: hashing, media inspection (pure Python MP4 parsing), size checks."""

from __future__ import annotations

import hashlib
import mimetypes
import shutil
import struct
from dataclasses import dataclass
from pathlib import Path

CHUNK = 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def guess_mime(path: Path | str) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "application/octet-stream"


def sniff_kind(path: Path) -> str:
    """Detect the real file kind from magic bytes (do not trust client-provided names)."""
    with open(path, "rb") as fh:
        head = fh.read(16)
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"GIF8"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[4:8] == b"ftyp":
        return "video/mp4"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "video/webm"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "audio/wav"
    if head.startswith(b"ID3") or head[:2] in (b"\xff\xfb", b"\xff\xf3"):
        return "audio/mpeg"
    return "application/octet-stream"


@dataclass
class MediaInfo:
    valid: bool
    container: str = ""
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None
    reason: str = ""


def inspect_mp4(path: Path) -> MediaInfo:
    """Lightweight MP4 validation without ffmpeg.

    Verifies the ``ftyp`` box, finds ``moov`` and extracts duration from ``mvhd`` and the
    visual track dimensions from ``tkhd``. Enough to detect truncated or HTML "downloads".
    """
    size = path.stat().st_size
    if size < 64:
        return MediaInfo(False, reason="file too small")
    with open(path, "rb") as fh:
        head = fh.read(12)
        if head[4:8] != b"ftyp":
            return MediaInfo(False, reason="missing ftyp box")
        fh.seek(0)
        info = MediaInfo(True, container="mp4")
        found_moov = False
        offset = 0
        while offset < size:
            fh.seek(offset)
            header = fh.read(8)
            if len(header) < 8:
                break
            box_size, box_type = struct.unpack(">I4s", header)
            header_len = 8
            if box_size == 1:
                large = fh.read(8)
                if len(large) < 8:
                    break
                box_size = struct.unpack(">Q", large)[0]
                header_len = 16
            elif box_size == 0:
                box_size = size - offset
            if box_size < header_len:
                return MediaInfo(False, container="mp4", reason="corrupt box size")
            if box_type == b"moov":
                found_moov = True
                fh.seek(offset + header_len)
                moov = fh.read(min(box_size - header_len, 8 * 1024 * 1024))
                _parse_moov(moov, info)
            offset += box_size
        if not found_moov:
            return MediaInfo(False, container="mp4", reason="missing moov box (truncated download?)")
        return info


def _iter_boxes(data: bytes):
    offset = 0
    while offset + 8 <= len(data):
        box_size, box_type = struct.unpack(">I4s", data[offset : offset + 8])
        header_len = 8
        if box_size == 1 and offset + 16 <= len(data):
            box_size = struct.unpack(">Q", data[offset + 8 : offset + 16])[0]
            header_len = 16
        elif box_size == 0:
            box_size = len(data) - offset
        if box_size < header_len:
            return
        yield box_type, data[offset + header_len : offset + box_size]
        offset += box_size


def _parse_moov(moov: bytes, info: MediaInfo) -> None:
    for box_type, payload in _iter_boxes(moov):
        if box_type == b"mvhd" and len(payload) >= 20:
            version = payload[0]
            if version == 1 and len(payload) >= 32:
                timescale = struct.unpack(">I", payload[20:24])[0]
                duration = struct.unpack(">Q", payload[24:32])[0]
            else:
                timescale = struct.unpack(">I", payload[12:16])[0]
                duration = struct.unpack(">I", payload[16:20])[0]
            if timescale:
                info.duration_seconds = round(duration / timescale, 3)
        elif box_type == b"trak":
            for sub_type, sub_payload in _iter_boxes(payload):
                if sub_type == b"tkhd" and len(sub_payload) >= 84:
                    version = sub_payload[0]
                    base = 88 if version == 1 else 76
                    if len(sub_payload) >= base + 8:
                        width = struct.unpack(">I", sub_payload[base : base + 4])[0] >> 16
                        height = struct.unpack(">I", sub_payload[base + 4 : base + 8])[0] >> 16
                        if width and height:
                            info.width, info.height = width, height


def inspect_media(path: Path) -> MediaInfo:
    kind = sniff_kind(path)
    if kind == "video/mp4":
        return inspect_mp4(path)
    if kind in ("video/webm",):
        return MediaInfo(True, container="webm")
    if kind.startswith("image/"):
        return MediaInfo(True, container=kind.split("/")[1])
    return MediaInfo(False, reason=f"unsupported or unrecognised file type ({kind})")


def move_file(src: Path, dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return dst


def dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for child in path.rglob("*"):
        if child.is_file():
            try:
                total += child.stat().st_size
            except OSError:
                pass
    return total
