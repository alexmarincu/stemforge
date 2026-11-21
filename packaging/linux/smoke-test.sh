#!/usr/bin/env bash
# Run the frozen bundle's diagnostic flags and keep the JSON reports.
# Ticket 11 §4: --self-test plus --smoke-test, no weights, exit nonzero on
# failure. Ticket 20 adds --with-separation and the AppImage extraction check.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
cd "$repo_root"

bundle=""
reports="$repo_root/build/reports"
with_separation=0
appimage=""

usage() {
    cat <<'EOF'
Usage: packaging/linux/smoke-test.sh [--bundle DIR] [--with-separation]
                                    [--appimage FILE]

  --bundle DIR       assembled Bundle (default: dist/StemForge-<ver>-linux-x86_64)
  --with-separation  also run --smoke-separation (needs the pinned weights in
                     STEMFORGE_HOME; install them with --install-model)
  --appimage FILE    also run FILE --appimage-extract-and-run --self-test
EOF
}

while (($# > 0)); do
    case "$1" in
        --bundle)
            [[ $# -ge 2 ]] || {
                echo "smoke-test.sh: --bundle needs a value" >&2
                exit 2
            }
            bundle="$2"
            shift 2
            ;;
        --with-separation) with_separation=1; shift ;;
        --appimage)
            [[ $# -ge 2 ]] || {
                echo "smoke-test.sh: --appimage needs a value" >&2
                exit 2
            }
            appimage="$2"
            shift 2
            ;;
        -h | --help) usage; exit 0 ;;
        -*) usage >&2; exit 2 ;;
        *) bundle="$1"; shift ;;
    esac
done

if [[ -z "$bundle" ]]; then
    python="$repo_root/.venv/bin/python"
    if [[ ! -x "$python" ]]; then
        echo "smoke-test.sh: $python is missing; run uv sync or pass --bundle" >&2
        exit 1
    fi
    version="$("$python" -c 'import stemforge; print(stemforge.__version__)')"
    bundle="$repo_root/dist/StemForge-${version}-linux-x86_64"
fi

if [[ ! -x "$bundle/StemForge" ]]; then
    echo "smoke-test.sh: $bundle/StemForge is missing; run packaging/linux/build.sh" >&2
    exit 1
fi

mkdir -p "$reports"
# Keep the smoke run's writable state inside build/, never a real user profile.
export STEMFORGE_HOME="${STEMFORGE_HOME:-$repo_root/build/smoke-home}"
mkdir -p "$STEMFORGE_HOME"

status=0
"$bundle/StemForge" --self-test --report "$reports/self-test.json" || status=1
"$bundle/StemForge" --smoke-test --report "$reports/smoke-test.json" || status=1
if ((with_separation)); then
    "$bundle/StemForge" --smoke-separation --report "$reports/smoke-separation.json" ||
        status=1
fi
if [[ -n "$appimage" ]]; then
    if [[ ! -x "$appimage" ]]; then
        echo "smoke-test.sh: $appimage is missing or not executable" >&2
        status=1
    else
        APPIMAGE_EXTRACT_AND_RUN=1 "$appimage" --appimage-extract-and-run --self-test \
            --report "$reports/appimage-self-test.json" || status=1
    fi
fi
exit "$status"
