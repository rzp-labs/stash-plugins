#!/bin/bash

# builds a repository of plugins
# outputs to _site with the following structure:
# index.yml
# <plugin_id>.zip
# Each zip file contains the files a plugin needs at runtime, taken from the
# committed tree (HEAD), so uncommitted changes and local caches never ship.

set -euo pipefail

outdir="${1:-_site}"

rm -rf "$outdir"
mkdir -p "$outdir"

# git pathspecs for a plugin's files that stay in the repo but never ship
excludes()
{
    local d=$1
    printf '%s\n' \
        ":(exclude,glob)$d/**/tests/**" \
        ":(exclude,glob)$d/**/samples/**" \
        ":(exclude,glob)$d/**/test_*.py" \
        ":(exclude,glob)$d/**/conftest.py" \
        ":(exclude,glob)$d/**/.coverage" \
        ":(exclude,glob)$d/**/.env*"
}

buildPlugin()
{
    f=$1

    if grep -q "^#pkgignore" "$f"; then
        return
    fi

    # get the plugin id from the manifest file name
    dir=$(dirname "$f")
    dir=${dir#./}
    plugin_id=$(basename "$f" .yml)

    echo "Processing $plugin_id"

    # The upstream MIT notice is part of every maintained plugin package.
    spec=("$dir" "LICENSE")
    while IFS= read -r exclude; do
        spec+=("$exclude")
    done < <(excludes "$dir")

    # version and date come from the last commit that touched shipped files,
    # so test-only commits don't offer users an update
    version=$(git log -n 1 --pretty=format:%h -- "${spec[@]}")
    updated=$(TZ=UTC0 git log -n 1 --date="format-local:%F %T" --pretty=format:%ad -- "${spec[@]}")
    epoch=$(git log -n 1 --pretty=format:%ct -- "${spec[@]}")

    # create the zip file from the committed tree with fixed timestamps,
    # permissions and file order, so it only changes when its content does
    zipfile="$(cd "$outdir" && pwd)/$plugin_id.zip"
    staging=$(mktemp -d)
    git archive --format=tar HEAD -- "${spec[@]}" | tar -x -C "$staging"
    mv "$staging/LICENSE" "$staging/$dir/LICENSE"
    chmod -R u=rwX,go=rX "$staging"
    # Python's timestamp API works on both macOS and the Linux Pages runner.
    python3 - "$staging" "$epoch" <<'PY'
import os
import sys

root, epoch = sys.argv[1], int(sys.argv[2])
for directory, dirs, files in os.walk(root):
    for name in dirs + files:
        os.utime(os.path.join(directory, name), (epoch, epoch), follow_symlinks=False)
os.utime(root, (epoch, epoch))
PY
    (cd "$staging/$dir" && find . -type f | LC_ALL=C sort | TZ=UTC0 zip -X -D -q "$zipfile" -@)
    rm -rf "$staging"

    name=$(grep "^name:" "$f" | head -n 1 | cut -d' ' -f2- | sed -e 's/\r//' -e 's/^"\(.*\)"$/\1/')
    description=$(grep "^description:" "$f" | head -n 1 | cut -d' ' -f2- | sed -e 's/\r//' -e 's/^"\(.*\)"$/\1/')
    ymlVersion=$(grep "^version:" "$f" | head -n 1 | cut -d' ' -f2- | sed -e 's/\r//' -e 's/^"\(.*\)"$/\1/')
    version="$ymlVersion-$version"
    dep=$(grep "^# requires:" "$f" | cut -c 12- | sed -e 's/\r//' || true)

    # write to spec index
    echo "- id: $plugin_id
  name: $name
  metadata:
    description: $description
  version: $version
  date: $updated
  path: $plugin_id.zip
  sha256: $(sha256sum "$zipfile" | cut -d' ' -f1)" >> "$outdir"/index.yml

    # handle dependencies
    if [ -n "$dep" ]; then
        echo "  requires:" >> "$outdir"/index.yml
        for d in ${dep//,/ }; do
            echo "    - $d" >> "$outdir"/index.yml
        done
    fi

    echo "" >> "$outdir"/index.yml
}

# This fork maintains only Tag Manager. Keep other plugins and their history
# in the source tree, but never advertise or package them in this feed.
for file in ./plugins/tagManager/tagManager.yml; do
    buildPlugin "$file"
done
