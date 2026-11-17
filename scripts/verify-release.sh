#!/bin/sh
# Verify a downloaded StemForge release asset directory before publication.
#
# The heavy archive inspection lives in scripts/release_assets.py (stdlib only);
# this entry point is POSIX sh so the publish job can run it with no Python
# environment beyond the system interpreter. It extracts the AppImage itself so
# the licence tree is checked against the bytes the user downloads.
#
# Usage:
#   scripts/verify-release.sh --dir <assets> --tag v<version>
#       [--repo-root DIR] [--vendor-lock FILE] [--skip-sums]
#
# --skip-sums is the publish job's first pass: the draft carries a per-job
# SHA256SUMS that cannot cover every asset yet. The job then writes the final
# SHA256SUMS and runs the script again without the flag.
set -eu

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
repo_root=$(CDPATH='' cd -- "$script_dir/.." && pwd -P)
assets=""
tag=""
vendor_lock=""
skip_sums=0
no_innoextract=0

usage() {
    cat <<'EOF'
Usage: scripts/verify-release.sh --dir DIR --tag TAG [options]

  --dir DIR          directory containing the downloaded release assets
  --tag TAG          release tag, exactly v<stemforge.__version__>
  --repo-root DIR    checkout with packaging/release.toml (default: this repo)
  --vendor-lock FILE vendor.lock.json (default: <repo-root>/packaging/vendor.lock.json)
  --skip-sums        skip SHA256SUMS coverage/matching (publish's first pass)
  --no-innoextract   diagnostic: skip setup extraction for synthetic fixture sets
EOF
}

fail() {
    echo "verify-release: $*" >&2
    exit 1
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --dir)
            [ "$#" -ge 2 ] || fail "--dir needs a value"
            assets="$2"
            shift 2
            ;;
        --tag)
            [ "$#" -ge 2 ] || fail "--tag needs a value"
            tag="$2"
            shift 2
            ;;
        --repo-root)
            [ "$#" -ge 2 ] || fail "--repo-root needs a value"
            repo_root=$(CDPATH='' cd -- "$2" && pwd -P)
            shift 2
            ;;
        --vendor-lock)
            [ "$#" -ge 2 ] || fail "--vendor-lock needs a value"
            vendor_lock="$2"
            shift 2
            ;;
        --skip-sums)
            skip_sums=1
            shift
            ;;
        --no-innoextract)
            no_innoextract=1
            shift
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            usage >&2
            fail "unknown argument: $1"
            ;;
    esac
done

[ -n "$assets" ] || fail "--dir is required"
[ -n "$tag" ] || fail "--tag is required"
[ -d "$assets" ] || fail "$assets is not a directory"
[ -f "$repo_root/packaging/release.toml" ] || fail "missing $repo_root/packaging/release.toml"
[ -f "$repo_root/scripts/generate_notices.py" ] || fail "missing the notices generator"
[ -f "$repo_root/scripts/release_assets.py" ] || fail "missing scripts/release_assets.py"
command -v python3 >/dev/null 2>&1 || fail "python3 is required"

# Ticket 10 section 8: no placeholder may reach a tag. The canonical gate is
# the notices generator, so this is the same check CI's licensing job runs.
if ! python3 "$repo_root/scripts/generate_notices.py" --check-release --root "$repo_root"; then
    echo "FAIL  release.config         packaging/release.toml carries a placeholder"
    exit 1
fi

version=$(python3 "$repo_root/scripts/release_assets.py" version --repo-root "$repo_root") ||
    fail "cannot read the release version"
[ -n "$version" ] || fail "the release version is empty"

work=$(mktemp -d "${TMPDIR:-/tmp}/stemforge-release.XXXXXX")
# shellcheck disable=SC2329  # invoked by the trap below
cleanup() {
    rm -rf "$work"
}
trap cleanup EXIT HUP INT TERM

appimage="$assets/StemForge-$version-linux-x86_64.AppImage"
appimage_root=""
if [ -f "$appimage" ]; then
    appimage_abs=$(CDPATH='' cd -- "$(dirname -- "$appimage")" && pwd -P)/$(basename -- "$appimage")
    chmod +x "$appimage_abs" 2>/dev/null || true
    mkdir -p "$work/appimage"
    if ! (cd "$work/appimage" && "$appimage_abs" --appimage-extract >/dev/null 2>&1); then
        echo "FAIL  appimage.extract     --appimage-extract failed"
        exit 1
    fi
    appimage_root="$work/appimage/squashfs-root"
    [ -d "$appimage_root" ] || fail "the AppImage produced no squashfs-root"
fi

if [ -z "$vendor_lock" ]; then
    vendor_lock="$repo_root/packaging/vendor.lock.json"
fi
[ -f "$vendor_lock" ] || fail "missing $vendor_lock"

set -- verify --dir "$assets" --tag "$tag" --repo-root "$repo_root" \
    --vendor-lock "$vendor_lock"
if [ -n "$appimage_root" ]; then
    set -- "$@" --appimage-root "$appimage_root"
fi
if [ "$skip_sums" -eq 1 ]; then
    set -- "$@" --skip-sums
fi
if [ "$no_innoextract" -eq 1 ]; then
    set -- "$@" --no-innoextract
fi

set +e
python3 "$repo_root/scripts/release_assets.py" "$@"
status=$?
set -e
exit "$status"
