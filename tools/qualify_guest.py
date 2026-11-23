"""Install using the Edge API of a disposable loopback-forwarded QEMU guest.

Use a separately staged signed catalog and an isolated Edge data directory.
The password file belongs to this test fixture. Protect it outside the repository.
"""
import argparse
import json
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode
import requests


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--port", type=int, required=True)
    args.add_argument("--credential-file", type=Path, required=True)
    args.add_argument("--controller", required=True)
    opts = args.parse_args()
    base = f"http://127.0.0.1:{opts.port}"
    session = requests.Session()

    def call(path, body=None, method=None, timeout=600):
        response = session.request(method or ("POST" if body is not None else "GET"), base + path,
                                   json=body, timeout=timeout)
        if response.status_code >= 400:
            try:
                detail = str(response.json().get("error", ""))[:500]
            except ValueError:
                detail = "Non-JSON response"
            route = path.split("?")[0]
            if "/~" in route:
                route = "/isolated-app/" + route.rsplit("/", 1)[-1]
            raise RuntimeError(f"{route} HTTP {response.status_code} {detail}")
        return response.json()

    if not opts.credential_file.exists():
        opts.credential_file.write_text(secrets.token_urlsafe(32))
        opts.credential_file.chmod(0o600)
    credentials = {"username":"opcua-fixture", "password":opts.credential_file.read_text()}
    response = session.post(base+"/api/v1/auth/bootstrap",json=credentials,timeout=90)
    identity = response.json() if response.status_code == 201 else call("/api/v1/auth/login", credentials)
    session.headers["Authorization"] = "Bearer " + identity["token"]
    project = "opcua-emulator"
    catalog = call("/api/v1/apps/catalog?projectId="+project)
    app = next(entry for entry in catalog["apps"] if entry["id"] == "orenda-opcua")
    version = app["versions"][0]
    permissions = dict(acknowledged=True, fingerprint=version["permissionFingerprint"],
                       grantedCapabilities=app["metadata"]["orenda"]["capabilities"])
    installed = call("/api/v1/apps/install", dict(appId=app["id"],projectId=project,version=version["version"],
                                                  permissions=permissions),timeout=1200)
    assert installed["installed"]["sdk"]["sdkVersion"] == "1.3"
    print("Market installer accepted the scoped grants and started the digest-pinned image.", flush=True)
    ticket = call("/api/v1/apps/orenda-opcua/ui-session", {})
    landing = session.get(base + ticket["openPath"], timeout=120)
    landing.raise_for_status()
    from urllib.parse import urlparse
    scoped = urlparse(landing.url).path
    if "/~" not in scoped:
        raise RuntimeError("Edge did not establish the isolated UI session")
    session.headers.pop("Authorization")
    prefix = scoped.rstrip("/") + "/api/"
    def state():
        return call(prefix+"state")
    def change(action, fields):
        return call(prefix+action, {"revision":state()["revision"], **fields})
    discovered = change("discover", {"url":opts.controller})
    change("connect", dict(url=opts.controller, fingerprint=discovered["fingerprint"]))
    tree = call(prefix+"browse")
    robot = next(n for n in tree["rows"] if n["name"] == "Robot")
    tree = call(prefix+"browse?"+urlencode({"id":robot["id"]}))
    nodes = [dict(id=n["id"],path="Robot / "+n["name"]) for n in tree["rows"] if n["name"] in {"ActualPosition","EmergencyStop"}]
    assert len(nodes)==2
    change("addMany",dict(nodes=nodes,generation=tree["generation"]))
    change("notifier",{"id":robot["id"]})
    end = time.monotonic()+160
    while time.monotonic()<end:
        status=state()
        if status["lastScrape"] and status["online"] and any(e["code"]=="KSS1" for e in status["events"]):
            print(json.dumps({"version":version["version"],"digest":version["digest"],"source":catalog["source"],
                              "readings":{k: v.get("quality") for k,v in status["samples"].items()},
                              "boxScrape":status["lastScrape"],"event":"KSS1"}),flush=True)
            return
        time.sleep(3)
    raise RuntimeError("Timed out waiting for automatic Box collection and a simulated KSS1 event")


if __name__=="__main__":
    main()
