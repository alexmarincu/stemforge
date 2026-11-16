# Separation engine research — StemForge v0.1.0

Status: draft for planning · Researched 2026-11-14 · All version numbers verified against PyPI/GitHub/Hugging Face on that date.
Scope: locally runnable Python stem-separation stack for a GPL-3.0 desktop app on Linux and Windows x86_64, CPU-first with optional GPU.
App context (from `.scratch/stemforge/map.md`): StemForge v0.1.0 is a **NiceGUI/Python** desktop app; canonical internal audio is **32-bit float WAV**; four stems are produced, with vocals/instrumental as the primary UI outputs; distribution is a Linux AppImage/tar.gz and a Windows portable ZIP/Inno Setup installer.

All measurements labelled **[measured]** were taken on the research machine:

- Intel Core i7-4800MQ (4 physical cores / 8 threads, Haswell, 2.7 GHz), 23 GB RAM, Linux, no GPU.
- `torch 2.15.0+cpu` from `download.pytorch.org/whl/cpu`, `demucs 4.1.0`, CPython 3.13.18.
- Timing runs were executed while other research agents shared the same 8 vCPUs, so CPU numbers are **conservative** (≈2.2–2.4 cores of effective utilisation observed).

---

## TL;DR recommendation

**Engine:** `demucs==4.1.0` with the `htdemucs` bag (single model), run inside a **subprocess worker** that links against `torch==2.15.0+cpu`, on **CPython 3.13**, with `numpy` pinned explicitly.

