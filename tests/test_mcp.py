import io
import json

from routekit import api, mcp_server


def _session(*messages):
    stdin = io.StringIO("".join(json.dumps(m) + "\n" for m in messages))
    stdout = io.StringIO()
    assert mcp_server.serve(stdin, stdout) == 0
    return [json.loads(line) for line in stdout.getvalue().splitlines()]


INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}


def test_handshake_and_tool_list():
    init, tools = _session(INIT, {"jsonrpc": "2.0", "method": "notifications/initialized"},
                           {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert init["result"]["protocolVersion"] == "2025-06-18"
    assert init["result"]["capabilities"] == {"tools": {"listChanged": False}}
    assert "routekit" in init["result"]["instructions"]
    names = [t["name"] for t in tools["result"]["tools"]]
    assert names == ["plan_route", "truck_locations", "fema_disasters", "freight_demand", "dat_rate",
                     "dat_search_loads", "geocode_address"]
    for t in tools["result"]["tools"]:
        assert t["inputSchema"]["type"] == "object" and "fn" not in t


def test_unknown_protocol_version_gets_latest():
    (init,) = _session({**INIT, "params": {**INIT["params"], "protocolVersion": "1999-01-01"}})
    assert init["result"]["protocolVersion"] == mcp_server.SUPPORTED_VERSIONS[0]


def test_plan_route_tool_offline():
    (_, res) = _session(INIT, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
        "name": "plan_route", "arguments": {"stops": ["Yard: 41.88, -87.63", "A: 41.95, -87.65 @ 9:30",
                                                      "B: 41.80, -87.60", "C: 41.90, -87.70"], "trucks": 2}}})
    result = res["result"]
    assert result["isError"] is False
    sc = result["structuredContent"]
    assert sc["ok"] and sc["exit_code"] == 0
    assert sorted(r["name"] for r in sc["plan"]) == ["A", "B", "C"]
    assert {r["truck"] for r in sc["plan"]} <= {"1", "2"}
    assert "routekit · fleet" in result["content"][0]["text"]


def test_tool_errors_are_reported_not_raised():
    (_, bad, unknown_tool, unknown_method) = _session(
        INIT,
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "plan_route", "arguments": {"stops": ["x", "y"], "nope": 1}}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "launch_rocket", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 6, "method": "resources/list"})
    assert bad["result"]["isError"] and "unknown option" in bad["result"]["structuredContent"]["error"]
    assert unknown_tool["error"]["code"] == -32602
    assert unknown_method["error"]["code"] == -32601


def test_parse_error_and_ping():
    stdin = io.StringIO("not json\n" + json.dumps({"jsonrpc": "2.0", "id": 9, "method": "ping"}) + "\n")
    out = io.StringIO()
    mcp_server.serve(stdin, out)
    err, ping = [json.loads(l) for l in out.getvalue().splitlines()]
    assert err["error"]["code"] == -32700 and ping["result"] == {}


def test_stdout_is_protected(monkeypatch):
    def noisy(**kw):
        print("this must not reach the protocol stream")
        return {"ok": True}
    monkeypatch.setattr(mcp_server, "_BY_NAME", {**mcp_server._BY_NAME, "truck_locations": {
        **mcp_server._BY_NAME["truck_locations"], "fn": lambda a: noisy()}})
    (_, res) = _session(INIT, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                               "params": {"name": "truck_locations", "arguments": {}}})
    assert res["result"]["structuredContent"] == {"ok": True}


def test_api_plan_route_requires_one_input():
    import pytest
    with pytest.raises(ValueError):
        api.plan_route()
    with pytest.raises(ValueError):
        api.plan_route(stops=["a", "b"], csv_path="x.csv")
