import json
import os
import secrets
import uuid
from pathlib import Path

from asyncua.crypto.cert_gen import generate_self_signed_app_certificate
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.file = self.directory / "settings.json"
        if self.file.exists():
            self.data = json.loads(self.file.read_text("utf8"))
        else:
            uri = "urn:orendabox:opcua:" + str(uuid.uuid4())
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            cert = generate_self_signed_app_certificate(
                key, "OrendaBox OPC UA Integrator", {}, [x509.UniformResourceIdentifier(uri)],
                [ExtendedKeyUsageOID.CLIENT_AUTH], days=1825)
            self.save(dict(uri=uri, cert=cert.public_bytes(serialization.Encoding.PEM).decode(),
                           key=key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                 serialization.NoEncryption()).decode(),
                           token=secrets.token_urlsafe(36), connection=None, interval=2,
                           channels=[], notifier=None, revision=0))

    def save(self, updated):
        temp = self.file.with_suffix(".tmp")
        with open(temp, "w", encoding="utf8", opener=lambda p, f: os.open(p, f, 0o600)) as out:
            json.dump(updated, out, ensure_ascii=False, allow_nan=False)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, self.file)
        self.data = updated
