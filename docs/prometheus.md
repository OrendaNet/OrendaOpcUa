# Box Prometheus collection

Install the app from Edge Console on a Box with the managed measurement contract. Approve **Outbound network access** for the controller and **Publish app measurements** for the Box collector. The installer starts the application and supplies its scoped runtime credential. DevicePlatform supplies the collector job and local scrape credential. Open **Channels**, select signals and choose **Monitor selected channels**. **Overview** shows the time of the latest authenticated Box scrape.

```promql
orenda_app_channel_value{app_id="orenda-opcua",channel="axis_a1_degrees"}
orenda_app_channel_good{app_id="orenda-opcua",channel="axis_a1_degrees"}
orenda_app_channels_ready{app_id="orenda-opcua"}
```

A Boolean exports 0 or 1. Bad quality, disconnection and unsupported values clear the value and export quality 0. The app replaces its complete snapshot every five seconds. Edge expires a snapshot after 30 seconds and checks the current install grants at scrape time. Prometheus records scrape time. The controller's source timestamp is visible alongside the value in the app.

If Box collection has not started, open **Prometheus** to see the reported state. For a denied grant, open the app's permissions in Edge Console. A controller-specific access failure appears in the channel preview or connection panel.

## External collectors

An administrator can reveal a read-only bearer credential under **Prometheus → Other Prometheus collectors**. Over an authorized private route to the allocated app port, scrape `/metrics` using a Prometheus `authorization.credentials_file`. External consumers get the existing `orenda_opcua_value_<suffix>`, `orenda_opcua_channel_good` and `orenda_opcua_connected` families. The optional external route has its own last-scrape timestamp. It never marks the Box job healthy.
