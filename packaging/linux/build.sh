#!/usr/bin/env bash
# Build the Linux release Bundle: PyInstaller onedir, the Bundle root, the two
# tarballs, and the thin zstd AppImage (tickets 10 §1–§5, 20, ADR 0010/0011).
#
# Run it from any checkout:  bash packaging/linux/build.sh
#
# The resulting tree is dist/StemForge-<ver>-linux-x86_64/ with vendor/ beside
# _internal/; packaging/linux/verify_bundle.py gates the layout, the licence
# tree, the torch trim, the vendor lock, and the size budgets.
#
# --allow-missing-licences is a local-layout debug switch for the interval before
# ticket 22 lands the licence tree. It must never be used by a release or CI
# build; without it a missing LICENSE, notice, source offer, or LICENSES/ fails.
set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
cd "$repo_root"

dist_root="$repo_root/dist"
work="$repo_root/build/pyinstaller"
reports="$repo_root/build/reports"
tools="$repo_root/build/tools"
allow_missing_licences=0
source_date_epoch="${SOURCE_DATE_EPOCH:-315532800}"

usage() {
    cat <<'EOF'
Usage: packaging/linux/build.sh [--allow-missing-licences]

  --allow-missing-licences  debug only: assemble a local bundle before ticket 22
                            supplies LICENSE/THIRD_PARTY_NOTICES.md/SOURCE_OFFER.md/
                            LICENSES. Never use for a release or CI build.
EOF
}

while (($# > 0)); do
    case "$1" in
        --allow-missing-licences) allow_missing_licences=1 ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            echo "build.sh: unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
    shift
done

die() {
    echo "build.sh: $*" >&2
    exit 1
}

note() {
    echo "build.sh: $*"
}

export SOURCE_DATE_EPOCH="$source_date_epoch"
if ((allow_missing_licences)); then
    echo "build.sh: WARNING: debug bundle without the licence tree (ticket 22 pending);" \
        "never publish it" >&2
fi

note "syncing the build environment"
uv sync --frozen --group build

version="$(.venv/bin/python -c 'import stemforge; print(stemforge.__version__)')"
bundle_name="StemForge-${version}-linux-x86_64"
bundle="$dist_root/$bundle_name"
pyinstaller_out="$dist_root/StemForge"
tarball="$dist_root/$bundle_name.tar.gz"
zstdball="$dist_root/$bundle_name.tar.zst"
appimage="$dist_root/$bundle_name.AppImage"

# Ticket 10 §1: a PyPI torch fallback silently pulls CUDA wheels.
.venv/bin/python - <<'PY'
import importlib.util
import sys

if importlib.util.find_spec("torch") is None:
    sys.exit("build.sh: torch is not installed; the release bundle needs it")
import torch

if not torch.__version__.endswith("+cpu"):
    sys.exit(f"build.sh: torch {torch.__version__} is not a +cpu build")
PY

note "building $bundle_name with PyInstaller"
rm -rf "$pyinstaller_out" "$work" "$reports" "$bundle"
mkdir -p "$reports"
.venv/bin/python -m PyInstaller \
    packaging/pyinstaller/stemforge_linux.spec \
    --noconfirm \
    --clean \
    --distpath "$dist_root" \
    --workpath "$work"

[[ -x "$pyinstaller_out/StemForge" ]] || die "PyInstaller did not produce $pyinstaller_out"
mv "$pyinstaller_out" "$bundle"

# The PyInstaller payload keeps debug symbols in several wheels (sphn is 82 MB
# unstripped, libtorch_cpu 90 MB larger than its stripped form). Stripping is
# what keeps the onedir inside the 1.15 GB budget; --smoke-separation validates
# that the inference stack still loads afterwards. vendor/ is never touched:
# its hashes are fixed by packaging/vendor.lock.json.
if command -v strip >/dev/null 2>&1 && command -v file >/dev/null 2>&1; then
    note "stripping unstripped ELF shared objects in _internal/"
    find "$bundle/_internal" -type f \( -name '*.so' -o -name '*.so.*' \) -print0 |
        while IFS= read -r -d '' library; do
            # `strip` rewrites the OpenBLAS load segments and leaves them
            # unaligned for the glibc 2.35 loader (`ELF load command
            # address/offset not page-aligned`). Its symbols are worth ~1.4 MB;
            # keep the library untouched and inside the budget.
            case "$(basename -- "$library")" in
                libscipy_openblas* | libopenblas*) continue ;;
            esac
            if file -b -- "$library" | grep -q 'not stripped'; then
                strip --strip-unneeded -- "$library"
            fi
        done
