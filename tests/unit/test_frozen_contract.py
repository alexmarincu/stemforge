"""The frozen-build contract: lazy imports and the two Bundle roots.

Ticket 06 §11 fixes ``freeze_support()`` as the first statement in the main
guard and keeps torch/Demucs imports inside ``workers/separation.py::run()``.
Ticket 10 §3 and ADR 0010 split resource resolution into a **data root**
(``sys._MEIPASS`` when frozen) and a **Bundle root**
(``Path(sys.executable).resolve().parent`` when frozen), both of which are the
development checkout's package and repo roots respectively.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from stemforge.platform import binaries, resources

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src" / "stemforge"
HEAVY_MODULES = frozenset({"torch", "demucs"})
SEPARATION_WORKER = SRC / "workers" / "separation.py"


def _source_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _main_guard(tree: ast.Module) -> ast.If:
    for node in tree.body:
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare):
            left = node.test.left
            if isinstance(left, ast.Name) and left.id == "__name__":
                return node
    raise AssertionError("__main__.py has no main guard")


def _module_scope_imports(tree: ast.Module) -> set[str]:
    """Top-level module names imported anywhere outside a function body."""
    roots: set[str] = set()

    class Visitor(ast.NodeVisitor):
        def visit_Import(self, node: ast.Import) -> None:
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            if node.module and node.level == 0:
                roots.add(node.module.split(".", 1)[0])

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            return

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            return

        def visit_Lambda(self, node: ast.Lambda) -> None:
            return

    Visitor().visit(tree)
    return roots


def _imports_in_function(tree: ast.Module, function_name: str) -> set[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            return _module_scope_imports(ast.Module(body=node.body, type_ignores=[]))
    raise AssertionError(f"{function_name}() not found")


def _all_imports(tree: ast.Module) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".", 1)[0])
    return roots


@pytest.mark.unit
def test_freeze_support_is_the_first_executable_statement_in_the_main_guard() -> None:
    source = (SRC / "__main__.py").read_text(encoding="utf-8")
    guard = _main_guard(ast.parse(source))

    first = guard.body[0]
    assert isinstance(first, ast.Expr)
    call = first.value
    assert isinstance(call, ast.Call)
    assert isinstance(call.func, ast.Attribute)
    assert call.func.attr == "freeze_support"
    assert isinstance(call.func.value, ast.Name)
    assert call.func.value.id == "multiprocessing"


@pytest.mark.unit
def test_no_module_outside_the_separation_worker_imports_torch_or_demucs_at_module_scope() -> None:
    offenders: list[str] = []
    for path in _source_files():
        if path == SEPARATION_WORKER:
            continue
        imported = _module_scope_imports(ast.parse(path.read_text(encoding="utf-8")))
        heavy = imported & HEAVY_MODULES
        if heavy:
            offenders.append(f"{path.relative_to(PROJECT_ROOT)}: {sorted(heavy)}")

    assert not offenders, "module-scope heavy imports found: " + "; ".join(offenders)


@pytest.mark.unit
def test_the_separation_worker_imports_torch_and_demucs_only_inside_run() -> None:
    tree = ast.parse(SEPARATION_WORKER.read_text(encoding="utf-8"))

    assert not (_module_scope_imports(tree) & HEAVY_MODULES)
    for name in ("torch", "demucs"):
        if name in _all_imports(tree):
            assert name in _imports_in_function(tree, "run"), f"{name} must be imported in run()"


@pytest.mark.unit
def test_development_roots_are_the_package_and_the_repository() -> None:
    assert resources.is_frozen() is False
    assert resources.data_root() == PROJECT_ROOT / "src" / "stemforge"
    assert resources.bundle_root() == PROJECT_ROOT
    assert resources.assets_dir() == PROJECT_ROOT / "src" / "stemforge" / "assets"
    assert resources.models_lock_path() == SRC / "models" / "models.lock.json"
    assert (resources.assets_dir() / "stemforge.ico").is_file()
    assert resources.models_lock_path().is_file()


@pytest.mark.unit
def test_frozen_roots_come_from_meipass_and_the_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "_internal"
    (data_root / "assets").mkdir(parents=True)
    (data_root / "assets" / "stemforge.ico").write_bytes(b"ico")
    (data_root / "models").mkdir()
    (data_root / "models" / "models.lock.json").write_text("{}", encoding="utf-8")

    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    executable = bundle_root / "StemForge"
    executable.write_text("", encoding="utf-8")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(data_root), raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))

    assert resources.is_frozen() is True
    assert resources.data_root() == data_root
    assert resources.bundle_root() == bundle_root
    assert resources.assets_dir() == data_root / "assets"
    assert resources.models_lock_path() == data_root / "models" / "models.lock.json"


@pytest.mark.unit
def test_frozen_binaries_resolve_from_the_bundle_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "_internal"
    data_root.mkdir()
    bundle_root = tmp_path / "bundle"
    ffmpeg = bundle_root / "vendor" / "ffmpeg" / "ffmpeg"
    ffmpeg.parent.mkdir(parents=True)
    ffmpeg.write_text("", encoding="utf-8")
    executable = bundle_root / "StemForge"
    executable.write_text("", encoding="utf-8")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(data_root), raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.delenv("STEMFORGE_TEST_FFMPEG", raising=False)

    assert binaries.find_vendor("ffmpeg/ffmpeg") == ffmpeg


@pytest.mark.unit
def test_frozen_missing_binary_raises_bundle_damaged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "_internal"
    data_root.mkdir()
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    executable = bundle_root / "StemForge"
    executable.write_text("", encoding="utf-8")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(data_root), raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.delenv("STEMFORGE_TEST_FFMPEG", raising=False)

    with pytest.raises(binaries.BundleDamagedError) as raised:
        binaries.find_vendor("ffmpeg/ffmpeg")

    assert raised.value.code == "preflight.bundle_damaged"
    assert "ffmpeg" in str(raised.value)


@pytest.mark.unit
def test_development_binary_prefers_an_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = tmp_path / "ffmpeg"
    candidate.write_text("", encoding="utf-8")
    monkeypatch.setenv("STEMFORGE_TEST_FFMPEG", str(candidate))

    assert binaries.find_vendor("missing/tool", "STEMFORGE_TEST_FFMPEG") == candidate


@pytest.mark.unit
def test_development_missing_binary_falls_back_to_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_which(name: str) -> str | None:
        return "/usr/bin/ffmpeg" if name == "ffmpeg" else None

    monkeypatch.delenv("STEMFORGE_TEST_FFMPEG", raising=False)
    monkeypatch.setattr(binaries.shutil, "which", fake_which)

    assert binaries.find_vendor("ffmpeg/ffmpeg") == Path("/usr/bin/ffmpeg")
    assert binaries.find_vendor("rubberband/rubberband") is None


@pytest.mark.unit
def test_worker_command_uses_the_frozen_flag_or_the_module_in_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    argv = ["separation", "--input", "in.wav", "--out", "out"]

    assert binaries.worker_command(argv) == [
        sys.executable,
        "-m",
        "stemforge.workers.separation",
        "--input",
        "in.wav",
        "--out",
        "out",
    ]

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", "/nonexistent", raising=False)

    assert binaries.worker_command(argv) == [
        sys.executable,
        "--worker",
        "separation",
        "--input",
        "in.wav",
        "--out",
        "out",
    ]


@pytest.mark.unit
def test_worker_command_requires_a_worker_name() -> None:
    with pytest.raises(ValueError, match=r"argv\[0\]"):
        binaries.worker_command([])


@pytest.mark.unit
def test_the_frozen_runtime_hook_guards_nicegui_imports(tmp_path: Path) -> None:
    hook = PROJECT_ROOT / "packaging" / "pyinstaller" / "runtime-hooks" / "rthook_stemforge_env.py"
    script = "\n".join(
        [
            "import importlib.util",
            "import sys",
            f"spec = importlib.util.spec_from_file_location('stemforge_rthook', {str(hook)!r})",
            "module = importlib.util.module_from_spec(spec)",
            "spec.loader.exec_module(module)",
            "assert sys.stemforge_frozen_env_guard is True",
            "try:",
            "    import nicegui",
            "except RuntimeError as exc:",
            "    print(f'GUARDED: {exc}')",
            "else:",
            "    raise SystemExit('the runtime hook did not guard the import')",
        ]
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("NICEGUI_STORAGE_PATH", "HF_HOME")
    }

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "GUARDED: StemForge frozen build:" in result.stdout
