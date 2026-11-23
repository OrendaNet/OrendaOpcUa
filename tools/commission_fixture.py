"""Configure only the loopback simulator for native container qualification."""
import json
from urllib.request import Request, urlopen

base = "http://127.0.0.1:3100/api/"
url = "opc.tcp://127.0.0.1:4841/plant/"


def call(path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    with urlopen(Request(base + path, data=data, headers={"Content-Type":"application/json"}), timeout=15) as r:
        return json.load(r)


def update(path, **fields):
    return call(path, {**fields, "revision":call("state")["revision"]})


pin = call("discover", {"url":url})["fingerprint"]
update("connect", url=url, fingerprint=pin)
robot = next(row for row in call("browse")["rows"] if row["name"] == "Robot")
from urllib.parse import urlencode
axis = next(row for row in call("browse?" + urlencode({"id":robot["id"]}))["rows"] if row["name"] == "ActualPosition")
update("add", id=axis["id"], metric="axis_a1_degrees")
