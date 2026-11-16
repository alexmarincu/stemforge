"""``domain.cancellation``: the cooperative cancel flag engines check."""

from __future__ import annotations

import pytest

from stemforge.domain.cancellation import CancelToken
from stemforge.errors import JobCancelled

pytestmark = pytest.mark.unit


def test_a_fresh_token_is_not_cancelled() -> None:
    token = CancelToken()

    assert token.cancelled is False
    token.check()


def test_cancel_is_idempotent_and_raises_at_the_checkpoint() -> None:
    token = CancelToken()

    token.cancel()
    token.cancel()

    assert token.cancelled is True
    with pytest.raises(JobCancelled):
        token.check()


def test_the_job_module_reexports_the_documented_type() -> None:
    from stemforge.jobs.job import CancelToken as DocumentedToken

    assert DocumentedToken is CancelToken
