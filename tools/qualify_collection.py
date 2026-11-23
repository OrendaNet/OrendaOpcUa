"""Verify the managed TSDB and grant lifecycle on a disposable, locally forwarded Box."""
import argparse
import json
import shlex
import time
from pathlib import Path
from urllib.parse import urlencode
import paramiko
import requests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--ssh-port", type=int, required=True)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--credential-file", type=Path, required=True)
    args = parser.parse_args()
    api = f"http://127.0.0.1:{args.port}/api/v1/"
    admin = requests.Session()
    login = admin.post(api+"auth/login", json={"username":"opcua-fixture","password":args.credential_file.read_text()}, timeout=30)
    login.raise_for_status()
    admin.headers["Authorization"] = "Bearer " + login.json()["token"]
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect("127.0.0.1", port=args.ssh_port, username="orenda", key_filename=str(args.key), look_for_keys=False, allow_agent=False, timeout=20, banner_timeout=60)
    def query(expr):
        url = "http://127.0.0.1:9090/api/v1/query?" + urlencode({"query":expr})
        _, out, _ = ssh.exec_command("curl --max-time 15 -fsS " + shlex.quote(url), timeout=30)
        payload = out.read()
        if out.channel.recv_exit_status():
            raise RuntimeError("Guest Prometheus query failed")
        return json.loads(payload)["data"]["result"]
    metric = 'orenda_app_channel_value{app_id="orenda-opcua",job="orenda-app-channels"}'
    def wait_values(present):
        until = time.monotonic()+150
        while time.monotonic()<until:
            rows = query(metric)
            readings = {row["metric"]["channel"]:float(row["value"][1]) for row in rows}
            if not present and not readings:
                return {}
            if present and len(readings)==2 and any(-6.1 < value < -3.9 for value in readings.values()) and any(value in (0,1) for value in readings.values()):
                return readings
            time.sleep(3)
        raise RuntimeError("PromQL did not reflect the expected permission state")
    def grant(capabilities):
        path = api + "apps/orenda-opcua/permissions"
        current = admin.get(path, timeout=30)
        current.raise_for_status()
        result = admin.put(path, json={"acknowledged":True, "fingerprint":current.json()["fingerprint"], "grantedCapabilities":capabilities}, timeout=120)
        result.raise_for_status()
    try:
        assert float(query('up{job="orenda-app-channels"}')[0]["value"][1]) == 1
        before = wait_values(True)
        try:
            grant(["network:outbound"])
            wait_values(False)
        finally:
            grant(["network:outbound", "prometheus:publish"])
        after = wait_values(True)
        print(json.dumps({"targetUp":True, "promqlBefore":before, "absentAfterRevocation":True, "promqlAfterRegrant":after}))
    finally:
        ssh.close()
        admin.close()


if __name__ == "__main__":
    main()
