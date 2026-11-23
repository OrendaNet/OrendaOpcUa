"""Run against tools.demo. Pass PROMETHEUS_BIN to also verify an independent TSDB."""
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright, expect

base = os.environ.get("DEMO_BASE", "http://127.0.0.1:3100")
endpoint = os.environ.get("DEMO_ENDPOINT", "opc.tcp://127.0.0.1:4841/plant/")
shots = os.environ.get("SCREENSHOTS")
if shots:
    Path(shots).mkdir(parents=True, exist_ok=True)

with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width":1280,"height":850}, device_scale_factor=1)
    page.set_default_timeout(15000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(base)
    page.get_by_role("button", name="Connect a controller").click()
    page.get_by_label("Endpoint address").fill(endpoint)
    page.get_by_role("button", name="Discover endpoint").click()
    page.get_by_label("I compared").check()
    page.get_by_label("Sign-in method").select_option("Anonymous")
    page.get_by_role("button", name="Trust and connect").click()
    page.get_by_role("button", name="Robot", exact=True).click()
    page.get_by_role("button", name="ActualPosition", exact=True).click()
    page.get_by_label("Prometheus metric suffix").fill("axis_a1_degrees")
    page.get_by_role("button", name="Add channel").click()
    expect(page.locator("#selected")).to_contain_text("axis_a1_degrees")
    page.get_by_role("button", name="EmergencyStop", exact=True).click()
    expect(page.locator("#preview h3")).to_have_text("EmergencyStop")
    page.get_by_label("Prometheus metric suffix").fill("emergency_stop")
    page.get_by_role("button", name="Add channel").click()
    expect(page.locator("#selected").get_by_text("emergency_stop", exact=True)).to_be_visible()
    if shots:
        page.screenshot(path=str(Path(shots)/"channels.png"))
    page.get_by_role("button", name="Up a level").click()
    page.locator(".row").filter(has=page.get_by_role("button",name="Robot",exact=True)).get_by_role("button",name="Inspect").click()
    page.get_by_role("button",name="Listen for events here").click()
    expect(page.locator("#eventList")).to_contain_text("KSS1",timeout=22000)
    page.get_by_role("button",name="Overview",exact=True).click()
    expect(page.locator("#readings")).to_contain_text("Good")
    page.get_by_role("button",name="Prometheus",exact=True).click()
    expect(page.locator("#exposition")).to_contain_text("orenda_opcua_value_axis_a1_degrees")
    if os.environ.get("PROMETHEUS_BIN"):
        with tempfile.TemporaryDirectory() as work:
            folder = Path(work)
            token = page.request.get(base+"/api/token").json()["token"]
            (folder/"credential").write_text(token, encoding="ascii")
            (folder/"prom.yml").write_text(
                "global:\n  scrape_interval: 1s\nscrape_configs:\n  - job_name: opcua\n"
                "    authorization:\n      credentials_file: credential\n    static_configs:\n"
                f"      - targets: ['{base.split('//')[1]}']\n", encoding="ascii")
            prom = subprocess.Popen([os.environ["PROMETHEUS_BIN"], "--config.file=prom.yml",
                "--storage.tsdb.path=tsdb", "--web.listen-address=127.0.0.1:19096"], cwd=folder,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                end = time.monotonic() + 25
                result = []
                while time.monotonic() < end and prom.poll() is None:
                    try:
                        with urlopen("http://127.0.0.1:19096/api/v1/query?query=orenda_opcua_value_axis_a1_degrees",timeout=2) as r:
                            result = json.load(r)["data"]["result"]
                        if result: break
                    except OSError: pass
                    time.sleep(.4)
                assert result and result[0]["metric"]["job"] == "opcua" and -6.1 < float(result[0]["value"][1]) < -3.9, result
                print("PromQL returned the selected axis gauge from the independent Prometheus TSDB.")
                page.get_by_role("button",name="Overview",exact=True).click()
                expect(page.locator("#scrapeStatus")).to_have_text("Receiving scrapes")
                if shots: page.screenshot(path=str(Path(shots)/"overview.png"))
            finally:
                prom.terminate()
                prom.wait(timeout=12)
    page.set_viewport_size({"width":320,"height":780})
    for name in ["Channels","Events","Prometheus","Connection","Overview"]:
        page.get_by_role("button",name=name,exact=True).click()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), name
    assert not errors, errors
    browser.close()
    print("Browser commissioning, event delivery and 320 px navigation passed.")
