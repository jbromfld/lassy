#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$project_dir"

if ! command -v node >/dev/null || ! command -v npm >/dev/null || ! command -v code >/dev/null; then
  echo "Node.js, npm, and the VS Code 'code' command are required to build and install the extension." >&2
  exit 1
fi

if [[ ! -x node_modules/.bin/tsc || ! -x node_modules/.bin/vsce ]]; then
  npm ci --prefer-offline --no-audit --no-fund
fi

npm test

mkdir -p build
artifact_path="$project_dir/build/$(node -p 'const p = require("./package.json"); `${p.name}-${p.version}.vsix`')"
npm run package -- --out "$artifact_path"

printf '\nVSIX built: %s\n' "$artifact_path"
extension_id="$(node -p 'const p = require("./package.json"); `${p.publisher}.${p.name}`')"
installed_extensions="$(code --list-extensions)"
while IFS= read -r installed_id; do
  if [[ "$installed_id" == "$extension_id" ]]; then
    code --uninstall-extension "$extension_id"
    break
  fi
done <<< "$installed_extensions"
code --install-extension "$artifact_path" --force
printf '\nReload VS Code, then use @knowledge with the retrieval service running.\n'
printf 'Default endpoint: http://localhost:8000/query (kbsearch.connectionMode=direct).\n'
