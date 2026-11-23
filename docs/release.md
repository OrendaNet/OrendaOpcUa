# OPC UA Integrator 0.2.0

The ARM64 service uses a compiled Go OPC UA collector and the JavaScript OrendaBox SDK. It supports certificate-pinned controller sessions, authentication, variable browsing and inspection, batch selection, source timestamps, quality and alarm events. DevicePlatform owns the local Prometheus target and scrape credential. Installation requires SDK contract 1.3, Edge Manager 0.2.80 and DevicePlatform 0.2.92.

Operators can view readings and events. An Edge administrator chooses the controller, trusts its certificate and selects signals. The client certificate and configuration persist in the app volume. Recent events are held in memory. This version targets one server and one notifier per installation.

The same ARM64 image passed installation, alarm delivery, PromQL readback, and grant revocation and recovery on both supported QEMU targets. [Qualification record](qualification-0.2.0.md)

See the [operator guide](operator-guide.md), [collection reference](prometheus.md) and [design decisions](design.md).
