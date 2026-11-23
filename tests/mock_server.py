import socket
from datetime import datetime, timezone

from asyncua import Server, ua
from asyncua.crypto.cert_gen import generate_self_signed_app_certificate
from asyncua.server.user_managers import User, UserRole
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


class Access:
    def __init__(self, password):
        self.password = password

    def get_user(self, iserver, username=None, password=None, certificate=None):
        if username is None or (username == "observer" and password == self.password):
            return User(role=UserRole.User)
        return None


class Plant:
    def __init__(self, password, port=None):
        self.url = f"opc.tcp://127.0.0.1:{port or free_port()}/plant/"
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.uri = "urn:example:controller:opcua"
        self.cert = generate_self_signed_app_certificate(self.key, "Offline commissioning fixture", {},
            [x509.UniformResourceIdentifier(self.uri), x509.DNSName("localhost")], [ExtendedKeyUsageOID.SERVER_AUTH])
        self.password = password

    async def start(self, shifted=False):
        self.server = Server(user_manager=Access(self.password))
        await self.server.init()
        await self.server.set_application_uri(self.uri)
        self.server.set_endpoint(self.url)
        self.server.set_server_name("Simulated robot cell")
        self.server.iserver.certificate = self.cert
        self.server.iserver.private_key = self.key
        # Also expose None for discovery, as industrial endpoints commonly do.
        self.server.set_security_policy([ua.SecurityPolicyType.NoSecurity, ua.SecurityPolicyType.Basic256Sha256_SignAndEncrypt])
        self.server.set_identity_tokens([ua.AnonymousIdentityToken, ua.UserNameIdentityToken])
        if shifted:
            await self.server.register_namespace("urn:mock:extra")
        ns = await self.server.register_namespace("urn:example:robot-cell")
        self.robot = await self.server.nodes.objects.add_object(ua.NodeId("Robot", ns), ua.QualifiedName("Robot", ns))
        self.axis = await self.robot.add_variable(ua.NodeId("Axes.A1.ActualPosition", ns),
                                                 ua.QualifiedName("ActualPosition", ns), -5.34)
        self.stop = await self.robot.add_variable(ua.NodeId("Safety.EmergencyStop", ns),
                                                 ua.QualifiedName("EmergencyStop", ns), False)
        self.text = await self.robot.add_variable(ua.NodeId("Description", ns), ua.QualifiedName("Description", ns), "Robot cell")
        self.events = await self.server.get_event_generator(ua.ObjectIds.ConditionType, self.robot)
        await self.server.start()

    async def alarm(self):
        await self.stop.write_value(True)
        self.events.event.ConditionName = "KSS1"
        self.events.event.Retain = True
        self.events.event.Severity = 800
        await self.events.trigger(message="EMERGENCY STOP")

    async def stop_server(self):
        await self.server.stop()
