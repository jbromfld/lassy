#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$project_dir"

if ! command -v node >/dev/null || ! command -v npm >/dev/null; then
  echo "Node.js and npm are required to build the extension." >&2
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
printf '\nInstall explicitly with: ./install.sh\n'
