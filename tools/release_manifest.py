import json
import os
import re
from pathlib import Path

manifest = json.loads(Path("orenda-app.json").read_text())
digest = os.environ["IMAGE_DIGEST"]
version = Path("VERSION").read_text().strip()
if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
    raise ValueError("Invalid release digest or version")
manifest["versions"] = [dict(version=version, image=f"ghcr.io/orendanet/orenda-opcua@{digest}", digest=digest,
                             architectures=["arm64"], minPlatformVersion="0.2.45",
                             releaseNotes="Secure controller commissioning, selected Prometheus channels and OPC UA event monitoring.")]
Path("dist").mkdir(exist_ok=True)
Path("dist/orenda-app.json").write_text(json.dumps(manifest, indent=2)+"\n")