fi

note "copying the pinned vendor tree beside _internal/ (ADR 0010)"
[[ -d "$repo_root/vendor" ]] || die "vendor/ is missing; run scripts/vendor-fetch.sh"
scripts/vendor-fetch.sh --verify-only
mkdir -p "$bundle/vendor"
tar -C "$repo_root/vendor" --exclude=.cache -cf - . | tar -C "$bundle/vendor" -xf -

install -m 0755 packaging/linux/run.sh "$bundle/run.sh"
install -m 0644 packaging/linux/README.md "$bundle/README.md"
# Ticket 10's layout spells the file README.txt; ship both names so the file
# manager and the ticket's checks agree.
install -m 0644 packaging/linux/README.md "$bundle/README.txt"
for readme in "$bundle/README.md" "$bundle/README.txt"; do
    sed -i "s/{{version}}/${version}/g" "$readme"
done

missing_licences=0
for name in LICENSE THIRD_PARTY_NOTICES.md SOURCE_OFFER.md; do
    if [[ -f "$repo_root/$name" ]]; then
        install -m 0644 "$repo_root/$name" "$bundle/$name"
    else
        missing_licences=1
    fi
done
if [[ -d "$repo_root/LICENSES" ]]; then
    cp -a "$repo_root/LICENSES" "$bundle/LICENSES"
else
    missing_licences=1
fi
if ((missing_licences)); then
    if ((allow_missing_licences)); then
        echo "build.sh: WARNING: licence tree incomplete (ticket 22); debug build only" >&2
    else
        die "licence tree is incomplete; add LICENSE, THIRD_PARTY_NOTICES.md," \
            "SOURCE_OFFER.md, LICENSES/ (ticket 22) or use --allow-missing-licences" \
            "for a local layout check only"
    fi
fi

command -v objdump >/dev/null 2>&1 ||
    die "objdump (binutils) is required for the host ABI gate"
verify_args=(--bundle "$bundle" --lock "$repo_root/packaging/vendor.lock.json")
if ((allow_missing_licences)); then
    verify_args+=(--allow-missing-licences)
fi
note "verifying the assembled Bundle before compressing it"
.venv/bin/python packaging/linux/verify_bundle.py "${verify_args[@]}"

note "building the release archives"
rm -f "$tarball" "$zstdball" "$appimage"
tar_options=(
    --sort=name
    --mtime="@${SOURCE_DATE_EPOCH}"
    --owner=0
    --group=0
    --numeric-owner
    --format=gnu
)
tar -C "$dist_root" "${tar_options[@]}" -cf - "$bundle_name" | gzip -9n >"$tarball"
tar -C "$dist_root" "${tar_options[@]}" -cf - "$bundle_name" |
    zstd --ultra -22 -T0 --no-progress -q -o "$zstdball"

note "building the thin zstd AppImage (ADR 0011)"
appimage_lock="$repo_root/packaging/linux/appimage.lock.json"
[[ -f "$appimage_lock" ]] || die "missing $appimage_lock"
read -r appimage_url appimage_sha appimage_size runtime_url runtime_sha runtime_size < <(
    .venv/bin/python - "$appimage_lock" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    record = json.load(handle)
runtime = record["runtime"]
print(
    record["url"],
    record["sha256"],
    record["size"],
    runtime["url"],
    runtime["sha256"],
    runtime["size"],
)
PY
)

fetch_pinned() {
    local url="$1" sha256="$2" size="$3" destination="$4"
    if [[ -f "$destination" ]] && .venv/bin/python - "$destination" "$sha256" "$size" <<'PY'
import hashlib
import os
import sys

path, digest, size = sys.argv[1], sys.argv[2], int(sys.argv[3])
try:
    if os.path.getsize(path) != size:
        raise SystemExit(1)
    checksum = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            checksum.update(chunk)
except OSError:
    raise SystemExit(1)
raise SystemExit(0 if checksum.hexdigest() == digest else 1)
PY
    then
        return 0
    fi
    note "downloading $(basename "$destination")"
    local temporary="$destination.download"
    rm -f "$temporary"
    curl -fL --retry 3 --connect-timeout 20 --max-time 300 -o "$temporary" "$url"
    if ! .venv/bin/python - "$temporary" "$sha256" "$size" <<'PY'
import hashlib
import os
import sys

path, digest, size = sys.argv[1], sys.argv[2], int(sys.argv[3])
if os.path.getsize(path) != size:
    raise SystemExit(1)
checksum = hashlib.sha256()
with open(path, "rb") as handle:
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        checksum.update(chunk)
raise SystemExit(0 if checksum.hexdigest() == digest else 1)
PY
    then
        rm -f "$temporary"
        die "$(basename "$destination") does not match appimage.lock.json"
    fi
    mv "$temporary" "$destination"
}

