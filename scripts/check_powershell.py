"""Parse every PowerShell source that CI executes before it reaches a runner.

Checked-in ``.ps1`` files are parsed from disk. A workflow's ``run`` text exists
only inside YAML, so this gate extracts it and hands it to the PowerShell engine
the step declares; ``ParseInput`` keeps the workflow label and the block's own
line numbers in the error instead of losing them to a temporary file.

An empty discovery result is a failure, not a pass: a moved directory or a
broken glob must not remove the gate silently.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOTS = (Path("scripts"), Path("packaging") / "windows")
WORKFLOW_ROOT = Path(".github") / "workflows"
POWERSHELL_ENGINES = ("pwsh", "powershell")
PARSER = Path(__file__).with_name("parse-powershell.ps1")
GITHUB_EXPRESSION = re.compile(r"\$\{\{.*?\}\}", re.DOTALL)


@dataclass(frozen=True)
class InlineBlock:
    label: str
    text: str
    engine: str


def find_shell(engine: str) -> str | None:
    return shutil.which(engine)


def discover_scripts(root: Path) -> list[Path]:
    found = [
        path for folder in SCRIPT_ROOTS for path in (root / folder).rglob("*.ps1") if path.is_file()
    ]
    return sorted(found)


def powershell_engine(shell: object) -> str | None:
    if not isinstance(shell, str):
        return None
    words = shell.split()
    if not words:
        return None
    keyword = words[0].lower().removesuffix(".exe")
    return keyword if keyword in POWERSHELL_ENGINES else None


def is_powershell_shell(shell: object) -> bool:
    return powershell_engine(shell) is not None


def neutralize_github_expressions(text: str) -> str:
    """Replace runner-substituted expressions with syntax PowerShell accepts.

    GitHub interpolates ``${{ ... }}`` before the script reaches the shell, so a
    raw block can contain text that is not PowerShell. ``${null}`` stays valid
    inside a quoted string and in argument position, including the ``"${x}:"``
    shape that a bare ``$null`` would turn back into a scope-qualified
    variable; keeping newlines keeps the block's line numbers aligned.
    """

    def replace(match: re.Match[str]) -> str:
        return "${null}" + "\n" * match.group(0).count("\n")

    return GITHUB_EXPRESSION.sub(replace, text)


def discover_inline_blocks(root: Path) -> list[InlineBlock]:
    blocks: list[InlineBlock] = []
    for path in sorted((root / WORKFLOW_ROOT).glob("*.yml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        jobs = payload.get("jobs") if isinstance(payload, dict) else None
        if not isinstance(jobs, dict):
            continue
        for job_name, job in jobs.items():
            if not isinstance(job, dict):
                continue
            steps = job.get("steps")
            # A reusable-workflow job has no steps of its own.
            if not isinstance(steps, list):
                continue
            for index, step in enumerate(steps, 1):
                if not isinstance(step, dict):
                    continue
                engine = powershell_engine(step.get("shell"))
                if engine is None:
                    continue
                label = f"{path.name}:{job_name}:{step.get('name') or f'step {index}'}"
                text = step.get("run")
                if not isinstance(text, str):
                    raise ValueError(f"{label}: PowerShell step has no run text")
                blocks.append(InlineBlock(label=label, text=text, engine=engine))
    return blocks


def parse_sources(root: Path, scripts: list[Path], blocks: list[InlineBlock]) -> int:
    available = {engine: find_shell(engine) for engine in POWERSHELL_ENGINES}

    def engine_for(engine: str) -> str:
        if available[engine]:
            return engine
        return "pwsh" if available["pwsh"] else "powershell"

    if not any(available.values()):
        print("check_powershell: no PowerShell host is available; install pwsh", file=sys.stderr)
        return 2

    # The Windows workflows invoke their .ps1 files with powershell.exe, so
    # that is the engine whose syntax they must satisfy.
    requests: dict[str, dict[str, list[object]]] = {}
    if scripts:
        request = requests.setdefault(engine_for("powershell"), {"scripts": [], "blocks": []})
        request["scripts"].extend(path.relative_to(root).as_posix() for path in scripts)
    for block in blocks:
        request = requests.setdefault(engine_for(block.engine), {"scripts": [], "blocks": []})
        request["blocks"].append(
            {"label": block.label, "text": neutralize_github_expressions(block.text)}
        )

    status = 0
    with tempfile.TemporaryDirectory(prefix="stemforge-pwsh-") as directory:
        for engine, request in requests.items():
            shell = available[engine]
            manifest = {"root": str(root), **request}
            manifest_path = Path(directory) / f"manifest-{engine}.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            try:
                completed = subprocess.run(
                    [
                        shell,
                        "-NoProfile",
                        "-NonInteractive",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(PARSER),
                        "-Manifest",
                        str(manifest_path),
                    ],
                    capture_output=True,
                )
            except OSError as error:
                print(f"check_powershell: cannot start {shell}: {error}", file=sys.stderr)
                return 2

            sys.stdout.write(completed.stdout.decode("utf-8", "replace"))
            sys.stderr.write(completed.stderr.decode("utf-8", "replace"))
            if completed.returncode != 0:
                status = completed.returncode

    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Parse every PowerShell source CI executes.")
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    arguments = parser.parse_args(argv)
    root = arguments.root.resolve()

    scripts = discover_scripts(root)
    try:
        blocks = discover_inline_blocks(root)
    except (ValueError, yaml.YAMLError) as error:
        print(f"check_powershell: {error}", file=sys.stderr)
        return 1
    if not scripts and not blocks:
        print(
            "check_powershell: no PowerShell sources found; refusing to pass an empty gate",
            file=sys.stderr,
        )
        return 1

    status = parse_sources(root, scripts, blocks)
    if status == 0:
        print(
            f"check_powershell: parsed {len(scripts)} script(s) and {len(blocks)} inline block(s)"
        )
    return status


if __name__ == "__main__":
    raise SystemExit(main())
