# 🚚 WGUPS Routing Program

A delivery-routing simulation for the fictional Western Governors University Parcel Service (WGUPS). It plans routes for three trucks and two drivers delivering 40 packages across Salt Lake City, meeting every deadline and special constraint while keeping total distance under 140 miles.

**Result:** all 40 packages delivered on time in **116 total miles**.

## Problem Scenario

- Each truck carries up to 16 packages and travels at 18 mph
- Deliveries start no earlier than 8:00 AM; loading and unloading are instantaneous
- Some packages have special constraints, e.g. package #9 has an incorrect address until 10:20 AM
- Supervisors must be able to see each package's status, delivery time, and truck progress at any point in the day

## Approach

- **Greedy nearest-neighbor routing:** each truck repeatedly drives to the closest undelivered stop
- **Custom hash table** (`hash_table.py`) for constant-time package lookup by ID
- **Time simulation:** truck positions and package statuses (At Hub → En Route → Delivered) are updated as the clock advances
- **Constraint handling:** truck capacity, delivery deadlines, delayed packages, and the mid-day address correction

## Tech Stack

- **Language:** Python (standard library only)
- **Data structures:** hash table, graph (distance matrix), lists
- **Algorithm:** greedy nearest neighbor

## How to Run

```bash
git clone https://github.com/M-Scarnavack/Nearest-Neighbor-Algorithm---Logistics-Project.git
cd Nearest-Neighbor-Algorithm---Logistics-Project
python main.py
```

The CLI menu lets you:

1. Look up a single package's status at a specific time
2. View the status of all packages at a specific time
3. View total mileage across all trucks

## Project Structure

| File | Purpose |
|---|---|
| `main.py` | Loads data, runs the routing simulation, and provides the CLI |
| `hash_table.py` | Chaining hash table used for package storage |
| `package.py` | Package model |
| `truck.py` | Truck model (capacity, mileage, clock) |
| `data/` | Package, address, and distance CSVs |
