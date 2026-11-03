---
name: routekit
description: Plan optimized delivery routes from a CSV of U.S. addresses with the routekit CLI — one truck or a whole fleet with capacities, delivery deadlines/time windows, ready times, truck pins, and must-ship-together groups. Use when the user wants to route, sequence, or schedule deliveries, stops, or visits from a spreadsheet/CSV of addresses, or asks "what order should I visit these", "split these stops across N trucks", or "can we hit these deadlines".
---

# routekit: delivery route planning from a CSV

routekit geocodes U.S. addresses (free Census geocoder, no API key), builds routes with nearest neighbor / cheapest feasible insertion, improves them with 2-opt and inter-truck moves, and prints a summary, a terminal map, and stop tables.

## 1. Make sure it's installed

```bash
routekit --version || pip install "git+https://github.com/M-Scarnavack/routekit.git"
```

Requires Python 3.9+. If `pip` installs into a different environment than the one on PATH, use `python -m pip install ...` and run it as `python -m routekit ...`.

## 2. No CSV? Pass the addresses directly

If the user just lists addresses in chat, don't make a file; pass them as arguments. The first one is the depot:

```bash
routekit plan "1100 W Randolph St, Chicago, IL 60607" \
              "Willis Tower: 233 S Wacker Dr, Chicago, IL @ 10:30 AM" \
              "Art Institute: 111 S Michigan Ave, Chicago, IL @ 9:00-11:00" \
              "41.8663, -87.6170"
```

Per stop: `Name: address` labels it, `@ TIME` is a deadline, `@ TIME-TIME` is a window, and `lat, lon` skips geocoding. For a long list, write one stop per line to a text file and run `routekit plan - < stops.txt`. Add `--save-stops stops.csv` to keep them. Don't run bare `routekit plan` from a tool call: it's the interactive prompt for humans.

## 3. Have a CSV? Look at it before running anything

Read the header and a few rows. routekit matches column names case-insensitively:

| Meaning | Accepted column names |
|---|---|
| Stop name | `name`, `stop`, `label`, `id`, `package id`, `customer` |
| Address (one column) | `address`, `full_address`, `location` (+ `city`/`state`/`zip` are appended if present) |
| Address (split) | `street` + `city` + `state` + `zip` |
| Coordinates (skip geocoding) | `lat`/`latitude` + `lon`/`lng`/`longitude` |
| Load per stop | `demand`, `qty`, `quantity`, `units`, `packages`, `load` (default 1) |
| Deadline | `deadline`, `latest`, `due`, `window_end`, `before` |
| Not before | `earliest`, `window_start`, `open`, `after` |
| Available at depot | `ready`, `available`, `ready_at` |
| Minutes on site | `service`, `service_min`, `dwell` |
| Must use truck N | `truck`, `vehicle`, `truck_id` (1-based) |
| Must ship together | `group`, `together`, `group_id` (same value = same trip) |

Times can be `9:05`, `09:05`, `9:05 AM`, `9am`; blank or `EOD` means no limit.

If the user's columns use other names (e.g. `Customer Address`, `Due By`), write a renamed copy of the CSV with the names above rather than asking the user to edit their file. Never overwrite their original.

The **first row is the depot** (start and end) unless you pass `--depot "ADDRESS"`.

## 4. Run it

routekit draws an animated map only in an interactive terminal; from a tool call it prints the finished map automatically, so no extra flag is needed. Add `--no-map` if the user only wants tables.

**One vehicle, best order:**
```bash
routekit plan stops.csv -o route.csv
routekit plan stops.csv --one-way          # don't return to the depot
```

**Fleet:** turned on by `--trucks`, `--capacity`, `--trips`, `--drivers`, `--end`, `--service`, or any deadline/ready/truck/group column. If the user has fewer drivers than trucks, or trucks that can make more than one run, say so with `--drivers` and `--trips`.
```bash
routekit plan stops.csv --trucks 3 --capacity 14 --start 8:30 --mph 20 --service 10 -o plan.csv
```

| Option | Meaning |
|---|---|
| `--trucks N` | Trucks available (the solver adds a truck only when it saves miles or a rule requires it) |
| `--capacity C` | Max load per truck in `demand` units. Omit with `--trucks` and no rule columns to split stops evenly |
| `--start TIME` / `--end TIME` | Leave the depot at / be back by |
| `--mph N` | Average speed (default 45; use ~20 for dense cities) |
| `--service MIN` | Minutes per stop unless a `service` column overrides |
| `--trips N` | Max trips per truck: a truck returns, reloads, and goes out again (needs `--capacity` to matter) |
| `--drivers N` | At most N trucks out at once; drivers switch trucks at the depot |
| `--reload MIN` | Minutes at the depot between a truck's trips |
| `--depot ADDRESS` | Use this start/end instead of row 1 |
| `--roads` | Real road miles and drive times from OSRM (free public server; `--osrm-url` for your own). Use it when the user cares about actual drive times. If Python's SSL is too old for the public server's TLS 1.3, routekit retries through `curl` automatically; only if that fails too, suggest installing a current Python |
| `--truck` / `--height FT` / `--weight TONS` / `--length FT` / `--hazmat` | Truck-legal routing via Valhalla (each size option implies `--truck`); reports toll-road miles per leg |
| `--avoid-tolls` | Stay off toll roads and report the extra miles and minutes it costs |
| `--offline` | No network; use CSV coordinates or the geocode cache |
| `-o FILE` | Save the ordered plan (truck, order, arrival time, miles) |

## 5. Read the output and report back

- **Summary panel:** total miles, the improvement from local search, trucks used, last truck back, windows met.
- **"Could not locate"**: the Census geocoder needs a house number + street; landmark names, intersections, PO boxes, and typos fail. Tell the user which rows failed and offer to fix the addresses or add `lat`/`lon` for those rows, then re-run.
- **Tolls**: toll *miles* are approximate (a whole maneuver counts if any part is tolled), and toll *dollars* aren't available. Don't quote a dollar figure.
- **"Not delivered"**: no truck could take those stops without breaking a rule. Suggest the specific lever: more `--trucks`, higher `--capacity`, earlier `--start`, higher `--mph`, or a looser deadline. Re-run to confirm.
- Without `--roads`, distances are straight-line (great-circle) miles and real road miles run noticeably higher. Say so when quoting times or miles, or re-run with `--roads`.

Summarize for the user: trucks used, miles, each truck's stop order with arrival times, any skipped or undelivered stops, and where the `-o` file was written.
