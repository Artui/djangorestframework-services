#!/usr/bin/env bash
#
# release-publish.sh — single source of truth for the release flow.
#
# Phases:
#   prepare   Extract version, no-op if already released, run pytest, build dist,
#             extract CHANGELOG section, write release metadata under
#             .release-metadata/. (Metadata lives outside dist/ so
#             `pypa/gh-action-pypi-publish` doesn't try to upload it as a
#             distribution.) Emits `released=true|false` and `version=…` to
#             $GITHUB_OUTPUT when running under GitHub Actions.
#   finalize  Tag, push tag, and create a GitHub Release with the wheel + sdist
#             attached. Skipped automatically if prepare decided no release.
#   all       prepare → uv publish → finalize. Used for manual workstation
#             releases (the CI workflow does PyPI publish via the dedicated
#             OIDC action between prepare and finalize).
#
# Required env:
#   PACKAGE_NAME    Display name (PyPI name). Used in logs only.
#   VERSION_FILES   Newline-separated `path|awk-extractor` pairs. The awk
#                   extractor is invoked as `awk -F '"' "/${pattern}/ {print \$2; exit}"`
#                   so it must select a line of the form `… = "X.Y.Z"`.
#                   All entries must agree on the version; the first wins for
#                   downstream use.
#
# Optional env:
#   DRY_RUN=1       Skip side-effecting steps (uv publish, git push, gh release).
#                   Used for local rehearsal.
#   GH_TOKEN        Required for `finalize` outside DRY_RUN (gh CLI auth).

set -euo pipefail

phase="${1:-}"
if [[ -z "$phase" ]]; then
    echo "usage: $0 <prepare|finalize|all>" >&2
    exit 2
fi

: "${PACKAGE_NAME:?PACKAGE_NAME must be set}"
: "${VERSION_FILES:?VERSION_FILES must be set}"

log() { echo "[release-publish:${phase}] $*"; }

extract_versions() {
    local first_version="" current_version=""
    local entry path pattern
    while IFS= read -r entry; do
        [[ -z "$entry" ]] && continue
        path="${entry%%|*}"
        pattern="${entry#*|}"
        if [[ ! -f "$path" ]]; then
            echo "version source not found: $path" >&2
            exit 1
        fi
        current_version="$(awk -F '"' "/${pattern}/ { print \$2; exit }" "$path")"
        if [[ -z "$current_version" ]]; then
            echo "could not extract version from $path with pattern '$pattern'" >&2
            exit 1
        fi
        if [[ -z "$first_version" ]]; then
            first_version="$current_version"
        elif [[ "$current_version" != "$first_version" ]]; then
            echo "version drift: $path reports $current_version, expected $first_version" >&2
            exit 1
        fi
    done <<<"$VERSION_FILES"
    if [[ -z "$first_version" ]]; then
        echo "VERSION_FILES yielded no versions" >&2
        exit 1
    fi
    printf '%s' "$first_version"
}

emit_output() {
    local key="$1" value="$2"
    if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
        printf '%s=%s\n' "$key" "$value" >>"$GITHUB_OUTPUT"
    fi
}

tag_exists() {
    local tag="$1"
    if git rev-parse "$tag" >/dev/null 2>&1; then
        return 0
    fi
    if [[ -n "$(git ls-remote --tags origin "$tag" 2>/dev/null)" ]]; then
        return 0
    fi
    return 1
}

extract_changelog_section() {
    local version="$1" out_file="$2"
    # Pull the block between `## [<version>]` and the next `## [` heading.
    # Trims surrounding blank lines so the GitHub Release body is tight.
    awk -v ver="$version" '
        BEGIN { capture = 0 }
        /^## \[/ {
            if (capture) { exit }
            if (index($0, "[" ver "]") == 4) { capture = 1; next }
        }
        capture { print }
    ' CHANGELOG.md \
        | awk 'NF { found = 1 } found { lines[++n] = $0 }
               END { last = n; while (last > 0 && lines[last] ~ /^[[:space:]]*$/) last--;
                     for (i = 1; i <= last; i++) print lines[i] }' \
        >"$out_file"
    if [[ ! -s "$out_file" ]]; then
        echo "CHANGELOG.md has no [$version] section" >&2
        exit 1
    fi
}

do_prepare() {
    local version
    version="$(extract_versions)"
    log "package=$PACKAGE_NAME version=$version"

    if [[ -z "${GITHUB_ACTIONS:-}" ]]; then
        # Local dev: a stale `dist/` from a previous build can confuse the
        # publish step. CI runs on a fresh runner so this is unnecessary there.
        rm -rf dist .release-metadata
    fi

    git fetch --tags --quiet origin || true
    if tag_exists "v$version"; then
        log "v$version already released, nothing to publish"
        emit_output released false
        emit_output version "$version"
        return 0
    fi

    log "running final test gate"
    uv run pytest

    log "building distributions"
    uv build

    # Metadata lives outside dist/ — pypa/gh-action-pypi-publish uploads
    # every file in its packages-dir, so dist/ must contain only wheels
    # and sdists.
    mkdir -p .release-metadata
    extract_changelog_section "$version" .release-metadata/release-notes.md
    printf '%s\n' "$version" >.release-metadata/RELEASE_VERSION

    emit_output released true
    emit_output version "$version"
    log "prepared release v$version"
}

do_finalize() {
    if [[ ! -f .release-metadata/RELEASE_VERSION ]]; then
        log "no .release-metadata/RELEASE_VERSION — prepare did not run or short-circuited; nothing to finalize"
        return 0
    fi
    local version
    version="$(cat .release-metadata/RELEASE_VERSION)"
    log "finalizing v$version"

    if [[ "${DRY_RUN:-}" == "1" ]]; then
        log "DRY_RUN=1, skipping tag push and gh release create"
        return 0
    fi

    if tag_exists "v$version"; then
        log "tag v$version already exists, skipping push"
    else
        git config user.name 'github-actions[bot]'
        git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
        git tag -a "v$version" -m "$version"
        git push origin "v$version"
    fi

    if gh release view "v$version" >/dev/null 2>&1; then
        log "GitHub Release v$version already exists, skipping create"
    else
        gh release create "v$version" \
            --title "v$version" \
            --notes-file .release-metadata/release-notes.md \
            dist/*.whl dist/*.tar.gz
    fi
}

do_publish_pypi() {
    if [[ "${DRY_RUN:-}" == "1" ]]; then
        log "DRY_RUN=1, skipping uv publish"
        return 0
    fi
    log "uploading to PyPI"
    uv publish
}

case "$phase" in
    prepare)
        do_prepare
        ;;
    finalize)
        do_finalize
        ;;
    all)
        do_prepare
        if [[ -f .release-metadata/RELEASE_VERSION ]]; then
            do_publish_pypi
            do_finalize
        fi
        ;;
    *)
        echo "unknown phase: $phase (expected prepare|finalize|all)" >&2
        exit 2
        ;;
esac
