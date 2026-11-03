# 🚚 From Class Project to Routing Tool

**C950: Data Structures and Algorithms II · Western Governors University · B.S. Computer Science**

I built this delivery-routing program for my **C950 Data Structures and Algorithms II** class at Western Governors University. After finishing the course I kept going and turned the same nearest-neighbor algorithm into **routekit**: a command-line tool that plans an optimized route through *any* list of U.S. addresses and animates it on a map in your terminal.

![routekit animating an optimized 30-city U.S. route in the terminal](docs/demo.gif)

- **[Part 1: The C950 assignment](#part-1-the-c950-assignment)**: the original WGUPS project, as submitted
- **[Part 2: What I've added since](#part-2-what-ive-added-since-routekit)**: routekit, the CLI tool it grew into

---

## Part 1: The C950 Assignment

### The assignment

C950 is the second course in WGU's data structures and algorithms sequence. The final project gives students a fictional delivery company, the **Western Governors University Parcel Service (WGUPS)**, and asks them to design and build a working routing program from scratch, with no third-party libraries:

- Deliver **40 packages** across Salt Lake City using **three trucks and two drivers**
- Keep combined mileage **under 140 miles** while meeting every delivery deadline
- Implement a **custom hash table**, without Python's built-in dictionary, for package storage and lookup
- Choose a **routing algorithm**, implement it, and analyze its time and space complexity
- Provide an **interface** that reports every package's status, and each truck's mileage, at any time of day
- Handle real-world constraints: delayed packages, truck-specific packages, packages that must ship together, and package #9's wrong address, which isn't corrected until 10:20 AM

Each truck holds 16 packages and travels at 18 mph. Deliveries start at 8:00 AM, and loading is instantaneous.

### My solution

- **Greedy nearest-neighbor routing** (`main.py`): each truck repeatedly drives to the closest undelivered stop. O(n²) per truck.
- **Custom chaining hash table** (`hash_table.py`) for average O(1) package lookup by ID
- **Time simulation:** truck clocks advance with each delivery, and package statuses update (At Hub → En Route → Delivered)
- **Truck loading:** packages are split across the trucks by ID. Package #9 is held back and delivered on a second run after its address is corrected at 10:20 AM.

**Result: all 40 packages delivered by their deadlines in 116 total miles, 24 miles under the limit.**

### Run the original project

```bash
python main.py
```

The menu lets you look up any package's status at a specific time, view all package statuses at a specific time, or view total truck mileage. Standard library only; the original WGU scenario data is in `data/`.

| File | Purpose |
|---|---|
| `main.py` | Loads data, runs the routing simulation, and provides the CLI |
| `hash_table.py` | Chaining hash table used for package storage |
| `package.py` | Package model |
| `truck.py` | Truck model (capacity, mileage, clock) |
| `data/` | WGUPS package, address, and distance CSVs |

---

## Part 2: What I've Added Since (routekit)

The C950 version only works on one fixed Salt Lake City distance table. **routekit** takes the same core idea and makes it work anywhere in the U.S.:

| | C950 project | routekit |
|---|---|---|
| **Input** | 27 fixed addresses with a precomputed distance table | Any CSV of U.S. addresses, or lat/lon coordinates |
| **Locations** | Hard-coded | Geocoded with the free U.S. Census geocoder, and cached for offline reuse |
| **Distances** | Lookup table | Great-circle (haversine) distance matrix computed on the fly |
| **Algorithm** | Nearest neighbor | Nearest neighbor **+ 2-opt** local search, reporting exactly how many miles it saved |
| **Output** | Text menu | Animated terminal map over bundled U.S. state outlines, numbered stops, a route table, and CSV export |
| **Packaging** | Single script | Installable CLI (`pip install -e .`) with 50 automated tests |

### New capabilities

- **Route any U.S. addresses.** Give it a CSV with an `address` column (or `street`/`city`/`state`/`zip`, or `lat`/`lon`), and routekit geocodes it through the [U.S. Census Bureau batch geocoder](https://geocoding.geo.census.gov/), with no API key.
- **2-opt optimization.** After nearest neighbor builds a route, 2-opt repeatedly reverses segments to remove crossings. On the 30-city demo it cuts the route from **11,737 to 10,139 miles (−13.6%)**.
- **Animated terminal map.** The route draws itself leg by leg in braille characters over U.S. state outlines from Natural Earth, with every stop numbered in driving order.
- **Real-world input handling.** Unmatched addresses are reported and skipped, geocoding results are cached, and the optimized stop order exports to CSV.

### Install and run

```bash
git clone https://github.com/M-Scarnavack/Nearest-Neighbor-Algorithm---Logistics-Project.git
cd Nearest-Neighbor-Algorithm---Logistics-Project
python -m venv .venv && source .venv/bin/activate
pip install -e .

routekit plan examples/us_cities.csv              # 30-city coast-to-coast tour
routekit plan examples/chicago_deliveries.csv     # real Chicago street addresses
routekit plan stops.csv --one-way -o route.csv    # your own stops, saved to CSV
```

| Option | Description |
|---|---|
| `--depot ADDRESS` | Start (and end) here instead of the first row |
| `--one-way` | Don't return to the depot |
| `--no-optimize` | Skip 2-opt and show the nearest-neighbor route only |
| `--mph N` | Average speed for drive-time estimates (default 45) |
| `--offline` | Use only cached or CSV coordinates; no network |
| `--no-map` / `--no-animate` | Skip the map, or draw it instantly |
| `--no-basemap` / `--no-labels` | Hide state outlines or stop numbers |
| `-o FILE` | Save the ordered route to a CSV |

Example input:

```csv
name,address
Warehouse,1100 W Randolph St Chicago IL 60607
Willis Tower,233 S Wacker Dr Chicago IL 60606
Wrigley Field,1060 W Addison St Chicago IL 60613
```

### How it works

1. **Geocode:** addresses go to the Census batch geocoder (1,000 per request) and are cached in `~/.cache/routekit/`.
2. **Distance matrix:** haversine miles between every pair of stops, O(n²).
3. **Nearest neighbor:** from the depot, always visit the closest unvisited stop. This is the same greedy strategy as the C950 project.
4. **2-opt:** remove two edges and reconnect the route the other way whenever that's shorter, until no swap helps. This untangles every crossing.
5. **Draw:** each terminal cell is a 2×4 grid of braille dots, so a 96-column map has 192×120 "pixels". Route legs are rasterized with Bresenham's line algorithm. State outlines appear above roughly 2° of map span; at city scale they'd be misleadingly coarse.

Distances are straight-line miles; road miles typically run 15–25% longer.

| Path | Purpose |
|---|---|
| `routekit/solver.py` | Haversine, nearest neighbor, 2-opt |
| `routekit/geocode.py` | Census batch geocoder with cache |
| `routekit/mapview.py` | Braille map rendering and animation frames |
| `routekit/cli.py` | `routekit plan` command |
| `tests/` | 50 pytest tests |
| `scripts/` | Basemap builder and demo-video renderer |

### Roadmap

- [x] Any-address input, 2-opt, animated terminal map
- [ ] Multiple trucks with capacities, delivery time windows, and package rules (C950's special notes, solved automatically rather than by hand)
- [ ] Real road distances via OSRM or OpenRouteService (`--roads`)
- [ ] Interactive HTML map export
- [ ] PyPI release and a Claude Code skill

### Development

```bash
pip install -e ".[dev]"
pytest -q

# Re-render the demo video and GIF (MP4 encoding uses macOS AVFoundation)
pip install -e ".[demo]"
python scripts/make_demo_video.py examples/us_cities.csv --out demo/
```

## License

routekit (`routekit/`, `tests/`, `scripts/`) is MIT licensed; see `LICENSE`. The WGUPS scenario and its data in `data/` belong to Western Governors University's C950 course. The U.S. state outlines come from [Natural Earth](https://www.naturalearthdata.com/) (public domain).
