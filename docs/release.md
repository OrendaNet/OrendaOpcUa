# OPC UA Integrator 0.1.0

The initial ARM64 release supports secure OPC UA server commissioning, controller certificate pinning, read-only operator views, direct NodeId inspection, selected numeric and Boolean channels, and a bounded recent-event view. The only requested SDK capability is outbound network access for the controller connection. Requires DevicePlatform 0.2.45 or newer.

Offline protocol tests cover secure username login, incorrect credentials, changed fingerprints, Edge roles, configured channel removal, bad-quality values, an emergency-stop event, a controller restart with reordered namespaces and app restart recovery. Browser qualification uses a simulated robot and an independent Prometheus 2.54.1 server. Native ARM64 container qualification exercises the scrape-only socket relay and PromQL readback. Physical KRC4 qualification of the packaged app is pending device availability.

[Setup guide](https://github.com/OrendaNet/OrendaOpcUa#readme) · [Box Prometheus setup](https://github.com/OrendaNet/OrendaOpcUa/blob/main/docs/prometheus.md) · [Report an issue](https://github.com/OrendaNet/OrendaOpcUa/issues)
