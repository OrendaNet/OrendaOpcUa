# Using OPC UA Integrator

OPC UA Integrator lets you observe a controller from OrendaBox. An administrator connects one server and selects the machine signals to collect. Operators can open the app from Edge Console to view readings, recent controller events and collection health.

## Install and connect

1. In the Box App Market, install **OPC UA Integrator**. Approve **Outbound network access** for the controller and **Publish app measurements** for the Box collector. The compatible Box configures collection during installation.
2. Open the app and choose **Connect a controller**. Enter the controller IP or hostname. For a nonstandard port or path, enter the complete `opc.tcp://` endpoint. Choose **Discover endpoint**.
3. Compare the displayed server fingerprint with the controller administrator's copy. Choose one of the sign-in methods offered by the server. If the controller requires client trust, download the Box public certificate under **Connection** and import it into the controller. Select **Trust and connect**.
4. Browse the controller folders in **Channels**. Tick readable numeric or Boolean variables and select **Monitor selected channels**. Selections carry across folders. Open a variable to inspect its live value and quality. **Advanced metric name** lets you assign a stable, unit-bearing name.
5. **Overview** shows the latest samples. When the first Box scrape arrives, the Prometheus card shows its time. Under **Prometheus**, copy the query to use the values in dashboards. Machine-specific names and permissions depend on the controller configuration.

## Read a channel

| App term | Meaning |
| --- | --- |
| Channel | One selected controller variable, such as axis position or an emergency-stop flag. |
| Value | The last valid numeric or Boolean reading. In Prometheus a Boolean is 1 or 0. |
| Quality | The controller's OPC UA status for the reading. A bad or uncertain reading drops the value from the next snapshot. |
| Controller time | The source timestamp supplied for that reading. |
| Box scrape | The time the authenticated, on-Box Prometheus collector last retrieved a fresh app snapshot. |
| Metric name | The channel's stable query identifier. A single Box can collect like-named signals from different apps. |

When a controller goes offline, the app retries automatically and suppresses stale values. A changed server certificate requires a new fingerprint check. If an individual variable reports **BadUserAccessDenied**, check that the controller account can read that variable and that the relevant controller option is enabled.

## Monitor faults and events

In **Channels**, inspect a controller object that supports events and choose **Listen for events here**. Open **Events**. Search by source, message or condition code. Each row includes severity, controller time and Box receipt time when available. The app keeps the latest 200 events in memory. A server that supports condition refresh can republish retained conditions after reconnection. An empty event list can mean the selected source has emitted no events.

For KUKA DeviceConnector, look for `ActualPosition` under the relevant axis's `ParameterSet`. An object under `Controllers` may expose KSS messages. A joint reading and a KSS1 event are both exercised by the test simulator. Availability on a physical machine depends on its DeviceConnector installation and granted access.

## Limits

The app supports one controller, one event source and 128 selected scalar channels per installation. The read interval is adjustable from one to sixty seconds. The read-only service supports certificate-pinned Sign & Encrypt endpoints with anonymous or username authentication. It leaves motion, writes, alarm acknowledgement and safety functions with the controller.

See the [Prometheus metric reference](prometheus.md) for query examples and optional external collectors.
