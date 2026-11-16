#!/usr/bin/env bash
# Build the Linux frozen spine: PyInstaller onedir plus the Bundle root.
# Ticket 10 §1–§3. Ticket 20 adds vendor binaries, verify_bundle.py, and packaging.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
cd "$repo_root"

dist="$repo_root/dist/StemForge"
work="$repo_root/build/pyinstaller"

uv sync --frozen --group build

# Ticket 10 §1: a PyPI torch fallback silently pulls CUDA wheels. torch is not a
# dependency yet (tickets 12/13 add it), so assert the pin only when present.
.venv/bin/python - <<'PY'
import importlib.util
import sys

if importlib.util.find_spec("torch") is not None:
    import torch

    if not torch.__version__.endswith("+cpu"):
        sys.exit(f"build.sh: torch {torch.__version__} is not a +cpu build")
PY

rm -rf "$dist" "$work"
.venv/bin/python -m PyInstaller \
    packaging/pyinstaller/stemforge_linux.spec \
    --noconfirm \
    --clean \
    --distpath "$(dirname "$dist")" \
    --workpath "$work"

# The Bundle root: everything beside _internal/ that ADR 0010 keeps outside the
# frozen payload. Ticket 20 replaces the README with packaging/vendor.sh output.
mkdir -p "$dist/vendor"
cat > "$dist/vendor/README.txt" <<'EOF'
Vendored binaries live here, beside _internal/ (ADR 0010).
packaging/vendor.sh places ffmpeg/, deno/, and rubberband/ in this directory.
This skeleton build ships the directory and no binaries.
EOF

install -m 0755 packaging/linux/run.sh "$dist/run.sh"

for name in LICENSE THIRD_PARTY_NOTICES.md SOURCE_OFFER.md; do
    if [[ -f "$repo_root/$name" ]]; then
        install -m 0644 "$repo_root/$name" "$dist/$name"
    fi
done
if [[ -d "$repo_root/LICENSES" ]]; then
    cp -R "$repo_root/LICENSES" "$dist/LICENSES"
fi

test -x "$dist/StemForge"
test -d "$dist/_internal"
test -f "$dist/_internal/assets/stemforge.ico"
test -f "$dist/_internal/models/models.lock.json"
test -d "$dist/vendor"
test -x "$dist/run.sh"

echo
echo "Bundle root: $dist"
du -sh "$dist"
(cd "$dist" && find . -maxdepth 3 | sort)
