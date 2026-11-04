import json
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest

from routekit import cli, http
from routekit.tracking import TraccarProvider, TrackingError, TrackingProvider, TruckPosition
from routekit.watch import DemoProvider, Monitor, dashboard_html, make_server

T0 = datetime(2026, 11, 3, 14, 0, tzinfo=timezone.utc)


def pos(id="1", speed=30.0, fix=T0, **attrs):
    return TruckPosition(id=id, name=f"Truck {id}", lat=41.9, lon=-87.6, speed_mph=speed, heading=90.0,
                         fix_time=fix, status="online", attributes=attrs)


class Scripted(TrackingProvider):
    """Feed the monitor one list of positions per call, on a clock we control."""
    name = "test"

    def __init__(self):
        self.now_ = T0
        self.positions = []
        self.fail = None

    def trucks(self):
        if self.fail:
            raise TrackingError(self.fail)
        return self.positions

    def now(self):
        return self.now_


def step(src, mon, minutes, positions):
    src.now_ = T0 + timedelta(minutes=minutes)
    src.positions = positions
    mon.fetched_at = 0.0
    return mon.get()


def kinds(snap):
    return sorted(a["kind"] for a in snap["alerts"])


@pytest.fixture
def setup():
    src = Scripted()
    return src, Monitor(src, stop_min=20, breakdown_min=45, offline_min=10)


def test_moving_truck_is_clear(setup):
    src, mon = setup
    snap = step(src, mon, 0, [pos()])
    assert snap["alerts"] == [] and snap["trucks"][0]["state"] == "moving"


def test_stop_warns_then_escalates_then_clears(setup):
    src, mon = setup
    step(src, mon, 0, [pos(fix=T0)])
    assert kinds(step(src, mon, 10, [pos(speed=0, fix=T0 + timedelta(minutes=9))])) == []
    snap = step(src, mon, 25, [pos(speed=0, fix=T0 + timedelta(minutes=24))])
    assert kinds(snap) == ["stop"] and snap["trucks"][0]["state"] == "warning"
    snap = step(src, mon, 50, [pos(speed=0, fix=T0 + timedelta(minutes=49))])
    assert kinds(snap) == ["long-stop"] and snap["alerts"][0]["severity"] == "critical"
    assert snap["trucks"][0]["state"] == "breakdown"
    snap = step(src, mon, 55, [pos(speed=35, fix=T0 + timedelta(minutes=55))])
    assert kinds(snap) == [] and snap["trucks"][0]["state"] == "moving"


def test_sos_latches_until_acknowledged(setup):
    src, mon = setup
    snap = step(src, mon, 0, [pos(alarm="sos")])
    assert kinds(snap) == ["alarm-sos"] and snap["alerts"][0]["severity"] == "critical"
    snap = step(src, mon, 1, [pos(fix=T0 + timedelta(minutes=1))])  # next report has no alarm
    assert kinds(snap) == ["alarm-sos"], "SOS must stay up until someone acknowledges it"
    assert mon.ack("1:alarm-sos") and not mon.ack("nope")
    assert kinds(step(src, mon, 2, [pos(fix=T0 + timedelta(minutes=2))])) == []


def test_fault_codes_and_multiple_alarms(setup):
    src, mon = setup
    snap = step(src, mon, 0, [pos(speed=0, alarm="fault,lowBattery", dtcs="P0217", batteryLevel=12)])
    assert kinds(snap) == ["alarm-fault", "alarm-lowBattery", "dtc"]
    assert snap["alerts"][0]["severity"] == "critical"
    assert "P0217" in next(a["message"] for a in snap["alerts"] if a["kind"] == "dtc")
    assert snap["trucks"][0]["battery"] == 12


