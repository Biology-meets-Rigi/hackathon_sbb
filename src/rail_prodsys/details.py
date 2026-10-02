"""Detailed KPIs of a prodsys run (for the chosen schedule, not for every GA evaluation)."""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Dict, List, Tuple

import numpy as np

from assets import InfrastructureState
from rail_prodsys.backend import ProdsysRun


def maintenance_hours(infra: InfrastructureState, edge_id: str) -> float:
    """Hours with works on the edge (overlapping windows of several assets counted once)."""
    windows = sorted(
        (infra.windows[a].start_hour, infra.windows[a].end_hour)
        for a in infra.edge_assets.get(edge_id, [])
        if a in infra.windows
    )
    total, current_start, current_end = 0.0, None, None
    for start, end in windows:
        if current_end is None or start > current_end:
            if current_end is not None:
                total += current_end - current_start
            current_start, current_end = start, end
        else:
            current_end = max(current_end, end)
    if current_end is not None:
        total += current_end - current_start
    return total


def collect_details(run: ProdsysRun) -> dict:
    network, dispatcher, operator = run.network, run.dispatcher, run.operator
    group = network.config.group_size

    boarded: Dict[str, int] = defaultdict(int)
    left_behind: Dict[str, int] = defaultdict(int)
    seats_offered: Dict[str, int] = defaultdict(int)
    operated_seats = {t.trip_id: math.ceil(t.seats / group) for t in run.construction.operated().trips}
    for departure in operator.departures:
        boarded[departure.route_id] += departure.boarded
        left_behind[departure.route_id] += departure.left_behind
        seats_offered[departure.route_id] += operated_seats[departure.trip_id]

    trips = run.construction.summary()
    lines = {}
    for route_id, line in network.lines.items():
        lines[route_id] = {
            "mode": line.mode,
            "stations": list(line.stations),
            "vehicles": len(line.vehicle_ids),
            "seats_per_vehicle": line.seats,
            "load_factor_pct": 100.0 * boarded[route_id] / seats_offered[route_id] if seats_offered[route_id] else 0.0,
            "passengers": boarded[route_id] * group,
            "left_behind_passengers": left_behind[route_id] * group,
            "maintenance_hours": sum(maintenance_hours(run.infra, e) for e in line.edges),
            "trips": {k: (float(v) if k == "mean_delay_minutes" else int(v)) for k, v in trips.get(route_id, {}).items()},
        }

    travel: Dict[str, List[float]] = defaultdict(list)
    for product_type, released, arrived in dispatcher.trips:
        travel[product_type].append(arrived - released)

    routes = {}
    transfers: Dict[str, int] = defaultdict(int)
    for (origin, destination), variants in network.routes.items():
        released = {v.product_id: dispatcher.route_counts.get(v.product_id, 0) for v in variants}
        total = sum(released.values())
        entries = []
        for v in variants:
            trips = travel.get(v.product_id, [])
            entries.append(
                {
                    "via": " > ".join(v.stations),
                    "passengers": released[v.product_id] * group,
                    "share": released[v.product_id] / total if total else 0.0,
                    "avg_travel_min": float(np.mean(trips)) if trips else None,
                }
            )
            for station in v.transfer_stations:
                transfers[station] += released[v.product_id] * group
        all_trips = [t for v in variants for t in travel.get(v.product_id, [])]
        routes[f"{origin} > {destination}"] = {
            "avg_travel_min": float(np.mean(all_trips)) if all_trips else None,
            "variants": entries,
        }

    return {
        "backend": "prodsys",
        "group_size": group,
        "lines": lines,
        "routes": routes,
        "transfers_per_station": dict(transfers),
        "section_wait_minutes": operator.capacity.wait_minutes,
    }
