"""token_manifest.json builder and atomic writer.

Schema matches :doc:`/_design/05_EXPORT.md` § ``token_manifest.json``.
``exported_at`` is ISO-8601 UTC.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .tokens import TokenInfo


SCHEMA_VERSION = 1


def _utc_now_iso() -> str:
    # ISO-8601 with a literal trailing 'Z' (no '+00:00') matches the schema
    # example in 05_EXPORT.md.
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_manifest(
    *,
    speaker_id: str,
    condition: str,
    audio_source: str,
    sample_rate: int,
    pad_ms: int,
    fade_ms: int,
    audio_format: str,
    tokens: Iterable[TokenInfo],
    exported_at: str | None = None,
) -> dict[str, Any]:
    """Produce the manifest dict for a successful export.

    ``audio_source`` is the relative path to the source audio used for slicing
    (typically ``output/<speaker>/<condition>/denoised.wav`` or the raw source).
    The caller is responsible for choosing the path string — this function does
    not touch the filesystem.
    """
    token_list = list(tokens)
    tokens_per_word: dict[str, int] = dict(
        Counter(t.assigned_name for t in token_list)
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "speaker_id": speaker_id,
        "condition": condition,
        "exported_at": exported_at if exported_at is not None else _utc_now_iso(),
        "audio_source": audio_source,
        "sample_rate": sample_rate,
        "pad_ms": pad_ms,
        "fade_ms": fade_ms,
        "audio_format": audio_format,
        "tokens_per_word": tokens_per_word,
        "tokens": [
            {
                "filename": t.filename,
                "assigned_name": t.assigned_name,
                "token_index": t.token_index,
                "start_sec": t.start_sec,
                "end_sec": t.end_sec,
                "duration_ms": t.duration_ms,
                "padded_start_sec": t.padded_start_sec,
                "padded_end_sec": t.padded_end_sec,
                "padded_duration_ms": t.padded_duration_ms,
            }
            for t in token_list
        ],
    }


def write_manifest(path: Path | str, manifest: dict[str, Any]) -> None:
    """Atomically write ``manifest`` as pretty-printed JSON to ``path``."""
    final_path = Path(path)
    tmp_path = final_path.with_suffix(final_path.suffix + ".tmp")
    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(manifest, indent=2, ensure_ascii=False)
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, final_path)
