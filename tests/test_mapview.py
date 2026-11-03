from routekit.basemap import state_rings
from routekit.cli import animation_frames
from routekit.mapview import MapScene, bresenham
from routekit.stops import Stop


def _tour(points):
    stops = [Stop(f"S{i}", "", lat, lon) for i, (lat, lon) in enumerate(points)]
    return stops + [stops[0]]  # round trip, same object at both ends


US = [(41.88, -87.63), (40.71, -74.01), (34.05, -118.24), (29.76, -95.37), (47.61, -122.33)]
CHICAGO = [(41.88, -87.65), (41.95, -87.66), (41.87, -87.62), (41.79, -87.58)]


def test_bresenham_endpoints_and_continuity():
    pts = bresenham(0, 0, 7, 3)
    assert pts[0] == (0, 0) and pts[-1] == (7, 3)
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        assert abs(x1 - x0) <= 1 and abs(y1 - y0) <= 1


def test_basemap_data_loads():
    rings = state_rings()
    assert len(rings) > 51  # 50 states + DC, several with islands
    lon, lat = rings[0][0]
    assert -180 <= lon <= -60 and 15 <= lat <= 72


def test_frame_dimensions_and_marks():
    scene = MapScene(_tour(US), cols=60)
    lines = scene.frame().plain.split("\n")
    assert len(lines) == scene.rows
    assert all(len(line) == 60 for line in lines)
    text = scene.frame().plain
    assert text.count("■") == 1
    assert text.count("●") == len(US) - 1


def test_labels_show_stop_order():
    text = MapScene(_tour(US), cols=80).frame().plain
    for i in range(1, len(US)):
        assert str(i) in text


def test_labels_can_be_disabled():
    text = MapScene(_tour(US), cols=80, labels=False).frame().plain
    assert not any(ch.isdigit() for ch in text)


def test_basemap_on_at_national_scale_off_at_city_scale():
    assert MapScene(_tour(US), cols=80).has_basemap
    assert not MapScene(_tour(US), cols=80, basemap=False).has_basemap
    assert not MapScene(_tour(CHICAGO), cols=80).has_basemap


def test_partial_frames_reveal_stops_progressively():
    scene = MapScene(_tour(US), cols=80)
    first = scene.frame(1).plain
    assert first.count("●") == 0 and "■" in first
    halfway_leg3 = (scene.reached_at[2] + scene.reached_at[3]) // 2
    mid = scene.frame(halfway_leg3).plain
    assert mid.count("●") == 2 and mid.count("◆") == 1
    final = scene.frame().plain
    assert final.count("●") == len(US) - 1 and "◆" not in final


def test_animation_frames_cover_whole_route():
    frames = animation_frames(total_dots=1234, n_stops=30)
    assert frames[-1] == 1234
    assert frames == sorted(frames)
    assert 60 <= len(frames) <= 6 * 30 + 1
