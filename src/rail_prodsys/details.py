"""Detailed KPIs of a prodsys run (for the chosen schedule, not for every GA evaluation)."""
from __future__ import annotations

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
    network, dispatcher = run.network, run.dispatcher
    group = network.config.group_size

    # prodsys resource states: PR = driving (loaded or empty), WT = waiting for a job
    states = run.runner.get_post_processor().df_aggregated_resource_states
    driving = states[states["Time_type"] == "PR"].set_index("Resource")["percentage"]

    lines = {}
    for line_id, line in network.lines.items():
        lines[line_id] = {
            "mode": line.mode,
            "stations": list(line.stations),
            "vehicles_day": line.vehicles_day,
            "vehicles_night": line.vehicles_night,
            "seats_per_vehicle": line.seats * group,
            "driving_share_pct": float(np.mean([driving.get(v, 0.0) for v in line.vehicle_ids])),
            "maintenance_hours": maintenance_hours(run.infra, line_id),
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
    }
