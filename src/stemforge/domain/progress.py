"""Stable Job progress values: English log text plus render-time codes.

Every stage and detail code is a semantic message ID in the English catalogue.
The English text stays alongside it for logs, the headless CLI, and the UI's
fallback when a code has no copy. Context carries the values a template needs.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Protocol

__all__ = [
    "DETAIL_CODES",
    "DETAIL_EXPORT_CHECKING_DESTINATION",
    "DETAIL_EXPORT_VERIFYING_TAGS",
    "DETAIL_EXPORT_WRITING_FILE",
    "DETAIL_IMPORT_NATIVE_FILE",
    "DETAIL_MODEL_ALREADY_INSTALLED",
    "DETAIL_MODEL_DOWNLOAD_PROGRESS",
    "DETAIL_PITCH_CACHED_RENDER",
    "DETAIL_PITCH_MEASURING_PEAK",
    "DETAIL_PITCH_ORIGINAL",
    "DETAIL_PITCH_SHIFT_STEM",
    "DETAIL_PITCH_SUMMING_NON_VOCAL",
    "DETAIL_PITCH_WRITING_MANIFEST",
    "DETAIL_SAVE_ORIGINAL_CHECKING_DESTINATION",
    "DETAIL_SAVE_ORIGINAL_COPYING_FILE",
    "DETAIL_SAVE_ORIGINAL_FINAL_NAME",
    "DETAIL_SAVE_ORIGINAL_WRITING_FILE",
    "DETAIL_SEPARATION_CHECKING_WEIGHTS",
    "DETAIL_SEPARATION_MEASURING_PEAK",
    "DETAIL_SEPARATION_OUTPUTS_VALIDATED",
    "DETAIL_SEPARATION_SECONDS",
    "DETAIL_SEPARATION_SEGMENT",
    "DETAIL_SEPARATION_STARTING_WORKER",
    "DETAIL_SEPARATION_WRITING_MANIFEST",
    "DETAIL_YOUTUBE_DOWNLOAD_COMPLETE",
    "DETAIL_YOUTUBE_DOWNLOAD_FINISHED",
    "DETAIL_YOUTUBE_DOWNLOAD_PROGRESS",
    "DETAIL_YOUTUBE_DOWNLOAD_PROGRESS_NO_SPEED",
    "DETAIL_YOUTUBE_DOWNLOAD_PROGRESS_NO_TOTAL",
    "DETAIL_YOUTUBE_DOWNLOAD_PROGRESS_NO_TOTAL_SPEED",
    "DETAIL_YOUTUBE_ENCODING_PREVIEW",
    "DETAIL_YOUTUBE_METADATA_RESOLVED",
    "DETAIL_YOUTUBE_OPENING_CACHED",
    "DETAIL_YOUTUBE_REPAIRING_CACHED",
    "DETAIL_YOUTUBE_RESOLVING_METADATA",
    "DETAIL_YOUTUBE_TRACK_ADDED",
    "DETAIL_YOUTUBE_WAITING",
    "DETAIL_YOUTUBE_WRITING_MANIFEST",
    "STAGE_CODES",
    "STAGE_EXPORT_BATCH_ITEM",
    "STAGE_EXPORT_ENCODING",
    "STAGE_EXPORT_TAGGING",
    "STAGE_EXPORT_WRITING",
    "STAGE_IMPORT_DECODING",
    "STAGE_IMPORT_PROBING",
    "STAGE_IMPORT_READING_METADATA",
    "STAGE_IMPORT_SAVING",
    "STAGE_MODEL_CONSENT",
    "STAGE_MODEL_DOWNLOAD",
    "STAGE_MODEL_INSTALL",
    "STAGE_MODEL_VERIFY",
    "STAGE_PITCH_DONE",
    "STAGE_PITCH_GAIN",
    "STAGE_PITCH_INSTRUMENTAL",
    "STAGE_PITCH_PREVIEWS",
    "STAGE_PITCH_PROCESSING",
    "STAGE_PITCH_PROFILE",
    "STAGE_PITCH_SAVING",
    "STAGE_PITCH_STUDYING",
    "STAGE_PITCH_VALIDATING",
    "STAGE_SAVE_ORIGINAL_COPYING",
    "STAGE_SAVE_ORIGINAL_WRITING",
    "STAGE_SEPARATION_DONE",
    "STAGE_SEPARATION_GAIN",
    "STAGE_SEPARATION_PREVIEWS",
    "STAGE_SEPARATION_SAVING",
    "STAGE_SEPARATION_SEPARATE",
    "STAGE_SEPARATION_VALIDATE",
    "STAGE_SEPARATION_VERIFY",
    "STAGE_YOUTUBE_CHECKING",
    "STAGE_YOUTUBE_DECODING",
    "STAGE_YOUTUBE_DOWNLOADING",
    "STAGE_YOUTUBE_FINISHING",
    "CodedText",
    "Progress",
    "context_progress",
]

STAGE_IMPORT_PROBING: Final = "job.stage.import.probing"
STAGE_IMPORT_DECODING: Final = "job.stage.import.decoding"
STAGE_IMPORT_READING_METADATA: Final = "job.stage.import.reading_metadata"
STAGE_IMPORT_SAVING: Final = "job.stage.import.saving"

STAGE_YOUTUBE_CHECKING: Final = "job.stage.youtube.checking"
STAGE_YOUTUBE_DOWNLOADING: Final = "job.stage.youtube.downloading"
STAGE_YOUTUBE_DECODING: Final = "job.stage.youtube.decoding"
STAGE_YOUTUBE_FINISHING: Final = "job.stage.youtube.finishing"

STAGE_SEPARATION_VERIFY: Final = "job.stage.separation.verify"
STAGE_SEPARATION_SEPARATE: Final = "job.stage.separation.separate"
STAGE_SEPARATION_VALIDATE: Final = "job.stage.separation.validate"
STAGE_SEPARATION_GAIN: Final = "job.stage.separation.gain"
STAGE_SEPARATION_PREVIEWS: Final = "job.stage.separation.previews"
STAGE_SEPARATION_SAVING: Final = "job.stage.separation.saving"
STAGE_SEPARATION_DONE: Final = "job.stage.separation.done"

STAGE_PITCH_STUDYING: Final = "job.stage.pitch.studying"
STAGE_PITCH_PROFILE: Final = "job.stage.pitch.profile"
STAGE_PITCH_PROCESSING: Final = "job.stage.pitch.processing"
STAGE_PITCH_VALIDATING: Final = "job.stage.pitch.validating"
STAGE_PITCH_INSTRUMENTAL: Final = "job.stage.pitch.instrumental"
STAGE_PITCH_GAIN: Final = "job.stage.pitch.gain"
STAGE_PITCH_PREVIEWS: Final = "job.stage.pitch.previews"
STAGE_PITCH_SAVING: Final = "job.stage.pitch.saving"
STAGE_PITCH_DONE: Final = "job.stage.pitch.done"

STAGE_EXPORT_ENCODING: Final = "job.stage.export.encoding"
STAGE_EXPORT_TAGGING: Final = "job.stage.export.tagging"
STAGE_EXPORT_WRITING: Final = "job.stage.export.writing"

STAGE_SAVE_ORIGINAL_COPYING: Final = "job.stage.save_original.copying"
STAGE_SAVE_ORIGINAL_WRITING: Final = "job.stage.save_original.writing"

STAGE_MODEL_CONSENT: Final = "job.stage.model.consent"
STAGE_MODEL_DOWNLOAD: Final = "job.stage.model.download"
STAGE_MODEL_VERIFY: Final = "job.stage.model.verify"
STAGE_MODEL_INSTALL: Final = "job.stage.model.install"

STAGE_EXPORT_BATCH_ITEM: Final = "job.stage.export_batch.item"

STAGE_CODES: Final[frozenset[str]] = frozenset(
    {
        STAGE_IMPORT_PROBING,
        STAGE_IMPORT_DECODING,
        STAGE_IMPORT_READING_METADATA,
        STAGE_IMPORT_SAVING,
        STAGE_YOUTUBE_CHECKING,
        STAGE_YOUTUBE_DOWNLOADING,
        STAGE_YOUTUBE_DECODING,
        STAGE_YOUTUBE_FINISHING,
        STAGE_SEPARATION_VERIFY,
        STAGE_SEPARATION_SEPARATE,
        STAGE_SEPARATION_VALIDATE,
        STAGE_SEPARATION_GAIN,
        STAGE_SEPARATION_PREVIEWS,
        STAGE_SEPARATION_SAVING,
        STAGE_SEPARATION_DONE,
        STAGE_PITCH_STUDYING,
        STAGE_PITCH_PROFILE,
        STAGE_PITCH_PROCESSING,
        STAGE_PITCH_VALIDATING,
        STAGE_PITCH_INSTRUMENTAL,
        STAGE_PITCH_GAIN,
        STAGE_PITCH_PREVIEWS,
        STAGE_PITCH_SAVING,
        STAGE_PITCH_DONE,
        STAGE_EXPORT_ENCODING,
        STAGE_EXPORT_TAGGING,
        STAGE_EXPORT_WRITING,
        STAGE_SAVE_ORIGINAL_COPYING,
        STAGE_SAVE_ORIGINAL_WRITING,
        STAGE_MODEL_CONSENT,
        STAGE_MODEL_DOWNLOAD,
        STAGE_MODEL_VERIFY,
        STAGE_MODEL_INSTALL,
        STAGE_EXPORT_BATCH_ITEM,
    }
)

DETAIL_IMPORT_NATIVE_FILE: Final = "job.detail.import.native_file"

DETAIL_YOUTUBE_RESOLVING_METADATA: Final = "job.detail.youtube.resolving_metadata"
DETAIL_YOUTUBE_METADATA_RESOLVED: Final = "job.detail.youtube.metadata_resolved"
DETAIL_YOUTUBE_DOWNLOAD_COMPLETE: Final = "job.detail.youtube.download_complete"
DETAIL_YOUTUBE_DOWNLOAD_FINISHED: Final = "job.detail.youtube.download_finished"
DETAIL_YOUTUBE_DOWNLOAD_PROGRESS: Final = "job.detail.youtube.download_progress"
DETAIL_YOUTUBE_DOWNLOAD_PROGRESS_NO_SPEED: Final = "job.detail.youtube.download_progress_no_speed"
DETAIL_YOUTUBE_DOWNLOAD_PROGRESS_NO_TOTAL: Final = "job.detail.youtube.download_progress_no_total"
DETAIL_YOUTUBE_DOWNLOAD_PROGRESS_NO_TOTAL_SPEED: Final = (
    "job.detail.youtube.download_progress_no_total_speed"
)
DETAIL_YOUTUBE_WAITING: Final = "job.detail.youtube.waiting"
DETAIL_YOUTUBE_ENCODING_PREVIEW: Final = "job.detail.youtube.encoding_preview"
DETAIL_YOUTUBE_WRITING_MANIFEST: Final = "job.detail.youtube.writing_manifest"
DETAIL_YOUTUBE_TRACK_ADDED: Final = "job.detail.youtube.track_added"
DETAIL_YOUTUBE_OPENING_CACHED: Final = "job.detail.youtube.opening_cached"
DETAIL_YOUTUBE_REPAIRING_CACHED: Final = "job.detail.youtube.repairing_cached"

DETAIL_SEPARATION_CHECKING_WEIGHTS: Final = "job.detail.separation.checking_weights"
DETAIL_SEPARATION_STARTING_WORKER: Final = "job.detail.separation.starting_worker"
DETAIL_SEPARATION_OUTPUTS_VALIDATED: Final = "job.detail.separation.outputs_validated"
DETAIL_SEPARATION_MEASURING_PEAK: Final = "job.detail.separation.measuring_peak"
DETAIL_SEPARATION_WRITING_MANIFEST: Final = "job.detail.separation.writing_manifest"
DETAIL_SEPARATION_SEGMENT: Final = "job.detail.separation.segment"
DETAIL_SEPARATION_SECONDS: Final = "job.detail.separation.seconds"

DETAIL_PITCH_SHIFT_STEM: Final = "job.detail.pitch.shift_stem"
DETAIL_PITCH_ORIGINAL: Final = "job.detail.pitch.original"
DETAIL_PITCH_SUMMING_NON_VOCAL: Final = "job.detail.pitch.summing_non_vocal"
DETAIL_PITCH_CACHED_RENDER: Final = "job.detail.pitch.cached_render"
DETAIL_PITCH_MEASURING_PEAK: Final = "job.detail.pitch.measuring_peak"
DETAIL_PITCH_WRITING_MANIFEST: Final = "job.detail.pitch.writing_manifest"

DETAIL_EXPORT_CHECKING_DESTINATION: Final = "job.detail.export.checking_destination"
DETAIL_EXPORT_VERIFYING_TAGS: Final = "job.detail.export.verifying_tags"
DETAIL_EXPORT_WRITING_FILE: Final = "job.detail.export.writing_file"

DETAIL_SAVE_ORIGINAL_CHECKING_DESTINATION: Final = "job.detail.save_original.checking_destination"
DETAIL_SAVE_ORIGINAL_COPYING_FILE: Final = "job.detail.save_original.copying_file"
DETAIL_SAVE_ORIGINAL_WRITING_FILE: Final = "job.detail.save_original.writing_file"
DETAIL_SAVE_ORIGINAL_FINAL_NAME: Final = "job.detail.save_original.final_name"

DETAIL_MODEL_ALREADY_INSTALLED: Final = "job.detail.model.already_installed"
DETAIL_MODEL_DOWNLOAD_PROGRESS: Final = "job.detail.model.download_progress"

DETAIL_CODES: Final[frozenset[str]] = frozenset(
    {
        DETAIL_IMPORT_NATIVE_FILE,
        DETAIL_YOUTUBE_RESOLVING_METADATA,
        DETAIL_YOUTUBE_METADATA_RESOLVED,
        DETAIL_YOUTUBE_DOWNLOAD_COMPLETE,
        DETAIL_YOUTUBE_DOWNLOAD_FINISHED,
        DETAIL_YOUTUBE_DOWNLOAD_PROGRESS,
        DETAIL_YOUTUBE_DOWNLOAD_PROGRESS_NO_SPEED,
        DETAIL_YOUTUBE_DOWNLOAD_PROGRESS_NO_TOTAL,
        DETAIL_YOUTUBE_DOWNLOAD_PROGRESS_NO_TOTAL_SPEED,
        DETAIL_YOUTUBE_WAITING,
        DETAIL_YOUTUBE_ENCODING_PREVIEW,
        DETAIL_YOUTUBE_WRITING_MANIFEST,
        DETAIL_YOUTUBE_TRACK_ADDED,
        DETAIL_YOUTUBE_OPENING_CACHED,
        DETAIL_YOUTUBE_REPAIRING_CACHED,
        DETAIL_SEPARATION_CHECKING_WEIGHTS,
        DETAIL_SEPARATION_STARTING_WORKER,
        DETAIL_SEPARATION_OUTPUTS_VALIDATED,
        DETAIL_SEPARATION_MEASURING_PEAK,
        DETAIL_SEPARATION_WRITING_MANIFEST,
        DETAIL_SEPARATION_SEGMENT,
        DETAIL_SEPARATION_SECONDS,
        DETAIL_PITCH_SHIFT_STEM,
        DETAIL_PITCH_ORIGINAL,
        DETAIL_PITCH_SUMMING_NON_VOCAL,
        DETAIL_PITCH_CACHED_RENDER,
        DETAIL_PITCH_MEASURING_PEAK,
        DETAIL_PITCH_WRITING_MANIFEST,
        DETAIL_EXPORT_CHECKING_DESTINATION,
        DETAIL_EXPORT_VERIFYING_TAGS,
        DETAIL_EXPORT_WRITING_FILE,
        DETAIL_SAVE_ORIGINAL_CHECKING_DESTINATION,
        DETAIL_SAVE_ORIGINAL_COPYING_FILE,
        DETAIL_SAVE_ORIGINAL_WRITING_FILE,
        DETAIL_SAVE_ORIGINAL_FINAL_NAME,
        DETAIL_MODEL_ALREADY_INSTALLED,
        DETAIL_MODEL_DOWNLOAD_PROGRESS,
    }
)


class CodedText(str):
    """English text carrying its stable catalogue code and template context.

    The value is a ``str`` so logs and every existing ``(fraction, stage, detail)``
    sink keep the exact English text. The Job seam reads ``code`` and ``context``
    so a renderer can resolve the same text through the catalogue; ``text`` is
    the explicit spelling for callers that do not want the string subclass.
    """

    code: str | None
    context: Mapping[str, object]

    def __new__(
        cls,
        text: str,
        *,
        code: str | None = None,
        context: Mapping[str, object] | None = None,
    ) -> CodedText:
        coded = super().__new__(cls, text)
        coded.code = code
        coded.context = MappingProxyType(dict(context or {}))
        return coded

    @property
    def text(self) -> str:
        """The English text as a plain string."""
        return str(self)


@dataclass(frozen=True, slots=True)
class Progress:
    """One progress publication: a fraction, coded stage/detail, and render keys."""

    fraction: float
    stage: CodedText | None = None
    detail: CodedText | None = None

    def __post_init__(self) -> None:
        """Wrap plain strings so a direct publisher keeps the value type's contract."""
        if self.stage is not None and not isinstance(self.stage, CodedText):
            object.__setattr__(self, "stage", CodedText(self.stage))
        if self.detail is not None and not isinstance(self.detail, CodedText):
            object.__setattr__(self, "detail", CodedText(self.detail))

    @property
    def stage_code(self) -> str | None:
        """The stage's stable code, if its publisher supplied one."""
        return self.stage.code if self.stage is not None else None

    @property
    def stage_context(self) -> Mapping[str, object]:
        return self.stage.context if self.stage is not None else MappingProxyType({})

    @property
    def detail_code(self) -> str | None:
        """The detail's stable code, if its publisher supplied one."""
        return self.detail.code if self.detail is not None else None

    @property
    def detail_context(self) -> Mapping[str, object]:
        return self.detail.context if self.detail is not None else MappingProxyType({})


ProgressSink = Callable[[float, str, str], None]


class ProgressPublisher(Protocol):
    """The Job-context seam a pipeline adapter publishes through."""

    def progress(
        self,
        fraction: float,
        *,
        stage: str | None = None,
        stage_code: str | None = None,
        stage_context: Mapping[str, object] | None = None,
        detail: str | None = None,
        detail_code: str | None = None,
        detail_context: Mapping[str, object] | None = None,
    ) -> None: ...


def context_progress(publisher: ProgressPublisher, stage_codes: Mapping[str, str]) -> ProgressSink:
    """Adapt a Job context to the engines' ``(fraction, stage, detail)`` callback."""

    def sink(fraction: float, stage: str, detail: str) -> None:
        coded = detail if isinstance(detail, CodedText) else None
        publisher.progress(
            min(1.0, max(0.0, fraction)),
            stage=stage,
            stage_code=stage_codes.get(stage),
            detail=detail or None,
            detail_code=coded.code if coded is not None else None,
            detail_context=coded.context if coded is not None else None,
        )

    return sink
