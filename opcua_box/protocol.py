import hashlib
import math
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from asyncua import Client, ua
from asyncua.crypto import security_policies as policies, uacrypto
from cryptography import x509
from cryptography.hazmat.primitives import serialization

POLICIES = [policies.SecurityPolicyAes256Sha256RsaPss, policies.SecurityPolicyAes128Sha256RsaOaep,
            policies.SecurityPolicyBasic256Sha256]
NUMERIC = {"Boolean", "SByte", "Byte", "Int16", "UInt16", "Int32", "UInt32", "Int64", "UInt64",
           "Float", "Double"}


def endpoint_url(value):
    if not isinstance(value, str) or len(value) > 768 or re.search(r"[\s\\\x00-\x1f]", value):
        raise ValueError("Enter an opc.tcp:// address and port.")
    url = urlsplit(value)
    if (url.scheme != "opc.tcp" or not url.hostname or not url.port or url.username is not None
            or url.password is not None or url.query or url.fragment):
        raise ValueError("Enter an opc.tcp:// address and port. Enter credentials separately.")
    return value


async def discover(url):
    endpoints = await Client(endpoint_url(url), timeout=5).connect_and_get_server_endpoints()
    for policy in POLICIES:
        for ep in endpoints:
            if ep.SecurityPolicyUri != policy.URI or ep.SecurityMode != ua.MessageSecurityMode.SignAndEncrypt:
                continue
            cert = x509.load_der_x509_certificate(ep.ServerCertificate[:4 + int.from_bytes(ep.ServerCertificate[2:4], "big")])
            der = cert.public_bytes(serialization.Encoding.DER)
            info = dict(policy=policy.URI.split("#")[-1], fingerprint=hashlib.sha256(der).hexdigest(),
                        name=ep.Server.ApplicationName.Text or ep.Server.ApplicationUri, uri=ep.Server.ApplicationUri,
                        expires=cert.not_valid_after_utc.isoformat(), identities=[v.TokenType.name for v in ep.UserIdentityTokens])
            now = datetime.now(timezone.utc)
            if not cert.not_valid_before_utc <= now <= cert.not_valid_after_utc:
                raise ValueError("Server certificate is outside its validity period. Check both clocks and the certificate.")
            return ep, policy, der, info
    raise ValueError("Enable a Sign & Encrypt endpoint with Basic256Sha256 or an AES policy on the controller.")


async def session(config, identity):
    ep, policy, der, info = await discover(config["url"])
    if info["fingerprint"] != config["fingerprint"]:
        raise ValueError("Server certificate changed. Compare its fingerprint and trust it again in Connection.")
    wanted = "UserName" if config["username"] else "Anonymous"
    if wanted not in info["identities"]:
        raise ValueError("This endpoint does not offer the chosen sign-in method.")
    client = Client(config["url"], timeout=5)
    client.application_uri = identity["uri"]
    client.name = "OrendaBox OPC UA Integrator"
    await client.set_security(policy, uacrypto.CertProperties(identity["cert"].encode(), extension="pem"),
                              uacrypto.CertProperties(identity["key"].encode(), extension="pem"),
                              server_certificate=uacrypto.CertProperties(der, extension="der"))
    # On KRC4 the selected transport IP, advertised DNS name and application URI can all differ.
    original = client.uaclient.create_session

    async def create(params):
        params.ServerUri = ep.Server.ApplicationUri
        params.EndpointUrl = ep.EndpointUrl
        return await original(params)

    client.uaclient.create_session = create
    if config["username"]:
        client.set_user(config["username"])
        client.set_password(config["password"])
    await client.connect()
    return client


def stable(node, namespaces):
    if node.NamespaceIndex >= len(namespaces):
        raise ValueError("The server returned an unknown namespace.")
    identifier = ua.NodeId(node.Identifier, 0, node.NodeIdType).to_string()
    return {"namespace": namespaces[node.NamespaceIndex], "identifier": identifier}


def resolve(ref, namespaces):
    if ref["namespace"] not in namespaces:
        raise ValueError("NamespaceMissing")
    original = ua.NodeId.from_string(ref["identifier"])
    return ua.NodeId(original.Identifier, namespaces.index(ref["namespace"]), original.NodeIdType)


def sample(data):
    quality = data.StatusCode.name
    variant = data.Value
    kind = variant.VariantType.name if variant else "Null"
    value = variant.Value if variant else None
    numeric = kind in NUMERIC and isinstance(value, (int, float)) and math.isfinite(value)
    if isinstance(value, int) and abs(value) > 2**53:
        numeric = False  # A Prometheus double cannot preserve arbitrary 64-bit integer precision.
    if isinstance(value, (list, tuple)):
        kind = kind + "[]"
    display = value if numeric else (str(value)[:160] if isinstance(value, (str, int, float)) else None)
    return dict(quality=quality, good=data.StatusCode.is_good(), type=kind, numeric=numeric,
                value=display if data.StatusCode.is_good() else None,
                sourceTime=data.SourceTimestamp.isoformat() if data.SourceTimestamp else None)


def message(exc):
    if isinstance(exc, ValueError):
        return str(exc)[:320]
    if isinstance(exc, ua.UaStatusCodeError):
        return ua.StatusCode(exc.code).name
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return "Controller unreachable. Check its address, the Box network grant and the controller network."
    return "OPC UA request failed (" + type(exc).__name__ + ")."
