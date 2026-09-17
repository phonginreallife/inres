#!/usr/bin/env bash
#
# Assemble the GitHub Pages site from the OpenWiki output.
#
# OpenWiki owns openwiki/ and writes plain OKF markdown there. Jekyll needs
# just-the-docs navigation front matter, so rather than committing two copies of
# every page this script derives docs/wiki/ from openwiki/ at build time.
# docs/wiki/ is generated and gitignored - never edit it by hand.
#
# Usage: scripts/build-docs.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/openwiki"
DEST="$ROOT/docs/wiki"

if [[ ! -d "$SRC" ]]; then
    echo "error: $SRC not found - run OpenWiki first" >&2
    exit 1
fi

rm -rf "$DEST"
mkdir -p "$DEST"

# Section titles and their order in the sidebar. Anything not listed still gets
# published, just after these and alphabetically.
declare -a SECTION_ORDER=(
    architecture
    concepts
    workflows
    ai-agent
    frontend
    monitoring
    operations
    development
)

section_title() {
    case "$1" in
        ai-agent)    echo "AI Agent" ;;
        architecture) echo "Architecture" ;;
        concepts)    echo "Concepts" ;;
        development) echo "Development" ;;
        frontend)    echo "Frontend" ;;
        monitoring)  echo "Monitoring" ;;
        operations)  echo "Operations" ;;
        workflows)   echo "Workflows" ;;
        *)           echo "$1" ;;
    esac
}

# Read a scalar out of a markdown file's YAML front matter.
front_matter_value() {
    local file="$1" key="$2"
    awk -v key="$key" '
        NR == 1 && $0 == "---" { inside = 1; next }
        inside && $0 == "---"  { exit }
        inside {
            if (index($0, key ":") == 1) {
                sub("^" key ": *", "")
                gsub(/^"|"$/, "")
                print
                exit
            }
        }
    ' "$file"
}

# Strip the OpenWiki front matter, leaving the body.
strip_front_matter() {
    awk 'NR == 1 && $0 == "---" { inside = 1; next }
         inside && $0 == "---"  { inside = 0; next }
         !inside { print }' "$1"
}

# Rewrite intra-wiki links so they resolve as built pages rather than raw files:
#   ../ai-agent/foo.md  ->  ../ai-agent/foo.html
#   ./workflows/foo.md  ->  ./workflows/foo.html
rewrite_links() {
    sed -E 's/\(([^()]*)\.md(#[^()]*)?\)/(\1.html\2)/g'
}

emit_page() {
    local src="$1" dest="$2" parent="$3" nav_order="$4"
    local title description

    title="$(front_matter_value "$src" title)"
    description="$(front_matter_value "$src" description)"
    [[ -z "$title" ]] && title="$(basename "${src%.md}")"

    mkdir -p "$(dirname "$dest")"
    {
        echo "---"
        echo "title: \"${title//\"/\\\"}\""
        [[ -n "$parent" ]] && echo "parent: \"$parent\""
        echo "nav_order: $nav_order"
        [[ -n "$description" ]] && echo "description: \"${description//\"/\\\"}\""
        echo "---"
        echo
        strip_front_matter "$src" | rewrite_links
    } > "$dest"
}

echo "Building docs/wiki from openwiki/"

# --- Quickstart: top of the sidebar, no parent -------------------------------
if [[ -f "$SRC/quickstart.md" ]]; then
    emit_page "$SRC/quickstart.md" "$DEST/quickstart.md" "" 2
    echo "  quickstart"
fi

# --- Sections ----------------------------------------------------------------
build_section() {
    local dir="$1" order="$2"
    local name title
    name="$(basename "$dir")"
    title="$(section_title "$name")"

    # OpenWiki generates index.md per directory; just-the-docs wants a parent
    # page instead, so the index is replaced rather than published.
    mkdir -p "$DEST/$name"
    {
        echo "---"
        echo "title: \"$title\""
        echo "nav_order: $order"
        echo "has_children: true"
        echo "---"
        echo
        echo "# $title"
        echo
        echo "| Page | What it covers |"
        echo "|:--|:--|"
        local page page_title page_desc
        for page in "$dir"/*.md; do
            [[ -e "$page" ]] || continue
            [[ "$(basename "$page")" == "index.md" ]] && continue
            page_title="$(front_matter_value "$page" title)"
            page_desc="$(front_matter_value "$page" description)"
            echo "| [$page_title]($(basename "${page%.md}").html) | $page_desc |"
        done
    } > "$DEST/$name/index.md"

    local n=1 page
    for page in "$dir"/*.md; do
        [[ -e "$page" ]] || continue
        [[ "$(basename "$page")" == "index.md" ]] && continue
        emit_page "$page" "$DEST/$name/$(basename "$page")" "$title" "$n"
        n=$((n + 1))
    done
    echo "  $name ($((n - 1)) pages)"
}

order=10
for name in "${SECTION_ORDER[@]}"; do
    [[ -d "$SRC/$name" ]] || continue
    build_section "$SRC/$name" "$order"
    order=$((order + 1))
done

# Any section the list above does not know about.
for dir in "$SRC"/*/; do
    name="$(basename "$dir")"
    [[ "$name" == .* ]] && continue
    printf '%s\n' "${SECTION_ORDER[@]}" | grep -qx "$name" && continue
    build_section "$dir" "$order"
    order=$((order + 1))
done

echo "Done: $(find "$DEST" -name '*.md' | wc -l | tr -d ' ') pages in docs/wiki"
