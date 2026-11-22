"""The model-weights feature the UI calls through ``WorkspaceService``."""

from __future__ import annotations

from collections.abc import Mapping

from stemforge.application.state import JobLedger
from stemforge.domain.models import JobKind, ModelConsent
from stemforge.jobs.job import Job, JobContext, JobRun
from stemforge.models import ModelSpec, ModelStatus, ModelStore
from stemforge.pipeline.model_job import (
    ModelInstallJob,
    Transport,
    consent_is_current,
    require_model_consent,
)
from stemforge.settings import SettingsStore


class ModelService:
    """Owns the pinned model's identity, consent gate, and install Job."""

    def __init__(
        self,
        settings: SettingsStore,
        models: ModelStore,
        ledger: JobLedger,
        transport: Transport | None,
    ) -> None:
        self._settings = settings
        self._models = models
        self._ledger = ledger
        self._transport = transport

    def model_spec(self) -> ModelSpec:
        """The pinned model identity and licence-notice version."""
        return self._models.spec

    def model_status(self) -> ModelStatus:
        """A cheap installed-or-not check; the Job/verify seam re-hashes before use."""
        return self._models.status()

    def model_consent(self) -> ModelConsent | None:
        return self._settings.model_consent()

    def model_consent_is_current(self) -> bool:
        return consent_is_current(
            self._settings.model_consent(),
            required_version=self._models.spec.licence_notice_version,
        )

    def accept_model_licence(self) -> ModelConsent:
        """Persist the explicit acknowledgement required before downloading."""
        return self._settings.accept_model_licence(version=self._models.spec.licence_notice_version)

    def install_model(
        self,
        *,
        accept_licence: bool = False,
        transport: Transport | None = None,
    ) -> str:
        """Submit a model-install Job and return its id.

        Without a current consent record this raises before submitting, so the
        UI can show the model gate without a failed Job in the history.
        """
        if accept_licence:
            self.accept_model_licence()
        if not self._models.is_installed():
            require_model_consent(
                self._settings.model_consent(),
                required_version=self._models.spec.licence_notice_version,
            )
        spec = self._models.spec
        job = Job(
            kind=JobKind.MODEL_INSTALL,
            identity=(JobKind.MODEL_INSTALL.value, spec.name, spec.revision),
            title=spec.name,
            run=self._install_run(self._transport if transport is None else transport),
        )
        return self._ledger.submit(job)

    def _install_run(self, transport: Transport | None) -> JobRun:
        def run(ctx: JobContext) -> Mapping[str, str]:
            job = ModelInstallJob(
                store=self._models,
                consent=self._settings.model_consent(),
                transport=transport,
            )
            result = job.run(ctx)
            return {
                "model": result.status.name,
                "sha256": result.status.sha256 or "",
                "path": str(result.status.path),
            }

        return run
