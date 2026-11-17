#!/bin/bash
set -euo pipefail
exec "$(cd "$(dirname "$0")" && pwd)/deploy.sh" --start-only "$@"
