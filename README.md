# 🚚 From Class Project to Routing Tool

[![PyPI](https://img.shields.io/pypi/v/routekit-cli)](https://pypi.org/project/routekit-cli/) [![Tests](https://github.com/M-Scarnavack/routekit/actions/workflows/tests.yml/badge.svg)](https://github.com/M-Scarnavack/routekit/actions/workflows/tests.yml)

**C950: Data Structures and Algorithms II · Western Governors University · B.S. Computer Science**

I built this delivery-routing program for my **C950 Data Structures and Algorithms II** class at Western Governors University. After finishing the course I kept going and turned it into **routekit**: a command-line tool that plans optimized routes through *any* list of U.S. addresses, for one truck or a whole fleet, and animates them on a map in your terminal. Re-solving the original C950 problem, it honors every special rule, including the two-driver limit, and drives **38% fewer miles** than my class solution.

![routekit animating an optimized 30-city U.S. route in the terminal](https://raw.githubusercontent.com/M-Scarnavack/routekit/main/docs/demo.gif)

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

The C950 version only works on one fixed Salt Lake City distance table, and its truck loads were assigned by hand. **routekit** generalizes both: it routes *any* U.S. addresses, and it plans whole fleets automatically under real delivery rules.

| | C950 project | routekit |
|---|---|---|
| **Input** | 27 fixed addresses with a precomputed distance table | Addresses typed in the terminal (checked as you go), or any CSV of U.S. addresses |
| **Locations** | Hard-coded | Geocoded with the free U.S. Census geocoder, cached for offline reuse |
| **Algorithm** | Nearest neighbor | Nearest neighbor + **2-opt** for one truck; **cheapest feasible insertion + local search** (2-opt, relocate, swap) for fleets |
| **Truck loading** | By hand, by package ID | Automatic, under capacity, deadlines, time windows, ready times, truck pins, must-ship-together groups, multiple trips per truck, and a driver limit |
| **Output** | Text menu | Animated terminal map (one color per truck), schedules, and CSV export |
| **Packaging** | Single script | Installable CLI with 227 automated tests, plus a Claude Code skill |

### Head-to-head: the original C950 problem, re-solved by routekit

`scripts/solve_wgups.py` feeds the **unchanged** C950 data (`data/Package.csv`, `data/Distance.csv`) to routekit's fleet solver, converting every special note into a rule:

| C950 note | routekit rule |
|---|---|
| "Can only be on truck 2" | `truck = 2` |
| "Delayed on flight, will not arrive until 9:05 am" | `ready = 9:05 AM` |
| "Must be delivered with 13, 15" | same `group` |
| Package 9 "Wrong address listed" | corrected address, `ready = 10:20 AM` |
| 9:00 / 10:30 AM deadlines | `deadline` |

| | Original C950 solution | routekit |
|---|---|---|
| Special notes honored | Package 9 only | **All of them** |
| Two-driver limit | Not enforced (3 trucks out at once) | **Enforced**: drivers switch trucks at the hub |
| Deadlines met | 14 of 14 | **14 of 14** (tightest by 9 min) |
| Plan | 3 trucks, loaded by hand | **2 trucks, 3 trips**, planned automatically |
| Miles, counted like the original (no return legs) | 116 | **72.3** (−38%) |
| Miles including every drive back to the hub | not counted | **79.9** |

Driver 1 takes truck 1 out at 8:00. When it returns, driver 2 takes it back out with the packages that arrived at 9:05. Driver 1 then leaves on truck 2 at 10:20, the moment package 9's address is corrected.

```bash
python scripts/solve_wgups.py
```

The script independently re-checks every rule after solving, rather than trusting the solver.

### New capabilities

- **Route any U.S. addresses.** Give it a CSV with an `address` column (or `street`/`city`/`state`/`zip`, or `lat`/`lon`), and routekit geocodes it through the [U.S. Census Bureau batch geocoder](https://geocoding.geo.census.gov/), with no API key.
- **Plan a fleet.** `--trucks`, `--capacity`, `--trips` (trucks can come back and go out again), `--drivers` (at most this many trucks out at once), and optional `deadline`, `earliest`, `ready`, `truck`, and `group` columns. On the Chicago fleet example, local search cuts the initial plan from 54.6 to 36.8 miles (−33%), with every time window met.
- **Real road distances (`--roads`).** Road miles and drive times from OpenStreetMap via [OSRM](https://project-osrm.org/), free with no API key, and the map traces the actual streets. On the Chicago example, road miles run about 34% longer than straight-line.
- **Truck routing and tolls.** `--truck` routes for a tractor-trailer, or give `--height`, `--weight`, `--length`, and `--hazmat`, so low bridges, weight limits, and hazmat restrictions are respected. Every leg shows its toll-road miles. `--avoid-tolls` stays off toll roads and reports exactly what that costs: on a Chicago–Gary–O'Hare–Joliet–Naperville loop, **+6.7 mi and +43 min**. This uses the free [Valhalla](https://valhalla.github.io/valhalla/) server run by FOSSGIS.
- **2-opt optimization** for single routes: the 30-city demo drops from **11,737 to 10,139 miles (−13.6%)**.
- **Animated terminal map.** Routes draw themselves over U.S. state outlines from Natural Earth, with one color per truck and every stop numbered.
- **Interactive browser map (`--html map.html`).** One self-contained file: OpenStreetMap streets, each truck's route (on real roads with `--roads` or `--truck`), clickable stops with address, arrival time, and window, and a toggle for each truck. Open it, or send it to someone who doesn't use a terminal.

  ![routekit's HTML map of a three-truck Chicago plan on real roads](https://raw.githubusercontent.com/M-Scarnavack/routekit/main/docs/html-map.jpg)

- **Disaster relief mode (FEMA + NWS).** `routekit disasters` lists open FEMA disaster declarations from OpenFEMA (free, no key). With `--stops relief.csv`, it turns a disaster's declared counties into relief stops. `routekit plan … --fema` flags every stop in a declared county or under an active National Weather Service alert, and `--fema-within 8` makes those stops priority deliveries, due within 8 hours. Here, on live data, are three trucks from a Tallahassee staging area to the 15 Florida counties in **DR-4955, Hurricane Isaias**:

  ```bash
  routekit disasters 4955 --stops relief.csv --depot "3300 Capital Cir NE, Tallahassee, FL"
  routekit plan relief.csv --fema --fema-within 8 --truck --trucks 3 --capacity 6 \
      --service 30 --start 6:00 --html relief.html
  ```

  ![routekit relief plan for Hurricane Isaias: three trucks to 15 declared Florida counties, red-ringed stops in declared areas](https://raw.githubusercontent.com/M-Scarnavack/routekit/main/docs/relief-map.jpg)

- **Live truck locations (Traccar).** `routekit trucks` shows where every truck is: GPS position, speed, heading, and how fresh the fix is. It reads from [Traccar](https://www.traccar.org), free open-source fleet tracking (a GPS server plus a free driver phone app, or any of hundreds of GPS devices). `routekit plan … --start-at-truck "Truck 2"` plans a route from a truck's current position.
- **Agent tools (MCP server).** `routekit mcp` lets AI agents use routekit as tools: `truck_locations`, `plan_route`, `fema_disasters`, and `geocode_address`. A dispatcher can ask *"Where's truck 2? Send it to Gary, then Joliet by 11, then Naperville, on truck routes"* and the agent finds the truck, plans the route from its live position, and reports arrival times and toll miles.
- **Freight demand map (FAF5).** `routekit freight` shows where U.S. truck freight picks up and its biggest lanes, from the federal Freight Analysis Framework: 2024 tonnage by origin state, destination state, and commodity. `--from IL` shows the lanes out of Illinois, `--commodity food` narrows it to one commodity, and `--html` draws a heat map.

  ![routekit freight heat map: interstate truck freight by origin state, 2024](https://raw.githubusercontent.com/M-Scarnavack/routekit/main/docs/freight-map.jpg)

- **DAT connector** *(needs DAT API access)*. `routekit dat rate "Chicago, IL" "Atlanta, GA"` gets a RateView lane rate. `routekit dat loads --origin "Chicago, IL"` searches the load board, and `--stops` turns the pickups into stops for `routekit plan`.
- **Clear failures.** Unmatched addresses and stops that no truck can legally take are listed with how to fix them, never silently dropped.

### Install

```bash
pip install routekit-cli
routekit --version
```

The package is called `routekit-cli` on [PyPI](https://pypi.org/project/routekit-cli/), but the command is `routekit`.

Requires Python 3.9+. To work on the code instead, clone the repo and run `pip install -e ".[dev]"`.

### Use it

**No CSV needed:** type the addresses. The first one is the depot.

```bash
routekit plan "1100 W Randolph St, Chicago, IL" \
              "Willis Tower: 233 S Wacker Dr, Chicago, IL @ 10:30 AM" \
              "1060 W Addison St, Chicago, IL"
```

Or run `routekit plan` with nothing after it. It asks for stops one at a time, **checks each address as you type it**, and offers to save them as a CSV when you're done:

```text
Depot › 1100 W Randolph St, Chicago, IL 60607
  ✓ 1100 W RANDOLPH ST, CHICAGO, IL, 60607
Stop 1 › Willis Tower Chicago
✗ Couldn't find that address. Include the house number, street, city, and state...
Stop 1 › Willis Tower: 233 S Wacker Dr, Chicago, IL @ 10:30 AM
  ✓ 233 S WACKER DR, CHICAGO, IL, 60606  by 10:30 AM
Stop 2 ›
```

Each typed stop can carry a label and a time: `Name: address`, `address @ 10:30 AM` for a deadline, or `address @ 9:00-11:00` for a window. You can also type `lat, lon` in place of an address. Pipe a list in with `cat stops.txt | routekit plan -`, and save typed stops for next time with `--save-stops stops.csv`.

**With a CSV** (best for many stops, or for loads and package rules):

```bash
routekit plan stops.csv                                   # best single-truck order, round trip
routekit plan stops.csv --one-way -o route.csv            # one way, saved to CSV
routekit plan stops.csv --trucks 3 --capacity 14 \
    --start 8:30 --mph 20 --service 10 -o plan.csv        # fleet with capacities and windows
routekit plan stops.csv --trucks 3 --drivers 2 --trips 2 \
    --capacity 16 --reload 15                             # trucks go out twice; only 2 drivers
routekit plan stops.csv --truck --height 13.5 --hazmat    # truck-legal roads, with toll miles per leg
routekit plan stops.csv --truck --avoid-tolls             # stay off toll roads; see what it costs
```

Try the bundled examples: `examples/us_cities.csv` (30 cities), `examples/chicago_deliveries.csv` (real street addresses), and `examples/chicago_fleet.csv` (demands and time windows).

**Your CSV.** The first row is the depot. Column names are case-insensitive:

```csv
name,address,demand,earliest,deadline
Warehouse,1100 W Randolph St Chicago IL 60607,,,
Willis Tower,233 S Wacker Dr Chicago IL 60606,3,,9:30 AM
Art Institute,111 S Michigan Ave Chicago IL 60603,2,9:00 AM,11:00 AM
```

| Column | Meaning |
|---|---|
| `name` | Stop label (also `stop`, `id`, `customer`) |
| `address` | One-line address, or `street` + `city` + `state` + `zip`, or `lat` + `lon` |
| `demand` | Load at this stop (default 1), counted against `--capacity` |
| `earliest` / `deadline` | Delivery window: `9:00`, `9:00 AM`, `9am`; blank or `EOD` = none |
| `ready` | Freight isn't at the depot until this time |
| `service` | Minutes on site (overrides `--service`) |
| `truck` | Must ride on truck N |
| `group` | Stops with the same value ride the same trip |

| Option | Description |
|---|---|
| `--trucks N` / `--capacity C` | Fleet size and max load per trip (without `--capacity`, stops split evenly) |
| `--trips N` / `--reload MIN` | Max trips per truck, and depot time between them |
| `--drivers N` | At most N trucks out at once; drivers can switch trucks at the depot |
| `--start TIME` / `--end TIME` | Leave the depot at / be back by (default start 8:00) |
| `--service MIN` | Default minutes per stop |
| `--mph N` | Average speed (default 45; ignored with `--roads`, which uses real drive times) |
| `--roads` / `--osrm-url URL` | Real road miles, drive times, and street shapes from OSRM (default: the free public server) |
| `--truck` | Route as a truck on truck-legal roads (default 13.5 ft, 24 tons, 71 ft), with toll miles per leg |
| `--height FT` / `--weight TONS` / `--length FT` / `--width FT` / `--hazmat` | Truck size and cargo (each implies `--truck`) |
| `--avoid-tolls` | Stay off toll roads (car or truck) and report the extra miles and minutes |
| `--valhalla-url URL` | Your own Valhalla server for truck and toll routing |
| `--depot ADDRESS` | Start and end here instead of the first row |
| `--one-way` | Don't return to the depot (with fleet options: one trip per truck) |
| `--no-optimize` | Skip local search and show the construction heuristic alone |
| `--offline` | Use only CSV coordinates or the geocode cache |
| `--no-map` / `--no-animate` / `--no-basemap` / `--no-labels` | Map display options |
| `-o FILE` | Save the plan, with truck, order, arrival time, and miles, to a CSV |
| `--html FILE` | Save an interactive map to open in a browser |
| `--fema` / `--fema-days N` | Flag stops in open FEMA disaster declarations (last N days, default 120) or under active NWS alerts |
| `--fema-within HOURS` | Make declared-area stops priority deliveries, due within HOURS of the start |
| `--start-at-truck NAME` | Start from a tracked truck's live position (Traccar), one way |

**Disaster declarations:**

```bash
routekit disasters --state FL                  # open declarations in Florida (last 120 days)
routekit disasters 4955                        # one disaster and its declared counties
routekit disasters 4955 --stops relief.csv --depot "STAGING ADDRESS"   # counties → relief stops
```

Relief stops are each county's Census center point, a stand-in until you know the actual distribution sites. Data: [OpenFEMA](https://www.fema.gov/about/openfema/api) disaster declarations, [NWS](https://www.weather.gov/documentation/services-web-api) active alerts, and the Census geocoder for each stop's county. All are free U.S. government services with no key.

### Where are my trucks? (Traccar)

routekit reads live positions from a [Traccar](https://www.traccar.org) server: run your own (it's free and open source), or use Traccar's hosted service. Each driver runs the free **Traccar Client** app, or the truck has a supported GPS device. Then set:

```bash
export ROUTEKIT_TRACCAR_URL=https://your-traccar-server
export ROUTEKIT_TRACCAR_TOKEN=...        # Traccar → Settings → Preferences → Token
# (or ROUTEKIT_TRACCAR_USER and ROUTEKIT_TRACCAR_PASSWORD)

routekit trucks                          # table of positions, speed, heading, fix age
routekit trucks --json                   # for scripts and agents
routekit trucks --html trucks.html       # browser map
routekit plan stops.csv --start-at-truck "Truck 2" --truck   # route from where the truck is now
```

There's no public API for an arbitrary truck's location, so positions always come from the fleet's own tracking system. Samsara, Motive, and Geotab customers get API access to their own data, and connectors for them are on the roadmap.

### Freight demand (FAF5)

```bash
routekit freight                               # where truck freight picks up, by state (2024)
routekit freight --interstate                  # only freight that crosses state lines
routekit freight --from IL                     # biggest lanes out of Illinois
routekit freight --to TX --commodity food      # where food shipped into Texas comes from
routekit freight --interstate --html freight.html   # heat map + top lanes
```

Data: [Freight Analysis Framework](https://www.bts.gov/faf) (FAF5.7.1, BTS/FHWA, public domain), truck mode, 2018–2024. routekit bundles a 517 KB summary, which you can rebuild with `scripts/build_freight.py`. It's **annual tonnage**: the long-run shape of where loads originate and go, useful for positioning trucks and choosing lanes. It is *not* live load postings. Most truck tonnage stays within one state (short hauls of gravel, sand, and the like), so use `--interstate` for over-the-road freight.

### DAT loads and rates

```bash
export ROUTEKIT_DAT_SERVICE_USER=...      # DAT service account (from DAT)
export ROUTEKIT_DAT_SERVICE_PASSWORD=...
export ROUTEKIT_DAT_USER=dispatcher@yourco.com

routekit dat check                                        # test the credentials
routekit dat rate "Chicago, IL" "Atlanta, GA" --equipment reefer
routekit dat loads --origin "Chicago, IL" --radius 150 --stops loads.csv
```

> **Status: unverified against the live DAT API.** DAT's API needs a DAT subscription plus an approved API agreement, and its full documentation sits behind DAT's [developer portal](https://developer.dat.com). This connector follows DAT's publicly described API: a two-step login (an organization token, then a user token), RateView lane rates, and load search. Every host is configurable (`ROUTEKIT_DAT_IDENTITY_URL`, `_FREIGHT_URL`, `_ANALYTICS_URL`), responses are parsed defensively, and `--json` includes DAT's raw response, so field names can be adjusted once someone with access tests it. It uses only the API: DAT's terms prohibit scraping its website.

### Use it from an AI agent (MCP)

`routekit mcp` runs a [Model Context Protocol](https://modelcontextprotocol.io) server over stdio, so routekit's features become tools any MCP-compatible agent can call:

| Tool | What it does |
|---|---|
| `truck_locations` | Every tracked truck's live position, speed, heading, and fix age |
| `plan_route` | Plan one truck or a fleet, from typed stops or a CSV, with any CLI option (roads, truck, tolls, windows, trips, drivers, FEMA, `start_at_truck`, HTML map); returns the stop-by-stop plan as data, plus the text report |
| `fema_disasters` | Open FEMA declarations; optionally write declared counties as relief stops |
| `freight_demand` | FAF5 truck freight: top origin states and lanes, by state or commodity |
| `dat_rate` / `dat_search_loads` | DAT lane rates and load search (with DAT API access) |
| `geocode_address` | Check an address before routing to it |

Register it with **Claude Code**:

```bash
claude mcp add routekit -e ROUTEKIT_TRACCAR_URL=https://your-traccar-server \
    -e ROUTEKIT_TRACCAR_TOKEN=your-token -- routekit mcp
```

Or with **Claude Desktop** (in `claude_desktop_config.json`):

```json
{ "mcpServers": { "routekit": { "command": "routekit", "args": ["mcp"],
    "env": { "ROUTEKIT_TRACCAR_URL": "https://your-traccar-server", "ROUTEKIT_TRACCAR_TOKEN": "your-token" } } } }
```

The server is implemented directly on JSON-RPC, with no MCP SDK, so it runs on Python 3.9 like the rest of routekit.

### Use it from Claude Code

The repo includes a skill, `skills/routekit/SKILL.md`, that teaches Claude Code to install routekit, read your CSV (renaming columns if needed), pick the right options, and explain the result. Install it once:

```bash
git clone https://github.com/M-Scarnavack/routekit.git
mkdir -p ~/.claude/skills && cp -R routekit/skills/routekit ~/.claude/skills/
```

Then just ask: *"Plan routes for deliveries.csv with 3 trucks, 12 stops max each, all done by noon."*

### How it works

1. **Geocode:** addresses go to the Census batch geocoder (1,000 per request) and are cached in `~/.cache/routekit/`.
2. **Distance matrix:** haversine miles between every pair of stops, O(n²).
3. **One truck:** nearest neighbor (the C950 strategy), then **2-opt** removes route crossings until no swap helps.
4. **Fleet (VRPTW):** stops are inserted tightest deadline first, each into the trip and position that adds the fewest miles *without breaking a rule*. Then local search repeats 2-opt within trips, **relocate** (move a stop to another trip), and **swap** (exchange stops between trips) until nothing improves. Every move is checked against capacity, windows, ready times, pins, and groups.
5. **Multiple trips and drivers:** each truck gets `--trips` slots. Once trips depend on each other (a truck's second trip waits for its first, and a trip waits for a free driver), every candidate move re-simulates the whole day. Whenever a truck is loaded and a driver is free, the most urgent trip leaves.
6. **Draw:** each terminal cell is a 2×4 grid of braille dots. Route legs are rasterized with Bresenham's line algorithm. All trucks animate together, scaled to finish at the same moment.

Without `--roads`, distances are straight-line miles, and road miles run longer (about 34% on the Chicago example). With `--roads`, routekit asks an [OSRM](https://project-osrm.org/) server for the road miles and drive times between every pair of stops. It optimizes on the average of both directions, since one-way streets make A→B differ from B→A and 2-opt assumes symmetry, and it schedules with the real one-way drive times. Results are cached, and requests are spaced a second apart to respect the free demo server's policy. For heavy use, [run your own OSRM](https://github.com/Project-OSRM/osrm-backend) and pass `--osrm-url`.

**Trucks and tolls** come from [Valhalla](https://valhalla.github.io/valhalla/), which has a real truck profile on OpenStreetMap data. The public server allows 100 source×target pairs per request, so the distance table is fetched in 10×10 blocks (4 requests for 15 stops, 25 for 50). For stops more than about 93 miles apart, the public Valhalla server won't build a truck distance table. routekit then plans on OSRM's road table, which is car-based, truck-routes every final leg through Valhalla, and says so in the summary. Toll *dollar amounts* aren't in OpenStreetMap, so routekit reports toll-road miles. Valhalla flags a whole maneuver as tolled if any part of it is, so treat those miles as approximate. Valhalla's matrix can't apply a hard "no tolls" rule, so `--avoid-tolls` uses the strongest toll avoidance to build the matrix and a hard exclusion for the final routes. If a stop truly can't be reached toll-free, routekit says so.

> **Older Pythons:** the public OSRM and Valhalla servers require TLS 1.3. Some Pythons can't do it, including the 3.9 that ships with macOS. When that happens, routekit automatically sends the request through the system `curl`, which keeps it encrypted. Fleet planning takes under a second for 60 stops and about 15 seconds for 200. Multi-trip, driver-limited plans take about 2 seconds at 120 stops.

| Path | Purpose |
|---|---|
| `routekit/solver.py` | Haversine, nearest neighbor, 2-opt |
| `routekit/vrp.py` | Fleet solver: feasible insertion + local search |
| `routekit/geocode.py` | Census batch geocoder with cache |
| `routekit/roads.py` | OSRM road distances, drive times, and street shapes |
| `routekit/truck.py` | Valhalla truck routing, toll miles, and toll avoidance |
| `routekit/http.py` | Rate limiting, plus a curl fallback for Pythons without TLS 1.3 |
| `routekit/mapview.py` | Braille map rendering and animation frames |
| `routekit/htmlmap.py` | Interactive HTML map (Leaflet + OpenStreetMap) |
| `routekit/fema.py` | OpenFEMA declarations, NWS alerts, county lookup |
| `routekit/tracking.py` | Live truck locations (Traccar; provider interface for more) |
| `routekit/freight.py` | FAF5 freight demand: origins, lanes, heat-map layers |
| `routekit/dat.py` | DAT RateView and load-search connector (unverified) |
| `routekit/mcp_server.py` / `routekit/api.py` | MCP server for AI agents, and the structured API it calls |
| `routekit/cli.py` | `routekit plan` command |
| `routekit/entry.py` | Typed and interactive stop entry |
| `skills/routekit/` | Claude Code skill |
| `scripts/` | WGUPS head-to-head, basemap builder, demo-video renderer |
| `tests/` | 227 pytest tests, including randomized rule verification |

### Roadmap

- [x] Any-address input, 2-opt, animated terminal map
- [x] Multiple trucks with capacities, time windows, and package rules (the C950 special notes, solved automatically)
- [x] Claude Code skill
- [x] Multiple trips per truck and driver limits
- [x] Real road distances and drive times via OSRM (`--roads`)
- [x] Truck routing (height, weight, length, hazmat) and toll reporting and avoidance via Valhalla
- [x] Interactive HTML map export (`--html`)
- [x] Live truck locations (Traccar) and `--start-at-truck`
- [x] MCP server for AI agents (`routekit mcp`)
- [ ] Samsara, Motive, and Geotab tracking connectors (needs a customer API token to verify)
- [x] DAT load board and rate connector (`routekit dat`); still needs testing with real DAT API credentials
- [x] PyPI release: `pip install routekit-cli`
- [x] FEMA relief mode: `routekit disasters`, `--fema`, `--fema-within`, with NWS alerts on both maps
- [x] Freight-demand map from FAF5 (`routekit freight`)

### Development

```bash
pip install -e ".[dev]"
pytest -q

# Re-render the demo video and GIF (MP4 encoding uses macOS AVFoundation)
pip install -e ".[demo]"
python scripts/make_demo_video.py examples/us_cities.csv --out demo/
```

## License

routekit (`routekit/`, `tests/`, `scripts/`, `skills/`) is MIT licensed; see `LICENSE`. Bundled public-domain data: U.S. state outlines from Natural Earth, county centers from the Census Gazetteer, and truck freight flows from FAF5 (BTS/FHWA). The WGUPS scenario and its data in `data/` belong to Western Governors University's C950 course. The U.S. state outlines come from [Natural Earth](https://www.naturalearthdata.com/) (public domain).
