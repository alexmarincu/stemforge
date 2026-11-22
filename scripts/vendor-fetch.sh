#!/usr/bin/env bash
# Fetch, verify, and build the pinned vendor set.
#
# Inputs:  packaging/vendor.lock.json (the only pin list)
# Output:  vendor/{ffmpeg,deno,rubberband}/...
#
# The script is idempotent: a complete, version-matching vendor tree is left
# alone unless --force is given. Every download is checked against the lock's
# SHA-256 before it is used, source builds happen in a temporary directory, and
# vendor/ is only written after every new file has been verified. A failure
# leaves the installed vendor tree untouched.
set -Eeuo pipefail
umask 022

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
lock="$repo_root/packaging/vendor.lock.json"
vendor="$repo_root/vendor"
cache="$vendor/.cache"
target="${STEMFORGE_VENDOR_TARGET:-linux-x86_64}"

verify_only=0
force=0
jobs="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)"

usage() {
    cat <<'EOF'
Usage: scripts/vendor-fetch.sh [--verify-only] [--force] [--jobs N]

  --verify-only  check the installed vendor tree against the lock; never
                 download or build (the CI verification step)
  --force        rebuild and replace the vendor tree even when it verifies
  --jobs N       parallel build jobs (default: online CPUs)

Environment: STEMFORGE_VENDOR_TARGET=linux-x86_64 (the only supported target).
EOF
}

while (($# > 0)); do
    case "$1" in
        --verify-only) verify_only=1 ;;
        --force) force=1 ;;
        --jobs)
            [[ $# -ge 2 ]] || { echo "vendor-fetch: --jobs needs a value" >&2; exit 2; }
            jobs="$2"
            shift
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            echo "vendor-fetch: unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
    shift
done

die() {
    echo "vendor-fetch: $*" >&2
    exit 1
}

note() {
    echo "vendor-fetch: $*"
}

[[ -f "$lock" ]] || die "missing $lock"
[[ "$target" == "linux-x86_64" ]] || die "unsupported target '$target' (expected linux-x86_64)"
command -v python3 >/dev/null 2>&1 || die "python3 is required to read $lock"

# One line of component metadata, then one line per installed file. Tabs are
# the field separator; no value in the lock contains a tab.
lock_rows() {
    local component="$1"
    python3 - "$lock" "$target" "$component" <<'PY'
import json
import sys

lock_path, target, component = sys.argv[1], sys.argv[2], sys.argv[3]
with open(lock_path, encoding="utf-8") as handle:
    payload = json.load(handle)
record = payload["components"][component]
entry = record["targets"][target]
source = entry.get("source", entry)
build = entry.get("build", {})
provenance = record.get("provenance", {})
print(
    "|".join(
        [
            str(record["version"]),
            str(record["license"]),
            str(record.get("version_flag") or ""),
            str(entry["kind"]),
            str(source.get("url") or ""),
            str(source.get("filename") or ""),
            str(source.get("sha256") or ""),
            str(source.get("size") or ""),
            str(build.get("archive_format") or entry.get("archive_format") or ""),
            str(provenance.get("project") or ""),
            str(provenance.get("homepage") or ""),
        ]
    )
)
for item in entry["install"]:
    print(
        "|".join(
            [
                "install",
                str(item["from"]),
                str(item["to"]),
                "1" if item.get("executable") else "0",
                str(item.get("sha256") or ""),
            ]
        )
    )
PY
}

sha256_of() {
    sha256sum "$1" | cut -d' ' -f1
}

require_sha() {
    local value="$1" label="$2"
    [[ "$value" =~ ^[0-9a-f]{64}$ ]] || die "$label has no valid SHA-256 in $lock"
}

require_tools() {
    local tool
    for tool in "$@"; do
        command -v "$tool" >/dev/null 2>&1 || die "$tool is required; install it and retry"
    done
}

ensure_archive() {
    local url="$1" filename="$2" sha="$3" size="$4"
    local destination="$cache/$filename" actual

    require_sha "$sha" "$filename"
    [[ "$url" == https://* ]] || die "$filename has no HTTPS URL in $lock"
    mkdir -p "$cache"

    if [[ -f "$destination" ]]; then
        actual="$(sha256_of "$destination")"
        if [[ "$actual" != "$sha" ]]; then
            die "cached $filename does not match the lock (expected $sha, got $actual); delete $destination and retry"
        fi
        note "cached  $filename $(stat -c '%s' "$destination") bytes sha256=$actual"
        return 0
    fi

    local partial="$cache/.$filename.part.$$"
    note "fetch   $url"
    curl --fail --location --silent --show-error \
        --retry 3 --retry-delay 2 --connect-timeout 30 \
        --output "$partial" "$url" || {
        rm -f "$partial"
        die "download failed: $url"
    }
    actual="$(sha256_of "$partial")"
    if [[ "$actual" != "$sha" ]]; then
        rm -f "$partial"
        die "SHA-256 mismatch for $filename (expected $sha, got $actual); refusing to use it"
    fi
    local actual_size
    actual_size="$(stat -c '%s' "$partial")"
    if [[ -n "$size" && "$actual_size" != "$size" ]]; then
        rm -f "$partial"
        die "size mismatch for $filename (expected $size, got $actual_size)"
    fi
    mv -f "$partial" "$destination"
    note "verified $filename $actual_size bytes sha256=$actual"
}

extract_archive() {
    local archive="$1" format="$2" destination="$3" strip="${4:-0}"
    mkdir -p "$destination"
    case "$format" in
        tar.xz) tar -xJf "$archive" -C "$destination" --strip-components="$strip" ;;
        tar.gz) tar -xzf "$archive" -C "$destination" --strip-components="$strip" ;;
        zip) unzip -q "$archive" -d "$destination" ;;
        *) die "unsupported archive format: $format" ;;
    esac
}

