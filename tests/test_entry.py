import csv
import io

import pytest
from rich.console import Console

from routekit import cli, entry
from routekit.entry import StopTextError, collect_interactive, parse_lines, parse_stop_text, save_stops
from routekit.stops import load_stops


def test_plain_address():
    s = parse_stop_text("233 S Wacker Dr, Chicago, IL", index=1)
    assert s.address == "233 S Wacker Dr, Chicago, IL"
    assert s.name == "233 S Wacker Dr"
    assert not s.located and s.latest is None


def test_first_stop_is_labeled_depot():
    assert parse_stop_text("1100 W Randolph St, Chicago, IL", index=0).name == "Depot (1100 W Randolph St)"
    assert parse_stop_text("Shop: 1100 W Randolph St", index=0).name == "Shop"


def test_label_and_deadline():
    s = parse_stop_text("Willis Tower: 233 S Wacker Dr, Chicago, IL @ 10:30 AM", index=1)
    assert (s.name, s.address, s.earliest, s.latest) == ("Willis Tower", "233 S Wacker Dr, Chicago, IL", None, 630)


@pytest.mark.parametrize("window", ["9:00-11:00", "9:00 - 11:00", "9am to 11am", "9:00 AM–11:00 AM"])
def test_window(window):
    s = parse_stop_text(f"Art Institute: 111 S Michigan Ave @ {window}", index=1)
    assert (s.earliest, s.latest) == (540, 660)


def test_coordinates():
    s = parse_stop_text("41.8789, -87.6359", index=1)
    assert s.located and (s.lat, s.lon) == (41.8789, -87.6359) and s.address == ""
    s = parse_stop_text("Dock 4: 41.9, -87.7 @ 9:30", index=1)
    assert s.name == "Dock 4" and s.located and s.latest == 570


@pytest.mark.parametrize("bad", ["Somewhere @ lunch", "Thing: 1 Main St @ 11:00-9:00", "Label:   ", "95.0, -87.6"])
def test_bad_input_raises(bad):
    with pytest.raises(StopTextError):
        parse_stop_text(bad, index=1)


def test_blank_and_comments_skipped():
    stops = parse_lines(["# Tuesday", "", "1 Main St", "   ", "2 Main St"])
    assert [s.address for s in stops] == ["1 Main St", "2 Main St"]


def test_error_names_the_line():
    with pytest.raises(StopTextError, match="stop 2"):
        parse_lines(["1 Main St", "2 Main St @ noonish"], unit="stop")


def test_saved_stops_reload_as_csv(tmp_path):
    stops = parse_lines(["Shop: 41.88, -87.65", "Tower: 41.87, -87.63 @ 9:00-10:30"])
    path = tmp_path / "s.csv"
    save_stops(path, stops)
    again = load_stops(path)
    assert [(s.name, s.lat, s.earliest, s.latest) for s in again] == [("Shop", 41.88, None, None),
                                                                     ("Tower", 41.87, 540, 630)]


def _console(inputs):
    """A console whose input() replays the given lines."""
    lines = iter(inputs)
    c = Console(file=io.StringIO(), width=100)

    def fake_input(prompt=""):
        try:
            return next(lines)
        except StopIteration:
            raise EOFError
    c.input = fake_input
    return c


def test_interactive_session(monkeypatch):
    lookups = {"1100 W Randolph St, Chicago, IL": (41.88, -87.65, "1100 W RANDOLPH ST"),
               "233 S Wacker Dr, Chicago, IL": (41.87, -87.63, "233 S WACKER DR")}
    monkeypatch.setattr(entry, "geocode_one", lambda addr, offline=False: lookups.get(addr))
    console = _console([
        "",                                         # too early to finish: reminder
        "1100 W Randolph St, Chicago, IL",          # depot
        "Willis Tower Chicago",                     # not found: rejected
        "Tower: 233 S Wacker Dr, Chicago, IL @ 10:30 AM",
        "Oops: 41.9, -87.7",
        "undo",
        "list",
        "Dock: 41.95, -87.66",
        "",                                         # done
    ])
    stops = collect_interactive(console)
    out = console.file.getvalue()
    assert [s.name for s in stops] == ["Depot (1100 W Randolph St)", "Tower", "Dock"]
    assert stops[1].latest == 630 and stops[1].located
    assert "Couldn't find that address" in out
    assert "Removed Oops" in out
    assert "Add at least a depot" in out


def test_cli_typed_arguments(capsys):
    code = cli.main(["plan", "Shop: 41.884, -87.654", "41.879, -87.637", "41.947, -87.656", "--no-map"])
    assert code == 0
    out = capsys.readouterr().out
    assert "After 2-opt" in out and "Shop" in out


def test_cli_stdin(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("Shop: 41.884, -87.654\n# comment\n41.879, -87.637\n"))
    assert cli.main(["plan", "-", "--no-map"]) == 0
    assert "Shop" in capsys.readouterr().out


def test_cli_save_stops(tmp_path, capsys):
    out = tmp_path / "typed.csv"
    assert cli.main(["plan", "A: 41.88, -87.65", "B: 41.87, -87.63 @ 9:30", "--no-map", "--save-stops", str(out)]) == 0
    rows = list(csv.DictReader(out.open()))
    assert [r["name"] for r in rows] == ["A", "B"] and rows[1]["deadline"] == "9:30 AM"


def test_cli_rejects_too_few_stops(capsys):
    assert cli.main(["plan", "41.88, -87.65"]) == 2
    assert "at least 2 stops" in capsys.readouterr().out
