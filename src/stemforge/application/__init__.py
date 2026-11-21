"""The application layer's public seam: one facade and the types the UI reads.

The UI imports only ``WorkspaceService`` and the view types from this package
(ADR 0013). Feature collaborators live under
:mod:`stemforge.application.collaborators` and are not part of the UI contract.
"""

from __future__ import annotations

from stemforge.engines.media import ExportFormat as ExportFormat
from stemforge.pipeline.export_job import ExportTarget as ExportTarget

from .service import (
    SEPARATION_OUTPUT_ROLES,
    ExportedFile,
    JobKindValue,
    JobView,
    LicenceFiles,
    PitchSet,
    PreflightCheckView,
    PreflightView,
    PreviewTarget,
    RemoveOutcome,
    SeparationSet,
    ServiceEngines,
    WorkspaceService,
)

__all__ = [
    "SEPARATION_OUTPUT_ROLES",
    "ExportedFile",
    "ExportFormat",
    "ExportTarget",
    "JobKindValue",
    "JobView",
    "LicenceFiles",
    "PitchSet",
    "PreflightCheckView",
    "PreflightView",
    "PreviewTarget",
    "RemoveOutcome",
    "SeparationSet",
    "ServiceEngines",
    "WorkspaceService",
]