def test_acked_condition_alert_stays_quiet_while_condition_lasts(setup):
    src, mon = setup
    step(src, mon, 0, [pos(dtcs="P0300")])
    mon.ack("1:dtc")
    snap = step(src, mon, 1, [pos(dtcs="P0300", fix=T0 + timedelta(minutes=1))])
    assert snap["alerts"][0]["acked"] and snap["trucks"][0]["state"] == "breakdown"
    snap = step(src, mon, 2, [pos(fix=T0 + timedelta(minutes=2))])
    assert snap["alerts"] == []


def test_lost_signal(setup):
    src, mon = setup
    snap = step(src, mon, 15, [pos(fix=T0)])
    assert kinds(snap) == ["offline"] and snap["trucks"][0]["offline"]


def test_trucks_with_problems_sort_first(setup):
    src, mon = setup
    snap = step(src, mon, 0, [pos("1"), pos("2", alarm="accident"), pos("3", fix=T0 - timedelta(minutes=30))])
    assert [t["name"] for t in snap["trucks"]] == ["Truck 2", "Truck 3", "Truck 1"]


def test_provider_error_keeps_last_data(setup):
    src, mon = setup
    step(src, mon, 0, [pos()])
    src.fail = "couldn't reach Traccar"
    snap = step(src, mon, 1, [])
    assert snap["error"] == "couldn't reach Traccar" and len(snap["trucks"]) == 1


def test_demo_day_plays_out():
    demo = DemoProvider()
    mon = Monitor(demo)
    seen = {}
    for minute in range(0, 80):
        demo.t0 = time.time() - minute * 60 / demo.SPEEDUP
        mon.fetched_at = 0.0
        for a in mon.get()["alerts"]:
            seen.setdefault((a["truck"], a["kind"]), minute)
    assert ("Truck 102", "alarm-fault") in seen and ("Truck 102", "dtc") in seen
    assert ("Truck 102", "long-stop") in seen
    assert ("Truck 103", "offline") in seen
    assert ("Truck 104", "stop") in seen
    assert ("Truck 105", "alarm-sos") in seen
    assert not any(t == "Truck 101" for t, _ in seen)


def test_traccar_keeps_attributes_and_phone(monkeypatch):
    class Resp:
        def __init__(self, d):
            self._d, self.status_code = d, 200

        def json(self):
            return self._d

    class Fake:
        def get(self, url, timeout=None, headers=None):
            if url.endswith("/api/devices"):
                return Resp([{"id": 7, "name": "Truck 7", "phone": "+15555550100", "status": "online"}])
            return Resp([{"deviceId": 7, "latitude": 41.0, "longitude": -87.0, "speed": 0,
                          "fixTime": T0.isoformat(), "attributes": {"alarm": "sos", "ignition": True}}])

    monkeypatch.setattr(http, "MIN_INTERVAL", 0.0)
    t = TraccarProvider("https://t.test", token="x", session=Fake()).trucks()[0]
    assert t.phone == "+15555550100" and t.attributes == {"alarm": "sos", "ignition": True}


def test_server_endpoints(setup):
    src, mon = setup
    src.positions = [pos(alarm="sos")]
    server = make_server(mon, port=0, poll_s=7)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        page = urllib.request.urlopen(base + "/").read()
        assert b"<title>" in page and page == dashboard_html()
        data = json.load(urllib.request.urlopen(base + "/api/fleet"))
        assert data["poll_s"] == 7 and data["alerts"][0]["id"] == "1:alarm-sos"
        req = urllib.request.Request(base + "/api/ack", data=b'{"id": "1:alarm-sos"}', method="POST")
        assert json.load(urllib.request.urlopen(req)) == {"ok": True}
    finally:
        server.shutdown()
        server.server_close()
    assert server.server_address[0] == "127.0.0.1"


def test_cli_watch_without_tracking(monkeypatch, capsys):
    monkeypatch.delenv("ROUTEKIT_TRACCAR_URL", raising=False)
    assert cli.main(["watch", "--no-open"]) == 1
    assert "routekit watch --demo" in capsys.readouterr().out