tool="$tools/appimagetool-x86_64.AppImage"
runtime_tool="$tools/runtime-x86_64"
mkdir -p "$tools"
fetch_pinned "$appimage_url" "$appimage_sha" "$appimage_size" "$tool"
fetch_pinned "$runtime_url" "$runtime_sha" "$runtime_size" "$runtime_tool"
chmod +x "$tool"

appdir_root="$repo_root/build/appimage"
appdir="$appdir_root/AppDir"
rm -rf "$appdir_root"
mkdir -p \
    "$appdir/usr/bin" \
    "$appdir/usr/lib" \
    "$appdir/usr/share/applications" \
    "$appdir/usr/share/doc/stemforge" \
    "$appdir/usr/share/icons/hicolor/scalable/apps" \
    "$appdir/usr/share/icons/hicolor/512x512/apps"
# One payload copy: the AppDir carries the same onedir tree as the tarball.
cp -a "$bundle" "$appdir/usr/lib/stemforge"
install -m 0755 packaging/linux/AppRun "$appdir/AppRun"
ln -s ../lib/stemforge/run.sh "$appdir/usr/bin/stemforge"
install -m 0644 packaging/linux/stemforge.desktop \
    "$appdir/usr/share/applications/stemforge.desktop"
# appimagetool 1.9.1 looks for the desktop file and the icon at the AppDir root;
# the usr/share copies are what the desktop integration consumes after mounting.
install -m 0644 packaging/linux/stemforge.desktop "$appdir/stemforge.desktop"
install -m 0644 src/stemforge/assets/stemforge.svg \
    "$appdir/usr/share/icons/hicolor/scalable/apps/stemforge.svg"
install -m 0644 src/stemforge/assets/stemforge-512.png \
    "$appdir/usr/share/icons/hicolor/512x512/apps/stemforge.png"
ln -s usr/share/icons/hicolor/scalable/apps/stemforge.svg "$appdir/stemforge.svg"
ln -s usr/share/icons/hicolor/512x512/apps/stemforge.png "$appdir/stemforge.png"
ln -s stemforge.svg "$appdir/.DirIcon"
for name in LICENSE THIRD_PARTY_NOTICES.md SOURCE_OFFER.md; do
    if [[ -f "$bundle/$name" ]]; then
        install -m 0644 "$bundle/$name" "$appdir/usr/share/doc/stemforge/$name"
    fi
done
if [[ -d "$bundle/LICENSES" ]]; then
    cp -a "$bundle/LICENSES" "$appdir/usr/share/doc/stemforge/LICENSES"
fi

(
    cd "$appdir_root"
    ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 SOURCE_DATE_EPOCH="$SOURCE_DATE_EPOCH" \
        "$tool" \
        --runtime-file "$runtime_tool" \
        --comp zstd \
        --mksquashfs-opt=-Xcompression-level \
        --mksquashfs-opt=22 \
        --mksquashfs-opt=-b \
        --mksquashfs-opt=1M \
        --mksquashfs-opt=-no-xattrs \
        --no-appstream \
        "$appdir" "$appimage"
)
[[ -s "$appimage" ]] || die "appimagetool did not produce $appimage"

note "verifying the Bundle and every archive"
full_verify=(
    .venv/bin/python packaging/linux/verify_bundle.py
    --bundle "$bundle"
    --lock "$repo_root/packaging/vendor.lock.json"
    --archive "$tarball"
    --archive "$zstdball"
    --archive "$appimage"
)
if ((allow_missing_licences)); then
    full_verify+=(--allow-missing-licences)
fi
"${full_verify[@]}"
"${full_verify[@]}" --json >"$reports/bundle-verify.json"

echo
du -sh "$bundle"
ls -lh "$tarball" "$zstdball" "$appimage"
note "done in ${SECONDS}s"
