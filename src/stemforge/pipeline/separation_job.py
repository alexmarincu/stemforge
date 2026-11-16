"""Separation Job: engine, validation, gain, previews, manifest, promotion (ticket 4).

Wiring point for ticket 12: before starting the worker, the Separation Job must
call ``ModelStore.verify_before_use()`` so the weights are re-hashed for every
Separation. A missing or mismatched cache raises ``separation.model_missing``
(``reason=absent|checksum``) and the UI shows the model gate instead of running.
"""

from __future__ import annotations
