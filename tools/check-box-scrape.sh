#!/usr/bin/env bash
# Isolated CI topology: process data stays on the controller side of the scrape socket.
set -euo pipefail
image="$1"
run="opcua-check-$$"
tmp="$(mktemp -d)"
cleanup() {
  docker rm -f "$run-relay" "$run-prom" "$run-app" > /dev/null 2>&1 || true
}
trap cleanup EXIT
chmod 755 "$tmp"
mkdir "$tmp/scrape"
sudo chown 1000:1000 "$tmp/scrape"
cat > "$tmp/prom.yml" <<'YAML'
global:
  scrape_interval: 1s
scrape_configs:
  - job_name: orenda-opcua
    static_configs:
      - targets: ['127.0.0.1:9468']
YAML
docker run -d --name "$run-app" --network none \
  -v "$PWD/tools:/app/tools:ro" -v "$PWD/tests:/app/tests:ro" \
  -v "$tmp/scrape:/data/scrape" "$image" python -m tools.demo --data /data >/dev/null
for attempt in $(seq 1 40); do
  if docker exec "$run-app" python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:3100/health', timeout=2)" 2>/dev/null; then break; fi
  sleep 1
done
docker exec "$run-app" python -m tools.commission_fixture
docker run -d --name "$run-prom" --network none -v "$tmp/prom.yml:/etc/prometheus/prometheus.yml:ro" prom/prometheus:v2.54.1 >/dev/null
docker run -d --name "$run-relay" --network "container:$run-prom" --read-only --cap-drop ALL \
  -v "$tmp/scrape:/scrape:ro" "$image" python -m opcua_box.relay >/dev/null
for attempt in $(seq 1 30); do
  if docker exec "$run-relay" python -c "import json,urllib.request; r=json.load(urllib.request.urlopen('http://127.0.0.1:9090/api/v1/query?query=orenda_opcua_value_axis_a1_degrees')); assert -6.1 < float(r['data']['result'][0]['value'][1]) < -3.9" 2>/dev/null; then
    echo 'Prometheus queried a selected channel across the Box scrape socket.'
    exit 0
  fi
  sleep 1
done
docker logs "$run-relay"
exit 1
