#!/bin/sh
set -eu
image=$1
docker run --rm --entrypoint sh "$image" -ec 'test -x /app/bin/collector; node -p "require.resolve(\"@orenda/box-sdk\")" ; if command -v python || command -v python3; then exit 1; fi'
container=$(docker create "$image")
scratch=$(mktemp -d)
trap 'docker rm "$container" >/dev/null; rm -rf "$scratch"' EXIT
docker cp "$container:/app/bin/collector" "$scratch/collector"
npm ci --ignore-scripts
python3 -m venv "$scratch/venv"
"$scratch/venv/bin/pip" install --disable-pip-version-check -q -r tests/requirements.txt
OPCUA_COLLECTOR="$scratch/collector" "$scratch/venv/bin/python" -m unittest discover -s tests -t .
