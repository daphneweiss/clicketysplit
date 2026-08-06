"""Scan a recordings root and discover speakers/conditions.

This module walks a user-picked
directory and proposes the speakers/conditions/files the Setup Wizard
should pre-fill. The scan is intentionally cheap — ``stat`` only, never
read audio — and deterministic (sort by lowercase name throughout).

Discovery rules (from the 02 doc):

- A directory immediately under ``root`` becomes a speaker if it contains
  either audio files (any extension in ``audio_io.SUPPORTED_EXTENSIONS``)
  or subdirectories containing audio.
- A subdir under a speaker becomes a condition if it contains audio files.
- Skip names that start with ``.`` or ``_``, plus reserved names
  ``output``, ``tokens``, ``final``, ``alternates``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .audio_io import SUPPORTED_EXTENSIONS

__all__ = [
    "DiscoveryResult",
    "ScannedCondition",
    "ScannedSpeaker",
    "scan_recordings",
]


# Reserved names that look like a speaker/condition but never are. These
# match the canonical output-tree layout (``output/``,
# ``tokens/``) plus the legacy pipeline's ``final``/``alternates`` dirs.
_RESERVED_NAMES: frozenset[str] = frozenset(
    {"output", "tokens", "final", "alternates"}
)


@dataclass(frozen=True)
class ScannedCondition:
    """An audio-containing subdirectory under a speaker.

    ``files`` is the absolute, lowercase-name-sorted list of audio files
    directly inside the condition dir (not recursive). ``total_bytes`` is
    the sum of ``Path.stat().st_size`` for those files — purely for
    display in the wizard.
    """

    name: str
    files: list[Path]
    n_files: int
    total_bytes: int


@dataclass(frozen=True)
class ScannedSpeaker:
    """A directory immediately under ``root`` that contains audio.

    Two layouts are supported and surfaced explicitly:

    - ``conditions`` populated, ``flat_files`` empty — speaker has one or
      more condition subdirs (the typical case).
    - ``flat_files`` populated, ``conditions`` empty — speaker has audio
      directly under the speaker dir with no condition subdirs (the
      single-condition flat layout).

    If both would be populated the design treats subdir conditions as
    primary and clears ``flat_files`` per the spec.
    """

    id: str
    subdir: str
    conditions: list[ScannedCondition]
    flat_files: list[Path]


@dataclass(frozen=True)
class DiscoveryResult:
    """What :func:`scan_recordings` returns."""

    root: Path
    speakers: list[ScannedSpeaker]
    unique_condition_names: list[str]


def _is_skipped_name(name: str) -> bool:
    """Hidden/underscore-prefixed names and reserved output-tree names skip."""
    if not name:
        return True
    if name.startswith((".", "_")):
        return True
    return name.lower() in _RESERVED_NAMES


def _is_audio_file(p: Path) -> bool:
    """Cheap audio test: extension only, no header sniff."""
    return p.suffix.lower() in SUPPORTED_EXTENSIONS


def _audio_files_in(dir_path: Path) -> list[Path]:
    """Return sorted audio files directly under ``dir_path`` (non-recursive)."""
    try:
        entries = list(dir_path.iterdir())
    except (OSError, PermissionError):
        return []
    files = [
        e
        for e in entries
        if e.is_file()
        and not _is_skipped_name(e.name)
        and _is_audio_file(e)
    ]
    files.sort(key=lambda p: p.name.lower())
    return files


def _scan_condition(cond_dir: Path) -> ScannedCondition | None:
    """Build a :class:`ScannedCondition` if ``cond_dir`` has audio, else None."""
    files = _audio_files_in(cond_dir)
    if not files:
        return None
    total_bytes = 0
    for f in files:
        try:
            total_bytes += f.stat().st_size
        except OSError:
            # Don't fail the whole scan over one unreadable file; the wizard
            # surfaces sizes purely for display.
            continue
    return ScannedCondition(
        name=cond_dir.name,
        files=[f.resolve() for f in files],
        n_files=len(files),
        total_bytes=total_bytes,
    )


def _scan_speaker(speaker_dir: Path) -> ScannedSpeaker | None:
    """Build a :class:`ScannedSpeaker` if ``speaker_dir`` contains audio.

    Returns ``None`` if the directory has neither flat-layout audio nor any
    condition subdir with audio — which is also the "only ``final/`` lives
    here" case, since ``final`` is reserved.
    """
    try:
        entries = list(speaker_dir.iterdir())
    except (OSError, PermissionError):
        return None

    subdirs = sorted(
        [
            e
            for e in entries
            if e.is_dir() and not _is_skipped_name(e.name)
        ],
        key=lambda p: p.name.lower(),
    )

    conditions: list[ScannedCondition] = []
    for sub in subdirs:
        cond = _scan_condition(sub)
        if cond is not None:
            conditions.append(cond)

    flat_files = _audio_files_in(speaker_dir)

    if conditions:
        # Spec: ``flat_files`` is empty when the speaker has condition subdirs.
        return ScannedSpeaker(
            id=speaker_dir.name,
            subdir=speaker_dir.name,
            conditions=conditions,
            flat_files=[],
        )
    if flat_files:
        return ScannedSpeaker(
            id=speaker_dir.name,
            subdir=speaker_dir.name,
            conditions=[],
            flat_files=[f.resolve() for f in flat_files],
        )
    return None


def scan_recordings(root: Path) -> DiscoveryResult:
    """Walk ``root`` and return discovered speakers/conditions.

    See module docstring for the rule set. The scan never reads audio
    payload, only ``stat`` for sizes; this keeps it fast for large
    recording trees.
    """
    root = Path(root)
    try:
        entries = list(root.iterdir())
    except (OSError, PermissionError):
        return DiscoveryResult(root=root.resolve(), speakers=[], unique_condition_names=[])

    speaker_dirs = sorted(
        [
            e
            for e in entries
            if e.is_dir() and not _is_skipped_name(e.name)
        ],
        key=lambda p: p.name.lower(),
    )

    speakers: list[ScannedSpeaker] = []
    for sd in speaker_dirs:
        sp = _scan_speaker(sd)
        if sp is not None:
            speakers.append(sp)

    unique_names: set[str] = set()
    for sp in speakers:
        for c in sp.conditions:
            unique_names.add(c.name)

    return DiscoveryResult(
        root=root.resolve(),
        speakers=speakers,
        unique_condition_names=sorted(unique_names, key=str.lower),
    )
