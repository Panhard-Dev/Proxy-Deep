#!/bin/sh
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  printf '%s\n' 'Python 3 é necessário para iniciar o proxy.' >&2
  exit 1
fi
exec python3 iniciar.py "$@"
