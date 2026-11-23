import asyncio
import contextlib
import os
import re
import time
from collections import deque
from datetime import datetime, timezone

from asyncua import ua
from aiohttp import ClientSession, ClientTimeout

from .protocol import endpoint_url, message, resolve, sample, session, stable


def utc():
    return datetime.now(timezone.utc).isoformat()


class Events:
    def __init__(self, monitor):
        self.monitor = monitor
        self.refreshing = False

    def event_notification(self, event):
        if self is not self.monitor.handler:
            return
        kind = getattr(event, "EventType", None)
        if kind == ua.NodeId(ua.ObjectIds.RefreshStartEventType):
            self.refreshing = True
            return
        if kind == ua.NodeId(ua.ObjectIds.RefreshEndEventType):
            self.refreshing = False
            return
        timestamp = getattr(event, "Time", None)
        self.monitor.events.appendleft(dict(
            received=utc(), time=timestamp.isoformat() if timestamp else None,
            code=str(getattr(event, "ConditionName", "") or "")[:150],
            source=str(getattr(event, "SourceName", "") or "")[:150],
            text=str(getattr(getattr(event, "Message", None), "Text", "") or "")[:1000],
            severity=getattr(event, "Severity", None), retain=getattr(event, "Retain", None),
            phase="Refresh" if self.refreshing else "Notification"))


