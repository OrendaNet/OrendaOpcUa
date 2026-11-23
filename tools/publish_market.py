"""Publish only this official app, using an immutable GitHub release manifest."""
import json
import os
import re
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BASE = "https://apps.orendanet.com"
APP = "orenda-opcua"
GITHUB = "https://github.com/OrendaNet/OrendaOpcUa"


def api(path, body=None, token=None, method=None, optional=False):
    headers = {"User-Agent":"Orenda-OPCUA-Publisher", "Accept":"application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    try:
        with urlopen(Request(BASE + path, data=data, headers=headers, method=method), timeout=20) as response:
            return json.load(response)
    except HTTPError as exc:
        if optional and exc.code == 404:
            return None
        raise RuntimeError(f"{method or ('POST' if body else 'GET')} {path} returned HTTP {exc.code}") from None


def main():
    manifest = json.loads(Path(sys.argv[1]).read_text())
    v, = manifest["versions"]
    if manifest["id"] != APP or not re.fullmatch(r"sha256:[0-9a-f]{64}", v["digest"]) or v["image"] != "ghcr.io/orendanet/orenda-opcua@" + v["digest"]:
        raise ValueError("Invalid immutable release manifest")
    public = "/public/v1/apps/" + APP
    listing = api(public, optional=True)
    if listing and listing.get("latestVersion", {}).get("digest") == v["digest"]:
        print("Already published and verified:", BASE + public)
        return
    if not os.getenv("ORENDA_REPO_ADMIN_USERNAME") or not os.getenv("ORENDA_REPO_ADMIN_PASSWORD"):
        raise RuntimeError("Assign the Orenda Apps publisher credentials to this repository before market publication.")
    token = api("/admin/v1/auth/login", dict(username=os.environ["ORENDA_REPO_ADMIN_USERNAME"],
                                              password=os.environ["ORENDA_REPO_ADMIN_PASSWORD"]))["token"]
    tenant = "/admin/v1/tenants/official"
    apps = tenant + "/apps"
    path = apps + "/" + APP
    existing = api(path, token=token, optional=True)
    app = {k:manifest[k] for k in ("id","name","description","category","runtime","metadata")}
    app["port"] = 3101
    api(path if existing else apps, app, token, "PUT" if existing else "POST")
    image_root = "https://raw.githubusercontent.com/OrendaNet/OrendaOpcUa/" + v["version"] + "/docs/screens/"
    api(path + "/storefront", dict(slug=APP, headline=manifest["name"], shortDescription=manifest["description"],
        longDescription="Connect a controller using a trusted certificate, browse and preview its nodes, and select numeric or Boolean signals for Prometheus. See quality, controller timestamps, event codes, severity and the last collector scrape. An optional setup helper connects the stock Box Prometheus. Requires outbound network access to the controller.",
        categories=["industrial","monitoring"], tags=["opc-ua","prometheus","robotics","telemetry"],
        screenshots=[image_root+"overview.png",image_root+"channels.png"],
        documentationUrl=GITHUB+"#readme", supportUrl=GITHUB+"/issues"), token, "PUT")
    versions = api(path + "/versions", token=token)["versions"]
    previous = next((entry for entry in versions if entry["version"] == v["version"]), None)
    if previous and previous["digest"] != v["digest"]:
        raise RuntimeError("This market version already identifies a different image.")
    if not previous:
        api(path + "/versions", v, token)
    base = api(tenant, token=token)["tenant"]["currentReleaseId"]
    api(tenant + "/publish", dict(appIds=[APP], baseReleaseId=base, label="opcua-" + v["version"],
                                  notes="Release OPC UA Integrator " + v["version"]), token)
    if api(public)["latestVersion"]["digest"] != v["digest"]:
        raise RuntimeError("Market readback did not match the signed release digest.")
    print("Published and verified:", BASE + public)


if __name__ == "__main__":
    main()
