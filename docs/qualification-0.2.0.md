# OPC UA Integrator 0.2.0 qualification

Tested on 23 November 2026 using disposable QEMU ARM64 guests and an independent secure OPC UA simulator. The release candidate is `ghcr.io/orendanet/orenda-opcua@sha256:c5195900013514c71bf496a75dbda9d8e629b137c360ff01c9ecfa1825e0f20e`, built from `0bce0e98d9dd7a5aa27875581a7718ea482d72df`. The release workflow promotes this digest. The packaged process entry point is Node.js, with the JavaScript Box SDK and a private, compiled Go collector.

| Observed through the Box | Tinker Board 2S | CP3B |
| --- | --- | --- |
| Standard Edge installer accepted a signed test catalog and approved grants | Passed | Passed |
| Isolated Edge UI session, secure OPC UA connection and selected channels | Passed | Passed |
| Axis and Boolean stop channels reported Good, KSS1 event delivered | Passed | Passed |
| Stock Box Prometheus target reported up and returned two selected values | Passed | Passed |
| Revoking export removed both values, granting it again restored both | Passed | Passed |

The test uninstalled the preceding candidate and installed the final digest on each target. The second installation recovered the app volume. One PromQL read on CP3B returned axis **−5.985** and stop **0**. On Tinker Board it returned **−4.773** and **1**. Following reapproval both guests returned fresh data. The actual values change as the simulator runs.

The authenticated CP3B browser frame supplied the screenshots of Overview, Channels, Events and Prometheus. Separate browser tests exercised commissioning and navigation at 320 px and queried an external Prometheus TSDB. Native ARM64 CI ran the encrypted simulator tests, including certificate mismatch, rejected credentials, namespace reordering, atomic selection, loss of signal quality and a 128-signal snapshot. SDK tests also exercise normalized OPC UA, PROFINET and EtherCAT observations. These last two inputs are adapter fixtures.

## Test environment

The guests booted Tinker Board and CP3B image filesystems in writable overlays with the candidate Edge and DevicePlatform service files staged. The catalog was locally signed for the test. The generic QEMU kernel needed matching netfilter modules on Tinker Board. CP3B used fresh test database volumes because its snapshot contained damaged database files. Those steps prepared the test machines. Each app installation subsequently went through Edge Console's ordinary installer. No per-app Prometheus setup was performed.

QEMU exercises the Box software path. Physical controller availability and DevicePlatform's separate signed physical qualification for production OS promotion remain outside this run.

[Native ARM64 build and tests](https://github.com/OrendaNet/OrendaOpcUa/actions/runs/45241111733) · [Guest installation test](../tools/qualify_guest.py) · [Box PromQL and grant test](../tools/qualify_collection.py)