class Monitor:
    def __init__(self, store):
        self.store = store
        self.lock = asyncio.Lock()
        self.client = None
        self.ns = []
        self.sub = self.handler = None
        self.events = deque(maxlen=200)
        self.event_state = "Off"
        self.samples = {}
        self.last_read = self.last_scrape = None
        self.last_good_connection = 0
        self.error = ""
        self.granted = None
        self.grant_checked = 0
        self.wake = asyncio.Event()

    @property
    def cfg(self):
        return self.store.data

    def change(self, **fields):
        self.store.save({**self.cfg, **fields, "revision": self.cfg["revision"] + 1})
        self.wake.set()

    async def network(self):
        base = os.getenv("ORENDA_EDGE_API_URL")
        if not base:
            return
        if time.monotonic() - self.grant_checked > 15 or self.grant_checked == 0:
            async with ClientSession(timeout=ClientTimeout(total=4)) as edge:
                async with edge.get(base.rstrip("/") + "/context", allow_redirects=False,
                                    headers={"Authorization": "Bearer " + os.environ["ORENDA_APP_TOKEN"]}) as response:
                    if response.status != 200:
                        raise ValueError("Edge could not verify this app's network grant. Check App permissions.")
                    self.granted = "network:outbound" in (await response.json()).get("capabilities", [])
                    self.grant_checked = time.monotonic()
        if not self.granted:
            raise ValueError("Open Edge Console → Apps → OPC UA Integrator → Permissions and grant outbound network access.")

    async def close(self):
        self.handler = self.sub = None
        if self.cfg["notifier"]:
            self.event_state = "Waiting for controller"
        client, self.client = self.client, None
        self.samples.clear()
        self.last_good_connection = 0
        if client:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(client.disconnect(), 4)
            client.disconnect_socket()

    async def connect(self, config, save=False):
        new = await session(config, self.cfg)
        try:
            ns = await new.get_namespace_array()
            if save:
                if self.cfg["connection"] and self.cfg["connection"]["url"] != config["url"] and (
                        self.cfg["channels"] or self.cfg["notifier"]):
                    raise ValueError("Remove the selected channels and event source before changing controllers.")
                self.change(connection=config)
        except BaseException:
            await new.disconnect()
            raise
        await self.close()
        self.client, self.ns, self.error = new, ns, ""
        await self.events_setup()

    def node(self, value):
        if not self.client:
            raise ValueError("The controller is offline. Check Connection and retry.")
        if not isinstance(value, str) or not 1 <= len(value) <= 1200:
            raise ValueError("Enter a valid OPC UA NodeId.")
        try:
            return self.client.get_node(ua.NodeId.from_string(value))
        except Exception as exc:
            raise ValueError("Use a NodeId such as ns=2;s=Motor.Speed.") from exc

    async def preview(self, value):
        node = self.node(value)
        cls = await node.read_node_class()
        result = dict(id=node.nodeid.to_string(), name=(await node.read_display_name()).Text, kind=cls.name)
        if cls == ua.NodeClass.Variable:
            result.update(sample(await node.read_data_value(raise_on_bad_status=False)))
        if cls in {ua.NodeClass.Object, ua.NodeClass.View}:
            data = await node.read_attribute(ua.AttributeIds.EventNotifier, raise_on_bad_status=False)
            result["events"] = bool(data.StatusCode.is_good() and data.Value and (data.Value.Value or 0) & 1)
        return result

    async def browse(self, value, offset):
        node = self.node(value)
        desc = ua.BrowseDescription()
        desc.NodeId = node.nodeid
        desc.ReferenceTypeId = ua.NodeId(ua.ObjectIds.HierarchicalReferences)
        desc.IncludeSubtypes = True
        desc.NodeClassMask = int(ua.NodeClass.Object | ua.NodeClass.Variable | ua.NodeClass.View)
        desc.ResultMask = ua.BrowseResultMask.All
        params = ua.BrowseParameters()
        params.NodesToBrowse = [desc]
        params.RequestedMaxReferencesPerNode = 65
        result, = await self.client.uaclient.browse(params)
        rows, seen = [], 0
        try:
            while True:
                result.StatusCode.check()
                for ref in result.References:
                    if not getattr(ref.NodeId, "ServerIndex", 0) and not getattr(ref.NodeId, "NamespaceUri", None):
                        if seen >= offset:
                            rows.append(dict(id=ref.NodeId.to_string(), name=ref.DisplayName.Text or ref.BrowseName.Name,
                                             kind=ref.NodeClass.name))
                        seen += 1
                        if len(rows) == 65:
                            break
                if len(rows) >= 65 or not result.ContinuationPoint:
                    break
                result, = await self.client.uaclient.browse_next(ua.BrowseNextParameters(False, [result.ContinuationPoint]))
        finally:
            if result.ContinuationPoint:
                await self.client.uaclient.browse_next(ua.BrowseNextParameters(True, [result.ContinuationPoint]))
        return dict(rows=rows[:64], next=offset + 64 if len(rows) > 64 else None)

    async def events_setup(self):
        self.handler = None
        if self.sub:
            await self.sub.delete()
            self.sub = None
        self.event_state = "Off"
        if not self.cfg["notifier"]:
            return
        try:
            node = self.client.get_node(resolve(self.cfg["notifier"], self.ns))
            self.handler = Events(self)
            self.sub = await self.client.create_subscription(500, self.handler)
            filt = ua.EventFilter()
            for key in ("EventType", "Time", "SourceName", "Message", "Severity", "ConditionName", "Retain"):
                base = ua.ObjectIds.ConditionType if key in {"ConditionName", "Retain"} else ua.ObjectIds.BaseEventType
                filt.SelectClauses.append(ua.SimpleAttributeOperand(ua.NodeId(base), [ua.QualifiedName(key, 0)],
                                                                   ua.AttributeIds.Value))
            await self.sub.subscribe_events(node, evfilter=filt, queuesize=100)
            self.event_state = "Listening"
            try:
                await self.client.get_node(ua.ObjectIds.ConditionType).call_method(
                    ua.NodeId(ua.ObjectIds.ConditionType_ConditionRefresh),
                    ua.Variant(self.sub.subscription_id, ua.VariantType.UInt32))
            except ua.UaStatusCodeError as exc:
                self.event_state = "Listening · retained refresh unavailable (" + message(exc) + ")"
        except Exception as exc:
            self.event_state = message(exc)

    async def collect(self):
        nodes, names, readings = [], [], {}
        for channel in self.cfg["channels"]:
            try:
                nodes.append(self.client.get_node(resolve(channel, self.ns)))
                names.append(channel["metric"])
            except ValueError:
                readings[channel["metric"]] = {"quality": "NamespaceMissing", "good": False}
        # A small read also checks liveness while the machine is idle or all namespaces are missing.
        if not nodes:
            await self.client.nodes.server_state.read_value()
        for i in range(0, len(nodes), 16):
            data = await self.client.read_attributes(nodes[i:i + 16])
            for name, item in zip(names[i:i + 16], data):
                readings[name] = sample(item)
        self.samples = readings
        self.last_read = utc()
        self.last_good_connection = time.monotonic()

    async def run(self):
        delay = 1
        while True:
            async with self.lock:
                self.wake.clear()
                if self.cfg["connection"]:
                    try:
                        await self.network()
                        if not self.client:
                            await self.connect(self.cfg["connection"])
                        await self.collect()
                        delay = self.cfg["interval"]
                        self.error = ""
                    except Exception as exc:
                        self.error = message(exc)
                        await self.close()
                        delay = min(30, max(2, delay * 2))
            try:
                await asyncio.wait_for(self.wake.wait(), delay)
            except asyncio.TimeoutError:
                pass

    def online(self):
        return bool(self.client and self.last_good_connection and
                    time.monotonic() - self.last_good_connection < self.cfg["interval"] * 2 + 6)

    def metrics(self):
        online = self.online()
        lines = ["# TYPE orenda_opcua_connected gauge", f"orenda_opcua_connected {int(online)}",
                 "# TYPE orenda_opcua_channel_good gauge"]
        for channel in self.cfg["channels"]:
            metric = channel["metric"]
            item = self.samples.get(metric, {})
            good = online and item.get("good") and item.get("numeric")
            lines.append(f'orenda_opcua_channel_good{{channel="{metric}"}} {int(bool(good))}')
            if good:
                val = item["value"]
                lines.extend([f"# TYPE orenda_opcua_value_{metric} gauge",
                              f"orenda_opcua_value_{metric} {int(val) if isinstance(val, bool) else val}"])
        return "\n".join(lines) + "\n"

    async def update(self, action, data):
        if data.get("revision") != self.cfg["revision"]:
            raise ValueError("Configuration changed in another session. Refresh before applying your edits.")
        if action == "connect":
            await self.network()
            url = endpoint_url(data.get("url"))
            pin = data.get("fingerprint", "")
            username, password = data.get("username", ""), data.get("password", "")
            if not isinstance(pin, str) or not re.fullmatch("[a-f0-9]{64}", pin):
                raise ValueError("Review the controller fingerprint first.")
            if not all(isinstance(v, str) and len(v) <= 512 for v in (username, password)):
                raise ValueError("Invalid controller credentials.")
            await self.connect(dict(url=url, fingerprint=pin, username=username, password=password), save=True)
        elif action == "interval":
            interval = data.get("interval")
            if type(interval) != int or not 1 <= interval <= 60:
                raise ValueError("Choose a read interval from 1 to 60 seconds.")
            self.change(interval=interval)
        elif action == "add":
            metric = data.get("metric", "")
            channels = self.cfg["channels"]
            if not isinstance(metric, str) or not re.fullmatch("[a-z][a-z0-9_]{0,62}", metric):
                raise ValueError("Use a metric name starting with a lowercase letter, up to 63 letters, digits or underscores.")
            if len(channels) >= 128:
                raise ValueError("Up to 128 channels can be collected.")
            info = await self.preview(data.get("id"))
            if not info.get("good") or not info.get("numeric"):
                raise ValueError("Select a readable numeric or Boolean scalar with Good quality.")
            ref = stable(self.node(info["id"]).nodeid, self.ns)
            if any(c["metric"] == metric or all(c[k] == v for k, v in ref.items()) for c in channels):
                raise ValueError("This node or metric name is already selected.")
            self.change(channels=channels + [{**ref, "metric": metric, "name": info["name"]}])
        elif action == "remove":
            self.change(channels=[c for c in self.cfg["channels"] if c["metric"] != data.get("metric")])
            self.samples.pop(data.get("metric"), None)
        elif action == "notifier":
            ref = None
            if data.get("id"):
                info = await self.preview(data["id"])
                if not info.get("events"):
                    raise ValueError("Select an object that supports event subscriptions.")
                ref = {**stable(self.node(info["id"]).nodeid, self.ns), "name": info["name"]}
            self.change(notifier=ref)
            if self.client:
                await self.events_setup()
        else:
            raise ValueError("Unknown configuration action.")