find_member() {
    local root="$1" member="$2" found
    found="$(find "$root" -type f -path "*/$member" -print -quit)"
    if [[ -z "$found" && -f "$root/$member" ]]; then
        found="$root/$member"
    fi
    [[ -n "$found" ]] || die "archive does not contain $member"
    printf '%s\n' "$found"
}

stage_file() {
    local source="$1" relative="$2" executable="$3"
    local destination="$work/stage/$relative"
    install -d "$(dirname "$destination")"
    install -m 0755 "$source" "$destination"
    if [[ "$executable" != "1" ]]; then
        chmod 0644 "$destination"
    fi
}

finalize_tree() {
    local relative source mode destination temporary
    while IFS= read -r relative; do
        [[ -n "$relative" ]] || continue
        source="$work/stage/$relative"
        [[ -f "$source" ]] || die "staged file disappeared: $relative"
        destination="$vendor/$relative"
        install -d "$(dirname "$destination")"
        if [[ -x "$source" ]]; then
            mode=0755
        else
            mode=0644
        fi
        temporary="$(dirname "$destination")/.$(basename "$relative").new.$$"
        install -m "$mode" "$source" "$temporary"
        mv -f "$temporary" "$destination"
    done <"$work/stage-files.txt"
}

version_line() {
    local path="$1" flag="$2" output
    output="$(env -u LD_LIBRARY_PATH "$path" "$flag" 2>&1)" || return 1
    printf '%s\n' "${output%%$'\n'*}"
}

verify_version() {
    local tool="$1" path="$2" flag="$3" expected="$4" output
    [[ -x "$path" ]] || die "not executable: ${path#"$vendor"/}"
    output="$(version_line "$path" "$flag")" || die "--version failed: $tool"
    case "$tool" in
        ffmpeg | ffprobe) [[ "$output" == *"${expected#n}"* ]] || die "$tool version mismatch: expected $expected, got '$output'" ;;
        rubberband) [[ "$output" == "$expected" || "$output" == "Rubber Band $expected" ]] || die "$tool version mismatch: expected $expected, got '$output'" ;;
        deno) [[ "$output" == "deno $expected "* ]] || die "$tool version mismatch: expected $expected, got '$output'" ;;
        *) die "unknown versioned tool: $tool" ;;
    esac
}

