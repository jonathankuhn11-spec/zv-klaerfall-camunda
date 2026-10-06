"""Gemeinsame Fixtures: Beispieldaten, festes Fachdatum, isolierte Dienste pro Test."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from zv_klaerfall.config import Dienste, Einstellungen

WURZEL = Path(__file__).resolve().parents[1]
SAMPLES = WURZEL / "samples" / "pain.001"
BPMN = WURZEL / "bpmn"
FACHDATUM = date.fromisoformat((SAMPLES / "fachdatum.txt").read_text().strip())


@pytest.fixture
def fachdatum() -> date:
    return FACHDATUM


@pytest.fixture
def samples() -> Path:
    return SAMPLES


@pytest.fixture
def bpmn_dir() -> Path:
    return BPMN


@pytest.fixture
def dienste(tmp_path: Path) -> Dienste:
    return Dienste(Einstellungen(datenverzeichnis=tmp_path / "data"))


def sample(name: str) -> bytes:
    return (SAMPLES / name).read_bytes()
