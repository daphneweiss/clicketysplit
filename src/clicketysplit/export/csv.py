"""tokens.csv writer (UTF-8, RFC-4180-quoted).

Header is stable across schema versions; new columns are appended, never
inserted or reordered. See :doc:`/_design/05_EXPORT.md` § ``tokens.csv schema``.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Iterable

from .tokens import TokenInfo


CSV_HEADER: tuple[str, ...] = (
    "speaker_id",
    "condition",
    "assigned_name",
    "token_index",
    "filename",
    "start_sec",
    "end_sec",
    "duration_ms",
    "padded_duration_ms",
    "sample_rate",
    "audio_format",
)


def write_tokens_csv(
    path: Path | str,
    *,
    speaker_id: str,
    condition: str,
    sample_rate: int,
    audio_format: str,
    tokens: Iterable[TokenInfo],
) -> None:
    """Write a single ``tokens.csv`` with one row per exported token."""
    final_path = Path(path)
    tmp_path = final_path.with_suffix(final_path.suffix + ".tmp")
    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(CSV_HEADER)
        for t in tokens:
            writer.writerow(
                [
                    speaker_id,
                    condition,
                    t.assigned_name,
                    t.token_index,
                    t.filename,
                    t.start_sec,
                    t.end_sec,
                    t.duration_ms,
                    t.padded_duration_ms,
                    sample_rate,
                    audio_format,
                ]
            )
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, final_path)