- Quality is the best of the locally runnable options (9.00 dB overall SDR on MUSDB HQ, 9.20 dB with fine-tuning + sparse attention), versus ~7.5 dB for MDX-Net and ~5.9 dB for Spleeter ([Demucs README](https://github.com/adefossez/demucs#comparison-with-accuracy), [HT Demucs paper](https://arxiv.org/abs/2211.08553)).
- Model download is 84 MB (single safetensors file), versus 336 MB for `htdemucs_ft`.
- CPU separation measured at **RTF 1.5–1.8×** on a 2014-era 4-core laptop (4-minute track: 6 min 10 s of compute, peak RSS **1.8 GB**).
- The library exposes a first-class progress **callback** with segment offsets — no need to scrape `tqdm`.
- Code is MIT. **The weights' licence is explicitly unresolved by the author** (see §1.4) — this is the single biggest shipping risk and must be a deliberate product/legal decision, not an accident.

**Packaging:** PyInstaller 6.22.3 `--onedir` of a minimal worker produced **881 MB on disk / 197 MB compressed (zstd -19)** in 3 min 12 s, and the frozen worker ran as fast as the venv (8.6 s for a 5 s clip, 1.06 GB RSS). Avoid `--onefile` (3 GB reports, multi-second extraction on every launch). A private CPython environment (974 MB / ≈220 MB compressed) is the lower-risk alternative if StemForge already ships its own Python runtime.

---

## 1. Demucs 4.1.0

### 1.1 Maintenance status

| Fact | Value | Source |
|---|---|---|
| Package | `demucs 4.1.0`, wheel `py3-none-any`, sdist 1.24 MB | [PyPI JSON](https://pypi.org/pypi/demucs/json) |
| Uploaded | 2026-07-11 | PyPI |
| Canonical repo | `github.com/adefossez/demucs`; the old `facebookresearch/demucs` is **archived** (last push 2024-04-24) | [README](https://github.com/adefossez/demucs), GitHub API |
| Licence (code) | MIT | [LICENSE](https://github.com/adefossez/demucs/blob/main/LICENSE) |
| Stars / open issues | 3,545 / 6 | GitHub API, 2026-11-14 |
| Last commit | 2026-10-26 (`829d218`, "adding clarification on license") | GitHub API |
| Tag | `v4.1.0` only; no GitHub Releases | GitHub API |
| Maintainer statement | "This is the officially maintained Demucs … Note that I'm not actively working on Demucs anymore, so expect slow replies and no new feature for now." | PyPI description / README |
| CI | one job, Python **3.10** only, `uv sync --extra train`, `make test_eval` | [.github/workflows/tests.yml](https://github.com/adefossez/demucs/blob/main/.github/workflows/tests.yml) |

Assessment: **low-activity but not abandoned**. 4.1.0 is a real maintenance release (packaging overhaul, torch 2.6 compatibility, Hugging Face hosting, audit fixes — see [docs/release.md](https://github.com/adefossez/demucs/blob/main/docs/release.md)). Feature work has stopped; security fixes are unlikely to be fast. For a GPL desktop app that pins its own wheels, that is acceptable for v0.1.0, but the engine boundary should be designed so it can be swapped (see §3).

### 1.2 Python and platform support

`requires-python = ">=3.10"` ([pyproject.toml](https://github.com/adefossez/demucs/blob/main/pyproject.toml)). Actual ceiling is set by binary dependencies, not by demucs:

| Python | torch 2.15.0 wheels | sphn 0.2.1 wheels | Verdict |
|---|---|---|---|
| 3.10 | ✅ (cp310) | ✅ (cp310) | supported; demucs CI target |
| 3.11 | ✅ | ✅ | supported |
| 3.12 | ✅ | ✅ | supported |
| 3.13 | ✅ | ✅ | **recommended**; **[measured]** full install + inference works |
| 3.14 | ✅ (cp314, cp314t) | ✅ (cp314) | **[measured]** install + inference works (RTF 1.82 on a 5 s clip); ecosystem still catching up |
| 3.15 | ✅ (cp315, cp315t) | ❌ | not viable |

Wheel availability sources: [torch 2.15.0 files on PyPI](https://pypi.org/pypi/torch/2.15.0/json), [sphn 0.2.1 files](https://pypi.org/pypi/sphn/0.2.1/json).

**[measured]** Both 3.13 and 3.14 were installed from scratch with `torch==2.15.0+cpu` + `demucs==4.1.0` + `numpy`, and both completed a 5-second separation on CPU. 3.13 is recommended only because it has the longest wheel coverage across the whole dependency graph; 3.14 is a viable fallback if the app's own runtime moves first.

Platform notes:

- **Windows**: demucs docs say 64-bit only ("Demucs is not supported on 32bits systems"), install via `uvx demucs`, and CUDA requires pointing uv at the PyTorch CUDA index because the default Windows wheel is CPU-only ([docs/windows.md](https://github.com/adefossez/demucs/blob/main/docs/windows.md)).
- **Linux**: default PyPI torch wheel bundles CUDA; CPU-only requires `--index-url https://download.pytorch.org/whl/cpu` ([docs/linux.md](https://github.com/adefossez/demucs/blob/main/docs/linux.md)).
- **ffmpeg** is optional for wav/flac/mp3/ogg/aac decoding (sphn handles them) but **required for FLAC output** and exotic input formats (release notes 4.1.0; `docs/linux.md`).

### 1.3 PyTorch 2.15 wheels (CPU)

Torch 2.15.0 is the current release and requires **Python >= 3.11** ([PyPI](https://pypi.org/pypi/torch/2.15.0/json)). Sizes measured from the wheel headers:

| Platform | Wheel | Download | Installed |
|---|---|---|---|
| Linux x86_64, CPU-only | `torch-2.15.0+cpu-cp313-cp313-manylinux_2_28_x86_64.whl` | **209.5 MB** | **760 MB** (`site-packages/torch`) |
| Linux x86_64, default (CUDA) | `torch-2.15.0-cp313-cp313-manylinux_2_28_x86_64.whl` | 539.2 MB | ≈2.5 GB with `nvidia-*` deps (estimate) |
| Windows x86_64, CPU/default | `torch-2.15.0+cpu-cp313-cp313-win_amd64.whl` | **126.6 MB** | **484 MB**, 12,354 files |

**[measured]** clean install on Python 3.13: `torch 2.15.0+cpu` + demucs + deps = **974 MB** venv total, dominated by torch (760 MB), sphn (79 MB), numpy (59 MB incl. `numpy.libs`), sympy (29 MB), networkx (8 MB).

### 1.4 Models and weights

Since 4.1.0 weights are hosted on the Hugging Face Hub as safetensors, with a fallback to the legacy AWS bucket for single signatures ([demucs/hf.py](https://github.com/adefossez/demucs/blob/main/demucs/hf.py), [demucs/pretrained.py](https://github.com/adefossez/demucs/blob/main/demucs/pretrained.py)).

| Model | Repo | Files | Size | Notes |
|---|---|---|---|---|
| `htdemucs` (default) | [`adefossez/HTDemucs`](https://huggingface.co/adefossez/HTDemucs) | `htdemucs.yaml` (21 B), `955717e8.safetensors` | **84,025,440 B (80.1 MiB)** + 12 KB JSON sidecar | 1 model in bag, 4 stems |
| `htdemucs_ft` | [`adefossez/HTDemucs-ft`](https://huggingface.co/adefossez/HTDemucs-ft) | 4 × `*.safetensors` | **336 MB total** | "4 times more time", slightly better SDR |
| `htdemucs_6s` | [`adefossez/HTDemucs-6s`](https://huggingface.co/adefossez/HTDemucs-6s) | 1 × safetensors | 54,885,744 B | adds `guitar`, `piano`; piano is poor |

- **Download URL** (what `hf_hub_download` resolves): `https://huggingface.co/adefossez/HTDemucs/resolve/main/955717e8.safetensors` → 84,025,440 bytes (`x-linked-size`).
- Legacy URL still live: `https://dl.fbaipublicfiles.com/demucs/hybrid_transformer/955717e8-8726e21a.th` → 84,141,911 bytes (from [`demucs/remote/files.txt`](https://github.com/adefossez/demucs/blob/main/demucs/remote/files.txt)).
- **Pin the HF revision**: the repo's current commit is `cbc8a9b1a87023b7fd74e7b3412e6321c0eab003` (last modified 2026-08-31). `hf_hub_download` accepts `revision=`.
- **Cache path [measured]**: `~/.cache/huggingface/hub/models--adefossez--HTDemucs/snapshots/cbc8a9b1…/955717e8.safetensors`. Default cache is `~/.cache/huggingface/hub`, overridable with `HF_HOME` or `HF_HUB_CACHE` ([HF docs](https://huggingface.co/docs/huggingface_hub/guides/manage-cache)). The hub uses symlinks into `blobs/`; on Windows without symlink support it copies instead.
- **Offline**: pre-seed the cache at install time, then set `HF_HUB_OFFLINE=1` for runtime. Without a token the hub prints an unauthenticated-rate-limit warning on every cold start **[measured]**.

#### Licence of the weights — unresolved

The 2026-10-26 README commit adds:

> "Demucs code is released under the MIT license … **The license of the weights is not clear**: MusDB contains some data that were only usable for research purpose. I sadly cannot help granting a license … The 6 source variant of htdemucs was trained only on Meta internal dataset, which likely means CC-NC-BY license is applicable …"

([README §License](https://github.com/adefossez/demucs#license))

Implications for StemForge:

1. The **code** licence (MIT) is compatible with GPL-3.0.
2. The **weights** have no granted licence. Redistributing them inside a GPL-3.0 installer is a legal grey area, and the `htdemucs_6s` variant is additionally likely **non-commercial**.
3. Safest v0.1.0 posture: **do not bundle weights**; download them on first use with an explicit user action and an attribution/licence notice. This also cuts the installer by 84 MB. Get counsel before any other choice.
4. All Demucs-derived alternatives (demucs.cpp's ggml weights) inherit exactly the same problem.

### 1.5 Python API vs CLI

**CLI** (`demucs.separate:main`, console script `demucs`):

```
demucs -n htdemucs -d cpu --two-stems=vocals -o OUT --filename "{track}/{stem}.{ext}" input.wav
```

**[measured]** on a 5 s clip:

```
$ python -m demucs.separate -n htdemucs -d cpu --two-stems=vocals \
      -o cli_out --filename "{stem}.{ext}" track5.wav
Selected model is a bag of 1 models. You will see that many progress bars per track.
Separated tracks will be stored in /tmp/opencode/bench/cli_out/htdemucs
Separating track track5.wav
100%|████████████████| 5.85/5.85 [00:12<00:00,  2.07s/seconds]
$ find cli_out -type f
cli_out/htdemucs/no_vocals.wav
cli_out/htdemucs/vocals.wav
```

Human-readable text goes to **stdout**, the tqdm bar to **stderr**. `HF_HUB_OFFLINE=1` with a warm cache also worked (no network access).

- Default output root `separated/`, then a model-name subfolder: `separated/htdemucs/<track>/drums.wav|bass.wav|other.wav|vocals.wav`.
- `--two-stems=vocals` writes `vocals.wav` and `no_vocals.wav` (or `minus_vocals.wav` with `--other-method=minus`). **It is not faster or lighter**: demucs computes all four stems and mixes afterwards (README: "This will mix the files after separating the mix fully, so this won't be faster or use less memory").
- Output is 44.1 kHz stereo; default encoding int16 PCM, `--float32` / `--int24` / `--flac` / `--mp3` available. FLAC requires ffmpeg.
- Errors exit with status 1 via `utils.fatal()` ([utils.py](https://github.com/adefossez/demucs/blob/main/demucs/utils.py)).
- Progress is `tqdm` on **stderr** only; the CLI prints human text on stdout ("Separating track …"). Not a stable machine interface.

**Python API** (`demucs.api.Separator`):

```python
from demucs.api import Separator, save_audio

sep = Separator(model="htdemucs", device="cpu", shifts=1, overlap=0.25,
                split=True, segment=None, jobs=0, progress=False,
                callback=lambda d: print(d))
origin, stems = sep.separate_audio_file(Path("input.wav"))   # dict: name -> tensor
save_audio(stems["vocals"], "vocals.wav", samplerate=sep.samplerate)
```

- Documented and stable enough for integration ([docs/api.md](https://github.com/adefossez/demucs/blob/main/docs/api.md)).
- `separate_audio_file` decodes via `sphn.read` with an ffmpeg fallback ([api.py](https://github.com/adefossez/demucs/blob/main/demucs/api.py)).
- `update_parameter()` allows reuse of a loaded model across tracks, avoiding the ~2 s model load per track.
- `--two-stems` is **not** in the API: `Separator` always returns all stems. The caller mixes `no_vocals` itself (`sum` of the others, or `origin - vocals`).

### 1.6 Progress reporting (the good part)

`Separator(callback=...)` is called for every chunk with a dict containing ([api.py docstring](https://github.com/adefossez/demucs/blob/main/demucs/api.py)):

```
model_idx_in_bag, shift_idx, segment_offset, state ("start"|"end"), audio_length, models
```

**[measured]** on a 30 s clip (44.1 kHz → `audio_length=1_323_000`, segment 7.8 s, overlap 0.25):

```
{"t":2.104,"audio_length":1323000,"model_idx_in_bag":0,"shift_idx":0,"segment_offset":0,"models":1,"state":"start"}
{"t":9.798,"audio_length":1323000,...,"segment_offset":0,"state":"end"}
{"t":9.824,...,"segment_offset":257985,"state":"start"}
...
```

`segment_offset` is in **samples**, stride is `(1-overlap) * segment * samplerate` = 257,985 samples (5.85 s), and the chunk length is `segment * samplerate` = 343,980 samples (7.8 s). 6 segments for 30 s → 12 events.

Deriving a fraction is straightforward and exact enough for a progress bar:

```
fraction = clamp((segment_offset + segment_samples) / audio_length, 0, 1)   # on "end"
segments = ceil((audio_length - segment_samples) / stride) + 1
```

Caveat: `audio_length` is the length after resampling, and the last chunk may be shorter. Using `state=="end"` events avoids over-reporting. Raising `KeyboardInterrupt` inside the callback aborts and cancels pending chunks (documented; fixed in 4.1.0).

### 1.7 CPU runtime and RAM

**Vendor claim** (README): "With Demucs, processing time should be roughly equal to 1.5 times the duration of the track" on CPU. This is stale guidance from the pre-v4 era but happens to be close for `htdemucs`.

**[measured]**, `htdemucs`, `-d cpu`, shifts=1, overlap=0.25, segment 7.8 s, torch threads=4:

| Input | Wall time | RTF | Peak RSS |
|---|---|---|---|
| 30 s stereo 44.1 kHz | 52.7 s separation (+2.0 s model load) | **1.76×** | **1,138 MB** |
| 240 s (4 min) stereo 44.1 kHz | **369.8 s** separation (6 min 10 s), 4.3 s model load | **1.54×** | **1,812 MB** |

Notes:

- RTF is roughly linear in track length because chunk size is fixed; memory grows with the full-track tensor buffers (4 stems held in RAM before writing). A 4-minute track wrote 4 × 42.3 MB int16 WAVs (169 MB on disk).
- Peak RSS of **1.8 GB** for a 4-minute track is the planning number; recommend a 4 GB free-RAM precondition and a warning below that. Demucs-GUI likewise states "about at least 8 GB of total memory" for the whole app.
- Extrapolation for planning: on this 4-core Haswell a 4-minute track takes **6 min 10 s** (measured) and peaks at **1.8 GB RSS**. On a modern 8–16 core desktop expect **≈2–4 minutes** and similar memory.
- `--shifts N` multiplies time by N (default 1 in 4.1.0, good).
- `-j/--jobs` parallelises chunks and roughly multiplies RAM; with a 1.8 GB baseline for 4 minutes, `jobs=2` already needs 3+ GB. Keep `jobs=0` (sequential) for v0.1.0.
- **Chunking:** HT models are trained at 7.8 s and **cannot use a longer segment**; `--segment` can only shorten it (less RAM, lower quality). Keep the default. `overlap=0.25` is the vendor default and a reasonable quality/speed point; `0.1` is a safe speed trade on weak CPUs. `--no-split` is only for very short clips and blows memory.
- Model load is ~2 s warm; first run adds the 84 MB download.
- Torch picks 4 threads (= physical cores) automatically on this 8-thread machine; oversubscription is a real risk when the app also does audio I/O — set `OMP_NUM_THREADS` / `torch.set_num_threads()` explicitly.

### 1.8 Packaging: PyInstaller / Nuitka / embedded interpreter

**PyInstaller** has first-class torch support via `hook-torch.py` in `pyinstaller-hooks-contrib` (2026.10, shipped with PyInstaller 6.22.3): it collects torch data files, all submodules, dynamic libs, MKL DLLs on Windows, and suppresses Linux symlink traps ([hook-torch.py](https://github.com/pyinstaller/pyinstaller-hooks-contrib/blob/master/_pyinstaller_hooks_contrib/stdhooks/hook-torch.py)).

Known costs, from the PyInstaller tracker:

- **Size**: a torch inference project reports **5 GB onedir / 3 GB onefile** ([issue #9170](https://github.com/pyinstaller/pyinstaller/issues/9170)).
- **Speed**: a torch model went from 1.8 s to 12 s after packing in one report ([issue #8211](https://github.com/pyinstaller/pyinstaller/issues/8211)) — plausibly thread/BLAS configuration, but a real risk that must be regression-tested.
- onefile additionally pays extraction time on every launch.

**[measured]** PyInstaller 6.22.3, `--onedir --collect-data demucs`, minimal worker (leaner than `--collect-all`, which drags in training/eval submodules and their optional deps):

- Build time: **3 min 12 s** (single-threaded analysis; CI will be similar)
- `dist/sfworker` size: **881 MB**; `tar + zstd -19` → **197 MB**
- Frozen worker on a 5 s clip: **8.58 s wall, 1,056 MB peak RSS, exit 0** — no slowdown versus the unfrozen venv on the same clip (9.12 s, 1,029 MB, measured on the Python 3.14 environment)

Working recipe:

```bash
pyinstaller --noconfirm --onedir --name stemforge-worker \
  --collect-data demucs \
  --hidden-import demucs.htdemucs --hidden-import demucs.hdemucs \
  --hidden-import demucs.repo --hidden-import demucs.hf \
  worker.py
```

- The torch hook supplies torch's data files, submodules and DLLs; `--collect-all torch` is unnecessary.
- **Dynamic import trap:** `demucs/hf.py` reconstructs the model class with `importlib.import_module(metadata['klass'])`, which static analysis cannot see. List `demucs.htdemucs` / `demucs.hdemucs` explicitly; today they are reached transitively via `apply.py`, but that is not a contract.
- `--collect-data demucs` carries `demucs/remote/*.yaml` for the legacy fallback; if you always load via HF you can drop it, but keep it for offline resilience.
- Windows: the hook collects MKL DLLs when the wheel depends on `mkl`; nothing extra is required for the CPU wheel. Ship the VC++ runtime as Inno Setup would normally do.

**Nuitka** (4.3.5, 2026-11-08) has a `torch` entry in its package config: it collects `torch/lib` DLLs and data, strips `torch.utils.cpp_extension`, `triton`, `numba`, and — importantly — **disables Torch JIT by default in standalone mode** (`--module-parameter=torch-disable-jit=yes|no`, `PYTORCH_JIT=0`) ([standard.nuitka-package.config.yml](https://github.com/Nuitka/Nuitka/blob/develop/nuitka/plugins/standard/standard.nuitka-package.config.yml)). The old `TorchPlugin.py` is deprecated/no-op. Demucs does not need TorchScript, so JIT-off is fine. Nuitka compilation of torch's Python code is slow (tens of minutes) and was not attempted here.

**Real-world reference points** (more useful than synthetic estimates):

- **Demucs-GUI** ships a *conda environment in an AppImage* rather than freezing: CPU release **234.7 MB** (7z) / **508.7 MB** (Linux tar), CUDA build 1.5 GB ([releases](https://github.com/CarlGao4/Demucs-Gui/releases), [linux build workflow](https://github.com/CarlGao4/demucs-gui-linux-build/blob/main/.github/workflows/build.yml)). Their README explicitly says the binaries are huge because of torch.
- **Ultimate Vocal Remover** installers are **1.69–2.08 GB** (torch + CUDA + model zoo) ([releases](https://github.com/Anjok07/ultimatevocalremovergui/releases)).

**Recommendation — both routes are viable, pick by app shape:**

- **PyInstaller `--onedir`** (measured on Linux): 881 MB on disk, 197 MB compressed, 3 min build, and no runtime penalty in our test (8.58 s vs 9.12 s for a 5 s clip). The Windows bundle should be smaller (torch installed 484 MB vs 760 MB). Natural choice if StemForge itself is a Python app that will be frozen anyway. Use **onedir, never onefile**.
- **Embedded CPython environment** (venv/conda-pack style): 974 MB on disk, ≈220 MB compressed, zero freezing risk, readable tracebacks, and a future GPU pack can swap only the torch wheels. Natural choice if the app shell is not Python (Electron/Tauri/Rust) and the worker is a sidecar.

Either way: budget 200–250 MB compressed for the runtime, test the frozen worker's RTF in CI (see PyInstaller #8211), and keep the worker's stdout protocol free of library noise.

---

## 2. Alternatives

### 2.1 demucs.cpp

C++17 reimplementation of Demucs v3/v4 inference, MIT, `sevagh/demucs.cpp`, 181 stars, last push 2026-11-06 ([repo](https://github.com/sevagh/demucs.cpp)).

- **Quality**: SDR essentially identical to PyTorch on the reference track (vocals 8.370 vs 8.339; drums 10.002 vs 10.058) ([SDR_scores.md](https://github.com/sevagh/demucs.cpp/blob/main/.github/SDR_scores.md)).
- **Weights**: converted ggml f16 — `ggml-model-htdemucs-4s-f16.bin` is **81 MB**, `htdemucs_ft` 4 × 81 MB, `htdemucs_6s` 53 MB; pre-converted files on [HF](https://huggingface.co/datasets/Retrobear/demucs.cpp).
- **CPU speed** (author's 16-core Ryzen 5950X, 4-minute track): **10m23s** single-threaded-program-with-OpenMP, **4m9s** with 4 worker threads ([PERFORMANCE.md](https://github.com/sevagh/demucs.cpp/blob/main/.github/PERFORMANCE.md)). That is RTF ≈2.6 and ≈1.04 — comparable to PyTorch Demucs, not a speed win.
- **Packaging**: no downloadable binaries since 2024 (all releases have zero assets); Windows binaries are an **open request** ([issue #25](https://github.com/sevagh/demucs.cpp/issues/25)); build flags include `-march=native`, producing CPU-specific binaries; needs OpenMP + a BLAS.
- **Integration**: no Python API and no progress callback — you would parse stdout or wrap a C++ CLI.

Verdict: good engineering, wrong shape for a cross-platform Python app. Only reconsider if bundle size becomes the dominant constraint and you are willing to own C++ builds for both OSes.

### 2.2 python-audio-separator (UVR / MDX / VR / RoFormer)

`audio-separator 0.48.0`, released 2026-10-29, MIT, actively developed by Karaoke Nerds; repo `nomadkaraoke/python-audio-separator` (1,489 stars) ([PyPI](https://pypi.org/pypi/audio-separator/json), [repo](https://github.com/nomadkaraoke/python-audio-separator)).

- **Python**: `>=3.10,!=3.14.1`; classifiers list 3.10–3.13 ([pyproject.toml](https://github.com/nomadkaraoke/python-audio-separator/blob/main/pyproject.toml)). Development status **4 – Beta**.
- **Dependency weight**: `torch>=2.3`, `onnxruntime` (extra), `librosa`, `scipy`, `numpy>=2`, `samplerate`, `diffq`, plus `onnx`/`onnx2torch` shims. It needs *both* torch and ONNX Runtime — strictly more to bundle than demucs alone.
- **Model zoo** (auto-downloaded from GitHub releases, [`TRvlvr/model_repo`](https://github.com/TRvlvr/model_repo/releases/tag/all_public_uvr_models)): `UVR-MDX-NET-Inst_HQ_3.onnx` **66.8 MB**, `UVR-MDX-NET-Voc_FT.onnx` 66.8 MB, `5_HP-Karaoke-UVR.pth` 126.8 MB, `UVR_MDXNET_KARA_2.onnx` 52.8 MB, RoFormer checkpoints 639 MB.
- **Progress**: no separation callback; tqdm is used for *downloads* only ([separator.py](https://github.com/nomadkaraoke/python-audio-separator/blob/main/audio_separator/separator/separator.py)). You get a blocking `separate()` call.
- **Quality**: MDX-Net family ≈7.5 dB SDR (KUIELAB-MDX-Net, 2nd place Sony Demixing Challenge 2021); UVR fine-tunes are tuned for vocals/karaoke, not 4-stem accuracy ([Demucs comparison table](https://github.com/adefossez/demucs#comparison-with-accuracy), [kuielab repo](https://github.com/kuielab/mdx-net-submission)).
- **Licensing**: code MIT; README asks for credit to UVR; individual model licences are not stated per file ([README §License](https://github.com/nomadkaraoke/python-audio-separator#license-)). Same class of ambiguity as Demucs weights, with less clarity.

Verdict: strongest *alternative*, and the right source if StemForge later adds a 2-stem "karaoke" mode with MDX/VR models. As the v0.1.0 four-stem engine it is heavier, less accurate, beta, and has no progress hook.

### 2.3 Spleeter

`deezer/spleeter 2.4.2`, MIT, last upload 2025-04-03, `requires-python <3.12,>=3.8`, pins `tensorflow==2.12.1` ([PyPI](https://pypi.org/pypi/spleeter/json)).

- **Python 3.12+ impossible** without patching: TF 2.12 has no cp312 wheels.
- **Quality**: 4-stem MWF SDR vocals 6.86 / drums 6.71 / bass 5.51 / other 4.55, ≈5.9 overall ([Separation Performances wiki](https://github.com/deezer/spleeter/wiki/Separation-Performances)).
- **Speed**: "100x faster than real-time on GPU" (README), the fastest option by a wide margin; CPU is still fast because it is a spectrogram U-Net.
- **Size**: 4-stem model archive **146.3 MB** (2-stem 73.1 MB, 5-stem 182.8 MB) ([release v1.4.0](https://github.com/deezer/spleeter/releases/tag/v1.4.0)).
- **Maintenance**: effectively dead — commits since 2025 are README link fixes; TF 2.12 is EOL.

Verdict: reject. Python ceiling 3.11, EOL TensorFlow, ~2 dB worse SDR, and a second ML runtime (TF) in the bundle.

### 2.4 MDX-Net (original)

`kuielab/mdx-net-submission`, MIT, last push 2023-01-06, 134 stars. PyTorch implementation of the Sony Demixing Challenge 2021 runner-up; the usable artefacts today are the ONNX conversions distributed through UVR/audio-separator, not this repo.

Verdict: not a standalone option in 2026 — it survives only *inside* audio-separator, so choosing it means choosing that package.

### 2.5 Comparison

| | Demucs 4.1.0 (htdemucs) | demucs.cpp | audio-separator | Spleeter 2.4.2 | MDX-Net (orig.) |
|---|---|---|---|---|---|
| Code licence | MIT | MIT | MIT | MIT | MIT |
| Weights licence | **unclear** | unclear (same) | per-model, unclear | not stated | unclear |
| Python | 3.10–3.14 | none (C++) | 3.10–3.13 | ≤3.11 | via UVR only |
| Runtime deps | torch only | C++/BLAS | torch **+** onnxruntime | tensorflow | torch |
| Model size (4 stems) | **84 MB** | 81 MB | 67 MB per model (2-stem MDX) / 127 MB (VR) | 146 MB | ~67 MB ONNX |
| Quality (SDR) | **9.0** | 9.0 | ~7.5 | ~5.9 | ~7.5 |
| CPU speed (4-min track, 4 core) | **≈6 min** | ≈10 min (1 thr) / 4 min (4 thr) | model-dependent | <1–2 min | ~2–4 min |
| Progress callback | **yes** | no | no | no | no |
| Windows packaging | wheels | DIY build | wheels | wheels (old) | n/a |
| Maintenance | low | low | **active** | dead | dead |

---

## 3. Recommended v0.1.0 design

### 3.1 Pins

```
# worker environment (CPython 3.13.x)
torch==2.15.0+cpu            # from https://download.pytorch.org/whl/cpu
demucs==4.1.0
numpy==2.5.4                 # NOT a declared demucs dependency — see below
sphn==0.2.1
huggingface-hub==2.4.0       # only used to fetch weights; pin the model revision too
safetensors==0.8.0
```

**Why numpy must be pinned explicitly:** demucs 4.1.0's dependency list contains numpy only for Intel macOS (`numpy<2; sys_platform == "darwin" and platform_machine == "x86_64"`), but `demucs/transformer.py` does `import numpy as np` at module scope. **[measured]** a clean `uv pip install demucs==4.1.0` (plus torch) on Linux installs **no numpy**, and:

```
>>> from demucs.api import Separator
ModuleNotFoundError: No module named 'numpy'      # demucs/transformer.py line 14
```

That means the released 4.1.0 is **broken out of the box** on Linux and Windows unless numpy comes in from elsewhere. The CLI fails the same way. Pin `numpy` explicitly, smoke-test the import in CI, and report this upstream — it is a release blocker, not a nicety.

Weights pin: `adefossez/HTDemucs` revision `cbc8a9b1a87023b7fd74e7b3412e6321c0eab003`.

### 3.2 Worker invocation

Separate process, separate interpreter:

```
<app>/runtime/py313/bin/python -m stemforge_worker \
    --model htdemucs --device cpu \
    --input <workspace>/source.wav \
    --out <workspace>/stems \
    --two-stems vocals \        # omit for 4 stems
    --threads 4
```

The worker:

1. sets `HF_HOME=<appdata>/models/hf` and `HF_HUB_OFFLINE=1`;
2. is launched with `OMP_NUM_THREADS=<physical cores>` in its environment, then calls `torch.set_num_threads(args.threads)` after import;
3. constructs `Separator` once and reuses it for the queue (model load ~2–4 s warm);
4. emits JSONL on **stdout**; all logs on **stderr**;
5. writes **float32 WAV** stems by default to match StemForge's canonical internal format (`--int16` available for export previews), then exits 0.

Two-stem implementation: call `Separator` normally, save `vocals`, compute `no_vocals = sum(other stems)` (matching demucs' `--other-method=add`), or `origin - vocals` for `minus`. Do **not** pass `--two-stems` to the CLI and expect a speed-up.

### 3.3 Progress protocol (JSON Lines, stdout)

One object per line, `v` for future evolution:

```json
{"v":1,"event":"hello","engine":"demucs","engine_version":"4.1.0","model":"htdemucs","device":"cpu","sample_rate":44100,"stems":["vocals","no_vocals"]}
{"v":1,"event":"stage","stage":"model_load","state":"start"}
{"v":1,"event":"stage","stage":"model_load","state":"end","seconds":1.98}
{"v":1,"event":"progress","stage":"separate","fraction":0.334,"segment":2,"segments":41,"audio_seconds_done":80.2,"audio_seconds_total":240.0,"eta_seconds":247.0}
{"v":1,"event":"stem","stem":"vocals","path":"/tmp/.../vocals.wav","format":"wav","sample_rate":44100,"channels":2}
{"v":1,"event":"done","elapsed_seconds":374.2,"stems":["vocals","no_vocals"]}
{"v":1,"event":"error","code":"model_load_failed","message":"…","retryable":false}
```

Rules:

- `fraction` computed from `state=="end"` events: `(segment_offset + segment_samples) / audio_length`, clamped.
- `segments` = `ceil((audio_length - segment_samples) / stride) + 1`; recompute when `audio_length`/`segment` are known.
- Progress cadence is one event per 7.8 s chunk (≈5.9 s stride) — sufficient as a liveness heartbeat; a stuck worker is detectable after 2–3 missed events.
- `eta_seconds` = `elapsed * (1 - fraction) / fraction`, smoothed (EMA) to avoid jitter.
- Cancellation: parent sends `SIGTERM` (Linux) / `TerminateProcess` (Windows); worker installs a handler that raises `KeyboardInterrupt` in the callback (documented abort path), then removes partial outputs. Hard-kill fallback after 5 s.
- Never parse the demucs CLI's human output or tqdm bars.

### 3.4 Bundle-size estimate

| Component | On disk | Compressed (zstd/7z) |
|---|---|---|
| CPython 3.13 + torch 2.15 CPU + demucs + deps (venv **[measured 974 MB]**) | 974 MB | ≈220 MB (est.) |
| PyInstaller `--onedir` equivalent **[measured]** | 881 MB | **197 MB** |
| `htdemucs` weights (downloaded, not bundled) | +84 MB | +84 MB |
| ffmpeg (static, for FLAC/exotic input) | +80–120 MB | +30–50 MB |
| **v0.1.0 installer target** | — | **≈230–280 MB** (weights on demand), ≈310–360 MB bundled |
| Optional CUDA pack | +2.5 GB | +1.2–1.5 GB |

The Demucs-GUI CPU release (234.7 MB 7z for a conda env that also contains PySide6) confirms this order of magnitude in the wild. Do **not** plan for a <100 MB installer with PyTorch inside.

### 3.5 Risks and mitigations

| Risk | Mitigation |
|---|---|
| Weights licence unclear | Don't bundle; first-run download with notice; legal review before v0.2.0 |
| PyInstaller torch slowdown / size | Prefer `--onedir`; measure frozen RTF in CI; fall back to an embedded interpreter if it regresses |
| numpy missing from demucs deps | **Release blocker**: `import demucs.api` fails without it. Pin `numpy`; smoke-test import and a 1 s separation in CI |
| HF hub network at runtime | Pre-seed cache in installer or first-run download; `HF_HUB_OFFLINE=1` afterwards |
| Windows symlink-less HF cache | Accept blob copies (2× disk) or set `HF_HUB_CACHE` to app dir |
| Thread oversubscription | Worker sets `OMP_NUM_THREADS`/`torch.set_num_threads` = physical cores |
| Long tracks blow memory | Cap input length or stream chunks; **1.8 GB RSS measured for a 4-minute track**; warn below 4 GB free RAM |
| Engine abandonment | Isolate behind worker protocol; audio-separator is the drop-in fallback |

### 3.6 GPU for v0.1.0 — recommendation

**Ship CPU-only torch in v0.1.0** (`torch==2.15.0+cpu`).

- It matches the product constraint ("CPU-first") and keeps the bundle smallest: 209.5 MB wheel / 760 MB installed on Linux, 126.6 MB / 484 MB on Windows, versus a CUDA wheel plus `nvidia-*` dependencies at ≈2.5 GB installed (≈1.3 GB extra compressed).
- On Windows, CUDA requires a **different wheel index** (`download.pytorch.org/whl/cuXXX`) and therefore a separate torch installation; a CPU wheel cannot be "switched on" later.
- Keep `device` as the single switch in the worker protocol. Accept `cuda` only when a GPU pack is installed, and fall back to `cpu` on any init error, reporting the fallback in the `hello` event.
- If GPU becomes a v0.x feature, ship it as an optional component that replaces the torch wheel inside the runtime environment (or as a second worker env), not as a second app build — and validate with `torch.cuda.is_available()` at install time.
- MPS/macOS is out of scope (StemForge targets Linux and Windows only).

---

## 4. Open questions

1. **Weights licence**: can StemForge legally redistribute `htdemucs` weights (or fetch them on the user's behalf) in a GPL-3.0 product? If not, is an on-demand download with a research-use notice acceptable? Blocks any bundled-weights decision.
2. **GPU scope for v0.1.0**: recommendation is CPU-only (§3.6). Confirm the product accepts no GPU acceleration in v0.1.0; if not, choose between an optional torch-replacement component and a second worker env.
3. **Stem file format**: canonical audio is 32-bit float WAV, so the worker should write float32 stems directly (≈338 MB for 4 minutes × 4 stems) rather than int16-then-convert. Confirm the workspace disk budget and whether FLAC intermediates are wanted (~50% smaller, adds ffmpeg to the worker).
4. **Python 3.14**: both torch and sphn ship cp314 wheels and a 3.14 run succeeded here, but 3.13 has broader wheel coverage. Does the app runtime need 3.14, or is 3.13 acceptable for the app's lifetime? (If 3.14, add it to CI now.)
5. **Progress granularity**: is one event per 7.8 s chunk enough for the UI, or should the worker also emit a time-based tick during a chunk?
6. **Model choice**: `htdemucs` (84 MB, 1×) vs `htdemucs_ft` (336 MB, 4× time, marginally better). v0.1.0 should default to `htdemucs`; is `ft` worth offering as a "best quality" toggle? (The published 9.20 dB figure also requires sparse-attention kernels that are not in the release.)
7. **demucs upstream**: report the missing `numpy` dependency and the HF-cache/offline behaviour; does the project accept PRs in its current low-activity state?
8. **Licence of `lameenc`** (LGPL-3.0-or-later) if MP3 export is ever enabled: LGPLv3 is GPLv3-compatible but carries relinking obligations for a bundled binary.

---

## Sources

All accessed 2026-11-14.

- Demucs PyPI metadata and 4.1.0 files — https://pypi.org/pypi/demucs/json
- Demucs repository, README, LICENSE — https://github.com/adefossez/demucs
- Demucs release notes 4.1.0 — https://github.com/adefossez/demucs/blob/main/docs/release.md
- Demucs source: `separate.py`, `api.py`, `apply.py`, `pretrained.py`, `hf.py`, `repo.py`, `remote/files.txt` — https://github.com/adefossez/demucs/tree/main/demucs
- Demucs Windows/Linux docs — https://github.com/adefossez/demucs/blob/main/docs/windows.md, `docs/linux.md`
- Demucs CI workflow — https://github.com/adefossez/demucs/blob/main/.github/workflows/tests.yml
- HT Demucs paper — https://arxiv.org/abs/2211.08553
- torch 2.15.0 PyPI files — https://pypi.org/pypi/torch/2.15.0/json
- PyTorch CPU wheel index — https://download.pytorch.org/whl/cpu/torch/
- Hugging Face model repos — https://huggingface.co/adefossez/HTDemucs, `/HTDemucs-ft`, `/HTDemucs-6s
- HF cache management docs — https://huggingface.co/docs/huggingface_hub/guides/manage-cache
- sphn — https://pypi.org/pypi/sphn/0.2.1/json, https://github.com/kyutai-labs/sphn
- PyInstaller torch hook — https://github.com/pyinstaller/pyinstaller-hooks-contrib/blob/master/_pyinstaller_hooks_contrib/stdhooks/hook-torch.py
- PyInstaller issues #9170, #8211 — https://github.com/pyinstaller/pyinstaller/issues/9170, /issues/8211
- Nuitka torch package config — https://github.com/Nuitka/Nuitka/blob/develop/nuitka/plugins/standard/standard.nuitka-package.config.yml
- Demucs-GUI releases and Linux build — https://github.com/CarlGao4/Demucs-Gui/releases, https://github.com/CarlGao4/demucs-gui-linux-build
- demucs.cpp — https://github.com/sevagh/demucs.cpp, `.github/PERFORMANCE.md`, `.github/SDR_scores.md`, issue #25
- python-audio-separator — https://pypi.org/pypi/audio-separator/json, https://github.com/nomadkaraoke/python-audio-separator
- UVR model repository — https://github.com/TRvlvr/model_repo/releases/tag/all_public_uvr_models
- Spleeter — https://pypi.org/pypi/spleeter/json, https://github.com/deezer/spleeter, wiki Separation-Performances, release v1.4.0
- MDX-Net submission — https://github.com/kuielab/mdx-net-submission
- Ultimate Vocal Remover releases — https://github.com/Anjok07/ultimatevocalremovergui/releases
