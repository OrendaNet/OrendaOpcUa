"""Configure the installed Box Prometheus to scrape the installed OPC UA app."""
import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
from pathlib import Path

JOB = """  - job_name: orenda-opcua
    static_configs:
      - targets: ['127.0.0.1:9468']
"""
LABEL = "com.orendanet.opcua.scrape-for"


def docker(*args):
    return subprocess.check_output(["docker", *map(str, args)], text=True).strip()


def field(container, template):
    return docker("inspect", "--format", template, container)


def find(service, exact_image=None):
    ids = docker("ps", "-q", "--filter", f"label=com.docker.compose.service={service}").split()
    if exact_image:
        ids = [c for c in ids if field(c, "{{.Config.Image}}").split("@")[0].split(":")[0] == exact_image]
    if len(ids) != 1:
        raise ValueError(f"Found {len(ids)} containers for {service}. Specify the intended container name explicitly.")
    return ids[0]


def mount(container, destination):
    mounts = json.loads(field(container, "{{json .Mounts}}"))
    for item in mounts:
        if item["Destination"] == destination:
            return Path(item["Source"])
    raise ValueError(f"Missing installed mount at {destination}")


def configured(original):
    marker = re.compile(r"(?m)^scrape_configs:[ \t]*\r?$")
    matches = list(marker.finditer(original))
    if len(matches) != 1:
        raise ValueError("Expected the stock scrape_configs list. See docs/prometheus.md for custom configurations.")
    existing = re.findall(r"(?m)^[ \t]*-[ \t]+job_name:[ \t]*['\"]?orenda-opcua['\"]?[ \t]*$", original)
    if existing:
        if JOB in original and len(existing) == 1:
            return original
        raise ValueError("An orenda-opcua job already exists with different settings. Review it first.")
    end = matches[0].end()
    return original[:end] + "\n" + JOB.rstrip() + original[end:]


def api_in_relay(relay, route):
    code = "import sys,urllib.request;print(urllib.request.urlopen('http://127.0.0.1:9090'+sys.argv[1],timeout=3).read().decode())"
    return json.loads(docker("exec", relay, "python", "-c", code, route))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Install the scrape relay and activate the validated job")
    parser.add_argument("--app", help="Installed OPC UA app container")
    parser.add_argument("--prometheus", help="Box core Prometheus container")
    args = parser.parse_args()
    app = args.app or find("app", "ghcr.io/orendanet/orenda-opcua")
    prom = args.prometheus or find("prometheus")
    app_image = field(app, "{{.Image}}")
    prom_id = field(prom, "{{.Id}}")
    prom_name = field(prom, "{{.Name}}").strip("/")
    relay = "orenda-opcua-scrape-" + hashlib.sha256(prom_name.encode()).hexdigest()[:10]
    config = mount(prom, "/etc/prometheus/prometheus.yml")
    scrape = mount(app, "/data") / "scrape"
    if not stat.S_ISSOCK((scrape/"metrics.sock").stat().st_mode):
        raise ValueError("Start the app and wait for its scrape socket.")
    print(f"Job orenda-opcua -> 127.0.0.1:9468 in {prom_name}\nConfig {config}\nRelay {relay}\nRead-only socket directory {scrape}")
    if not args.apply:
        configured(config.read_text())
        print("Rerun with --apply to activate.")
        return
    with config.open("r+", encoding="utf8") as file:
        fcntl.flock(file, fcntl.LOCK_EX)
        original = file.read()
        updated = configured(original)
        if updated != original:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf8") as candidate:
                candidate.write(updated)
                candidate.flush()
                os.chmod(candidate.name, 0o644)
                inside = "/tmp/" + Path(candidate.name).name + ".yml"
                docker("cp", candidate.name, prom + ":" + inside)
                try:
                    docker("exec", prom, "/bin/promtool", "check", "config", inside)
                finally:
                    docker("exec", "--user", "0", prom, "rm", "-f", inside)
        existing = docker("ps", "-aq", "--filter", f"name=^/{relay}$")
        if existing:
            owner = field(relay, '{{index .Config.Labels "' + LABEL + '"}}')
            if owner != prom_name:
                raise ValueError("The relay name is already used by another container.")
            same = (field(relay, "{{.Image}}") == app_image and
                    field(relay, "{{.HostConfig.NetworkMode}}") == "container:" + prom_id and
                    mount(relay, "/scrape") == scrape)
            if not same:
                docker("rm", "-f", relay)
                existing = ""
        if not existing:
            docker("run", "-d", "--name", relay, "--label", LABEL + "=" + prom_name, "--restart", "unless-stopped",
                   "--network", "container:" + prom_id, "--read-only", "--cap-drop", "ALL",
                   "--security-opt", "no-new-privileges", "-v", str(scrape) + ":/scrape:ro", app_image,
                   "python", "-m", "opcua_box.relay")
        elif field(relay, "{{.State.Running}}") != "true":
            docker("start", relay)
        def write(text):
            file.seek(0)
            file.write(text)
            file.truncate()
            file.flush()
            os.fsync(file.fileno())
        if updated != original:
            backup = config.with_name(config.name + ".before-opcua-" + str(time.time_ns()))
            shutil.copy2(config, backup)
            print("Backup", backup)
            write(updated)
        try:
            docker("kill", "--signal=HUP", prom)
            for _ in range(65):
                try:
                    targets = api_in_relay(relay, "/api/v1/targets?state=active")["data"]["activeTargets"]
                    if any(t["labels"].get("job") == "orenda-opcua" and t["health"] == "up" for t in targets):
                        print("Verified orenda-opcua target UP in Box Prometheus.")
                        return
                except (subprocess.CalledProcessError, KeyError, ValueError):
                    pass
                time.sleep(1)
            raise RuntimeError("Prometheus did not report the target UP within 65 seconds.")
        except BaseException:
            if updated != original:
                write(original)
                docker("kill", "--signal=HUP", prom)
            if not existing:
                docker("rm", "-f", relay)
            raise


if __name__ == "__main__":
    main()
