"""PyInstaller runtime hook: assert the pre-import environment is in place.

``NICEGUI_STORAGE_PATH`` and ``HF_HOME`` must be set before the first NiceGUI or
Demucs import. ``main()`` calls
``platform.environment.configure()`` for that, which necessarily happens *after*
this hook runs, so the hook installs an import guard: the first ``nicegui`` or
``demucs`` import without both variables raises instead of letting either
package write a cache beside the executable.
"""

import os
import sys

REQUIRED_ENV_VARS = ("NICEGUI_STORAGE_PATH", "HF_HOME")
GUARDED_MODULES = ("nicegui", "demucs")


class _PreImportEnvironmentGuard:
    """A meta-path finder that checks the environment when a heavy package starts."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".", 1)[0] not in GUARDED_MODULES:
            return None
        missing = [name for name in REQUIRED_ENV_VARS if not os.environ.get(name)]
        if missing:
            raise RuntimeError(
                "StemForge frozen build: "
                + " and ".join(missing)
                + " must be set before importing "
                + fullname
                + " (platform.environment.configure() did not run first)"
            )
        return None


sys.meta_path.insert(0, _PreImportEnvironmentGuard())
# ``--self-test`` reports this marker so the bundle proves the hook ran.
sys.stemforge_frozen_env_guard = True
