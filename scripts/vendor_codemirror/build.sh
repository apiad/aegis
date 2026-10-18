#!/usr/bin/env bash
# Rebuild src/aegis/client/vendor/codemirror from the pinned packages in
# package.json (and package-lock.json): one core.mjs, one lang-<name>.mjs per
# entry in entries/, and the chunks they share, as esbuild names them.
#
# Node and npm are needed only to run this script; aegis serves the built files
# and never needs either. Adding a language is one file in entries/ and one
# line in src/aegis/client/js/editor.js's map from extension to language.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
out="$repo/src/aegis/client/vendor/codemirror"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

cp "$here/package.json" "$work/"
[ -f "$here/package-lock.json" ] && cp "$here/package-lock.json" "$work/"
cp -r "$here/entries" "$work/entries"
cd "$work"
if [ -f package-lock.json ]; then npm ci --silent; else npm install --silent; fi
[ -f "$here/package-lock.json" ] || cp package-lock.json "$here/"

rm -rf "$out"
mkdir -p "$out"
npx esbuild entries/*.js --bundle --splitting --minify --format=esm \
  --out-extension:.js=.mjs --chunk-names='chunk-[hash]' --legal-comments=none \
  --outdir="$out" --log-level=warning

# Every package bundled, with its licence (all MIT at the pinned versions).
{
  echo "CodeMirror 6 and its dependencies, bundled by scripts/vendor_codemirror/build.sh."
  for dir in node_modules/@codemirror/* node_modules/@lezer/* node_modules/codemirror \
    node_modules/crelt node_modules/style-mod node_modules/w3c-keyname; do
    [ -f "$dir/LICENSE" ] || continue
    printf '\n==== %s %s ====\n\n' "${dir#node_modules/}" \
      "$(node -p "require('./$dir/package.json').version")"
    cat "$dir/LICENSE"
  done
} > "$out/LICENSE-codemirror.txt"
echo "$(ls "$out"/*.mjs | wc -l) modules, $(du -sk "$out" | cut -f1) KB in $out"
