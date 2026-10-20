#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
artifact="build/$(node -p 'const p = require("./package.json"); `${p.name}-${p.version}.vsix`')"
[[ -f "$artifact" ]] || { echo "Run ./build.sh first" >&2; exit 1; }
code --install-extension "$artifact" --force