tree_complete() {
    local root="$1" component row _tag _from to executable _sha path
    local -a rows=()
    for component in ffmpeg ffprobe deno libsndfile rubberband; do
        mapfile -t rows < <(lock_rows "$component")
        for row in "${rows[@]:1}"; do
            IFS='|' read -r _tag _from to executable _sha <<<"$row"
            path="$root/$to"
            [[ -f "$path" ]] || return 1
            if [[ "$executable" == "1" && ! -x "$path" ]]; then
                return 1
            fi
        done
    done
    return 0
}

verify_tree() {
    local root="$1"
    local checked=0 component row _from to executable expected_sha path actual
    local -a rows=()
    local -A expected_versions=()

    for component in ffmpeg ffprobe deno libsndfile rubberband; do
        mapfile -t rows < <(lock_rows "$component")
        ((${#rows[@]} >= 2)) || die "$component has no install entries in $lock"
        IFS='|' read -r version _license _version_flag _kind _url _filename _sha _size _format _project _homepage <<<"${rows[0]}"
        expected_versions["$component"]="$version"

        for row in "${rows[@]:1}"; do
            IFS='|' read -r _tag _from to executable expected_sha <<<"$row"
            path="$root/$to"
            checked=$((checked + 1))
            [[ -f "$path" ]] || die "missing: vendor/$to"
            if [[ "$executable" == "1" ]]; then
                [[ -x "$path" ]] || die "not executable: vendor/$to"
            fi
            if [[ -n "$expected_sha" ]]; then
                actual="$(sha256_of "$path")"
                [[ "$actual" == "$expected_sha" ]] || die "SHA-256 mismatch: vendor/$to (expected $expected_sha, got $actual)"
            fi
        done
    done

    local library="$root/rubberband/libsndfile.so.1"
    [[ -f "$library" ]] || die "missing: vendor/rubberband/libsndfile.so.1"

    verify_version ffmpeg "$root/ffmpeg/ffmpeg" -version "${expected_versions[ffmpeg]}"
    verify_version ffprobe "$root/ffmpeg/ffprobe" -version "${expected_versions[ffprobe]}"
    verify_version deno "$root/deno/deno" --version "${expected_versions[deno]}"
    verify_version rubberband "$root/rubberband/rubberband" --version "${expected_versions[rubberband]}"

    ((${#expected_versions[@]} == 5)) || die "the lock does not pin all five components"
    note "verified $checked files in $root"
}

print_summary() {
    local component row version license kind url filename sha size _from to _executable _file_sha path actual project
    local -a rows=()
    echo
    echo "Vendor set — provenance and licences"
    echo "================================================"
    for component in ffmpeg ffprobe deno libsndfile rubberband; do
        mapfile -t rows < <(lock_rows "$component")
        IFS='|' read -r version license _flag kind url filename sha size _format project _homepage <<<"${rows[0]}"
        printf '\n%s %s — %s\n' "$project" "$version" "$license"
        printf '  %s: %s\n' "$([[ "$kind" == "built" ]] && echo "built from" || echo "archive")" "$filename"
        printf '  url:    %s\n' "$url"
        printf '  sha256: %s\n' "$sha"
        for row in "${rows[@]:1}"; do
            IFS='|' read -r _tag _from to _executable _file_sha <<<"$row"
            path="$vendor/$to"
            actual="$(sha256_of "$path")"
            printf '  vendor/%s  %s bytes  sha256=%s\n' "$to" "$(stat -c '%s' "$path")" "$actual"
        done
    done
    echo
    echo "Source offer: GPL-3.0-only section 6(b), valid for three years."
    echo "  contact:  https://github.com/alexmarincu/stemforge/issues"
    echo "  releases: https://github.com/alexmarincu/stemforge/releases"
    echo "  inputs:   packaging/vendor/SOURCES.md"
    echo "No model weights are present in vendor/."
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if ((verify_only == 0)) && ((force == 0)); then
    if tree_complete "$vendor"; then
        note "vendor tree is complete; verifying it (use --force to rebuild)"
        verify_tree "$vendor"
        print_summary
        exit 0
    fi
fi

if ((verify_only == 1)); then
    [[ -d "$vendor" ]] || die "vendor/ is missing; run scripts/vendor-fetch.sh first"
    verify_tree "$vendor"
    print_summary
    exit 0
fi

require_tools curl tar unzip xz cmake meson ninja pkg-config strip

work="$(mktemp -d "${TMPDIR:-/tmp}/stemforge-vendor.XXXXXX")"
cleanup() {
    rm -rf "$work"
}
trap cleanup EXIT HUP INT TERM

mkdir -p "$work/stage"
: >"$work/stage-files.txt"

declare -A extracted=()
SECONDS=0

for component in ffmpeg ffprobe deno libsndfile rubberband; do
    component_started=$SECONDS
    mapfile -t rows < <(lock_rows "$component")
    ((${#rows[@]} >= 2)) || die "$component has no install entries in $lock"
    IFS='|' read -r version license _version_flag kind url filename sha size format project _homepage <<<"${rows[0]}"
    note "$component $version — $license ($kind)"

    archive_dir=""
    case "$component" in
        ffmpeg | ffprobe)
            ensure_archive "$url" "$filename" "$sha" "$size"
            if [[ -z "${extracted[$sha]:-}" ]]; then
                archive_dir="$work/archive-$sha"
                extract_archive "$cache/$filename" "$format" "$archive_dir" 0
                extracted["$sha"]="$archive_dir"
            else
                archive_dir="${extracted[$sha]}"
            fi
            ;;
        deno)
            ensure_archive "$url" "$filename" "$sha" "$size"
            if [[ -z "${extracted[$sha]:-}" ]]; then
                archive_dir="$work/archive-$sha"
                extract_archive "$cache/$filename" "$format" "$archive_dir" 0
                extracted["$sha"]="$archive_dir"
            else
                archive_dir="${extracted[$sha]}"
            fi
            ;;
        libsndfile)
            ensure_archive "$url" "$filename" "$sha" "$size"
            if [[ -z "${extracted[$sha]:-}" ]]; then
                archive_dir="$work/libsndfile-src"
                extract_archive "$cache/$filename" "$format" "$archive_dir" 1
                extracted["$sha"]="$archive_dir"

                sf_build="$work/libsndfile-build"
                sf_prefix="$work/libsndfile-prefix"
                cmake -S "$archive_dir" -B "$sf_build" \
                    -DCMAKE_BUILD_TYPE=Release \
                    -DCMAKE_INSTALL_PREFIX="$sf_prefix" \
                    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
                    -DBUILD_SHARED_LIBS=ON \
                    -DBUILD_PROGRAMS=OFF \
                    -DBUILD_EXAMPLES=OFF \
                    -DBUILD_TESTING=OFF \
                    -DENABLE_EXTERNAL_LIBS=OFF \
                    -DENABLE_MPEG=OFF \
                    -DENABLE_CPACK=OFF \
                    -DINSTALL_PKGCONFIG_MODULE=ON \
                    -DINSTALL_MANPAGES=OFF >/dev/null
                cmake --build "$sf_build" --parallel "$jobs" >/dev/null
                cmake --install "$sf_build" >/dev/null

                versioned="$(find "$sf_prefix/lib" -maxdepth 1 -type f -name 'libsndfile.so.1.*' -print -quit)"
                if [[ -z "$versioned" ]]; then
                    versioned="$(readlink -f "$sf_prefix/lib/libsndfile.so.1")"
                fi
                [[ -n "$versioned" && -f "$versioned" ]] || die "libsndfile did not produce libsndfile.so.1"
                install -m 0755 "$versioned" "$work/libsndfile.so.1"
                strip --strip-unneeded "$work/libsndfile.so.1"
                extracted["libsndfile.so.1"]="$work/libsndfile.so.1"
            else
                archive_dir="${extracted[$sha]}"
            fi
            ;;
        rubberband)
            ensure_archive "$url" "$filename" "$sha" "$size"
            if [[ -z "${extracted[$sha]:-}" ]]; then
                archive_dir="$work/rubberband-src"
                extract_archive "$cache/$filename" "$format" "$archive_dir" 1
                extracted["$sha"]="$archive_dir"

                rb_build="$work/rubberband-build"
                sf_prefix="$work/libsndfile-prefix"
                [[ -d "$sf_prefix/lib/pkgconfig" ]] || die "libsndfile must build before Rubber Band"
                PKG_CONFIG_PATH="$sf_prefix/lib/pkgconfig" meson setup "$rb_build" "$archive_dir" \
                    --buildtype=release \
                    --default-library=static \
                    -Dfft=builtin \
                    -Dresampler=builtin \
                    -Dvamp=disabled \
                    -Dlv2=disabled \
                    -Dladspa=disabled \
                    -Djni=disabled \
                    -Dcmdline=enabled \
                    -Dtests=disabled \
                    "-Dcpp_link_args=-Wl,-rpath,\$ORIGIN" \
                    "-Dc_link_args=-Wl,-rpath,\$ORIGIN" >/dev/null
                ninja -C "$rb_build" -j "$jobs" >/dev/null
                install -m 0755 "$rb_build/rubberband" "$work/rubberband"
                strip --strip-unneeded "$work/rubberband"
                if command -v patchelf >/dev/null 2>&1; then
                    patchelf --set-rpath "\$ORIGIN" "$work/rubberband"
                else
                    note "patchelf is absent; keeping the linker's \$ORIGIN rpath (it is first)"
                fi
                extracted["rubberband"]="$work/rubberband"
            else
                archive_dir="${extracted[$sha]}"
            fi
            ;;
        *) die "unknown component: $component" ;;
    esac

    for row in "${rows[@]:1}"; do
        IFS='|' read -r _tag from to executable _expected_sha <<<"$row"
        install -d "$(dirname "$work/stage/$to")"
        case "$component" in
            ffmpeg | ffprobe | deno)
                source_file="$(find_member "$archive_dir" "$from")"
                stage_file "$source_file" "$to" "$executable"
                ;;
            libsndfile)
                stage_file "$work/libsndfile.so.1" "$to" "$executable"
                ;;
            rubberband)
                stage_file "$work/rubberband" "$to" "$executable"
                ;;
            *) die "unknown component: $component" ;;
        esac
        printf '%s\n' "$to" >>"$work/stage-files.txt"
    done
    note "$component done in $((SECONDS - component_started))s"
done

# Verify the staged files exactly as the installed tree will be verified, then
# replace the vendor files atomically. Nothing under vendor/ changes before this
# point.
verify_tree "$work/stage"
finalize_tree
verify_tree "$vendor"

# Keep the build record beside the binaries for the release job's BUILDINFO and
# the source offer. vendor/ is git-ignored.
{
    echo "StemForge vendor set — generated by scripts/vendor-fetch.sh"
    echo "date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "target: $target"
    echo "lock: packaging/vendor.lock.json"
    echo
    for component in ffmpeg ffprobe deno libsndfile rubberband; do
        mapfile -t rows < <(lock_rows "$component")
        IFS='|' read -r version license _flag kind url filename sha size _format project homepage <<<"${rows[0]}"
        echo "$project $version ($license)"
        echo "  obtained: $filename $url"
        echo "  archive sha256: $sha"
        echo "  homepage: $homepage"
        for row in "${rows[@]:1}"; do
            IFS='|' read -r _tag from to executable _file_sha <<<"$row"
            echo "  installed: vendor/$to sha256=$(sha256_of "$vendor/$to")"
        done
        echo
    done
    echo "Source offer: GPL-3.0-only section 6(b), three years,"
    echo "https://github.com/alexmarincu/stemforge/issues"
} >"$vendor/PROVENANCE.txt"

print_summary
note "wrote $vendor/PROVENANCE.txt"
note "finished in ${SECONDS}s"
