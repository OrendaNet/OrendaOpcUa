# Collect selected channels with Prometheus

Each selected channel exports a gauge named `orenda_opcua_value_<suffix>`. A Boolean produces 0 or 1. Each configured channel also has a quality gauge, and the controller has a connection gauge. Disconnected, Bad, Uncertain and unsupported values drop out of the value family on the next scrape. A stable value continues to export while successful reads confirm quality. Source timestamps remain visible in the app. Prometheus records scrape time.

## Bundled OrendaBox Prometheus

An administrator runs the setup helper once on the Linux Box after installing and starting the market app. Copy **tools/box_scrape.py** from the matching source release to the Box and run the following from that directory:

```sh
sudo python3 box_scrape.py
sudo python3 box_scrape.py --apply
```

The first command previews the identified app, Prometheus configuration and scrape socket. If the Box contains several instances, pass the intended container names using `--app NAME --prometheus NAME`. The second command performs the following changes.

- Starts a small relay from the already-installed OPC UA image with its capabilities dropped and root filesystem read-only. It binds `127.0.0.1:9468` inside Prometheus's network namespace. Its sole mount is the read-only directory containing the exporter's scrape socket. The SDK application's controller network stays isolated.
- Adds the job to the existing Prometheus configuration. The helper validates the candidate with the installed promtool, saves a timestamped backup next to the existing file, preserves the configuration's mounted inode and reloads Prometheus.
- Verifies that Prometheus reports the target **UP**. On a failed activation it restores its configuration change. Rerunning the helper reconciles the existing relay. Rerun after a platform update replaces the Prometheus container or configuration, and after updating or reinstalling the app.

In the app, open **Prometheus** to see the last actual scrape. A preview request leaves the scrape timestamp unchanged. A target can report UP while the controller is offline; query the connection and channel-quality gauges for machine status.

```promql
orenda_opcua_value_axis_a1_degrees{job="orenda-opcua"}
orenda_opcua_channel_good{channel="axis_a1_degrees"}
orenda_opcua_connected
```

For an existing custom Prometheus configuration, the relay job is:

```yaml
- job_name: orenda-opcua
  static_configs:
    - targets: ['127.0.0.1:9468']
```

To retire the integration, remove only the `orenda-opcua` scrape job, validate/reload Prometheus, and remove the helper-named relay container shown in its preview. Uninstall the app through Edge Console.

## Other collectors

An independent Prometheus instance with an authorized private route to the app's TCP listener can scrape `/metrics` directly. Edge publishes a managed app port on the Box loopback interface. The address and port below illustrate a collector running on the same host as its local bind. A separate Docker container's loopback is a different network namespace.

An Edge administrator can reveal the scrape-only credential under **Prometheus → Other Prometheus collectors**. Store it in a protected credentials file readable by Prometheus. Use TLS or an authenticated encrypted tunnel for a cross-host collector. The temporary Edge browser URL carries a user session and is unsuitable for a scrape job.

```yaml
- job_name: orenda-opcua
  metrics_path: /metrics
  authorization:
    type: Bearer
    credentials_file: /etc/prometheus/secrets/opcua-scrape
  static_configs:
    - targets: ['127.0.0.1:20000']  # substitute the actual authorized bind
```
