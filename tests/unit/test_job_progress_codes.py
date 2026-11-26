"""Every stable Job and display code resolves through the English catalogue."""

from __future__ import annotations

import ast
import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from stemforge.domain import display, progress
from stemforge.domain.roles import INSTRUMENTAL_ROLE, STEM_ROLES

pytestmark = pytest.mark.unit

ENGLISH_CATALOG = (
    Path(__file__).resolve().parents[2] / "src" / "stemforge" / "i18n" / "catalogs" / "en.json"
)


def _catalog() -> Mapping[str, object]:
    payload: object = json.loads(ENGLISH_CATALOG.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _missing(codes: frozenset[str], catalog: Mapping[str, object]) -> list[str]:
    return sorted(code for code in codes if code not in catalog)


def test_every_job_stage_code_has_english_copy() -> None:
    assert _missing(progress.STAGE_CODES, _catalog()) == []


def test_every_job_detail_code_has_english_copy() -> None:
    assert _missing(progress.DETAIL_CODES, _catalog()) == []


def test_every_display_code_has_english_copy() -> None:
    assert _missing(display.DISPLAY_CODES, _catalog()) == []


def test_every_role_has_a_display_code() -> None:
    codes = {display.role_label_key(role) for role in (*STEM_ROLES, INSTRUMENTAL_ROLE, "original")}

    assert _missing(frozenset(codes), _catalog()) == []


def test_stage_and_detail_codes_are_distinct_and_non_empty() -> None:
    assert all(progress.STAGE_CODES)
    assert all(progress.DETAIL_CODES)
    assert len(progress.STAGE_CODES) == len(set(progress.STAGE_CODES))
    assert len(progress.DETAIL_CODES) == len(set(progress.DETAIL_CODES))
    assert not progress.STAGE_CODES.intersection(progress.DETAIL_CODES)


def test_every_stage_and_detail_constant_is_registered() -> None:
    values = {
        value
        for name, value in vars(progress).items()
        if (name.startswith("STAGE_") or name.startswith("DETAIL_")) and isinstance(value, str)
    }

    assert values == set(progress.STAGE_CODES | progress.DETAIL_CODES)


def test_coded_text_keeps_english_text_with_its_code_and_context() -> None:
    text = progress.CodedText(
        "Saving",
        code=progress.STAGE_IMPORT_SAVING,
        context={"part": 1},
    )

    assert text == "Saving"
    assert text.text == "Saving"
    assert text.code == progress.STAGE_IMPORT_SAVING
    assert dict(text.context) == {"part": 1}


def test_context_progress_forwards_the_clamped_fraction_and_coded_detail() -> None:
    updates: list[tuple[float, dict[str, object]]] = []

    class Sink:
        def progress(self, fraction: float, **values: object) -> None:
            updates.append((fraction, values))

    emit = progress.context_progress(Sink(), {"Encoding": progress.STAGE_EXPORT_ENCODING})
    emit(
        1.4,
        "Encoding",
        progress.CodedText(
            "writing the file",
            code=progress.DETAIL_EXPORT_WRITING_FILE,
            context={"name": "song.wav"},
        ),
    )

    fraction, values = updates[0]
    assert fraction == 1.0
    assert values["stage"] == "Encoding"
    assert values["stage_code"] == progress.STAGE_EXPORT_ENCODING
    assert values["detail"] == "writing the file"
    assert values["detail_code"] == progress.DETAIL_EXPORT_WRITING_FILE
    assert dict(values["detail_context"]) == {"name": "song.wav"}


def test_source_literals_for_stage_and_detail_codes_are_registered() -> None:
    src = Path(__file__).resolve().parents[2] / "src" / "stemforge"
    known = progress.STAGE_CODES | progress.DETAIL_CODES | display.DISPLAY_CODES
    violations: list[str] = []
    for path in src.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg not in {"stage_code", "detail_code"}:
                    continue
                value = keyword.value
                if (
                    isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                    and value.value not in known
                ):
                    violations.append(f"{path}:{node.lineno}: {value.value!r}")

    assert not violations, "A stage or detail code is missing from its registry:\n" + "\n".join(
        violations
    )
