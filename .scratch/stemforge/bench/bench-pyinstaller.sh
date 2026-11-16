#!/usr/bin/env bash
# Frozen-bundle size and startup benchmark. Run only after CPU benchmarks:
# PyInstaller's analysis is single-threaded but competes for the CPU.
set -uo pipefail
cd "$(dirname "$0")"

uv pip install --python .venv/bin/python "pyinstaller==6.22.3"

.venv/bin/python measure.py pyinstaller-build -- \
  .venv/bin/pyinstaller --noconfirm --onedir --name sf-stub \
  --collect-data demucs \
  --hidden-import demucs.htdemucs --hidden-import demucs.hdemucs \
  --hidden-import demucs.repo --hidden-import demucs.hf \
  worker.py 2>/dev/null | tee -a results.jsonl

du -sb dist/sf-stub > dist-size-bytes.txt
find dist/sf-stub -type f | wc -l > dist-file-count.txt

# Startup: run the frozen stub three times; first run is warm-disk, not cold.
for i in 1 2 3; do
  .venv/bin/python measure.py "frozen-startup-$i" -- dist/sf-stub/sf-stub 2>/dev/null | tee -a results.jsonl
done

# Trimmed variant for the measured torch/test + torch/bin saving.
du -sb dist/sf-stub/_internal/torch/test dist/sf-stub/_internal/torch/bin 2>/dev/null > trim-bytes.txt
tar -C dist -cf - sf-stub | zstd -19 -T4 -o dist/sf-stub.tar.zst -f
du -sb dist/sf-stub.tar.zst > dist-tar-size-bytes.txt
