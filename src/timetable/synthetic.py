"""
Synthetic GTFS timetable derived from the network data, until the reference-year timetable is available.

Per edge and direction a regular interval timetable: day headway per mode for hours [0, 12), seats
per trip so that the day service carries capacity_at_day per 12 h, and a night headway for hours
[12, 24) that carries capacity_at_night with the same vehicles. The opposite direction runs half a
headway later, so trips do not meet on a section while the run time is below half the headway.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict

import pandas as pd

from data_models import DAY_PERIOD_END_HOUR, Edge, Node
from timetable.gtfs import MODE_TO_ROUTE_TYPE, format_gtfs_time


@dataclass
class SyntheticTimetableConfig:
    headway_day_minutes: Dict[str, float] = field(
        default_factory=lambda: {"Zug": 30.0, "Schiff": 60.0, "Bus": 30.0, "Luftseilbahn": 15.0}
    )
    start_date: str = "20260101"
    end_date: str = "20261231"


def trip_seats(edge: Edge, headway_minutes: float) -> int:
    per_hour = edge.capacity_at_day / DAY_PERIOD_END_HOUR
    return int(math.ceil(per_hour * headway_minutes / 60.0))


def night_headway(edge: Edge, seats: int) -> float:
    per_hour = edge.capacity_at_night / (24.0 - DAY_PERIOD_END_HOUR)
    return float(max(1, round(seats * 60.0 / per_hour))) if per_hour > 0 else 24 * 60.0


def generate_feed(nodes: Dict[str, Node], edges: Dict[str, Edge], out_dir: str, config: SyntheticTimetableConfig = None) -> None:
    config = config or SyntheticTimetableConfig()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    pd.DataFrame(
        [{"agency_id": "hackathon", "agency_name": "Synthetic timetable", "agency_url": "https://example.org", "agency_timezone": "Europe/Zurich"}]
    ).to_csv(out / "agency.txt", index=False)
    pd.DataFrame(
        [{"stop_id": n.node_id, "stop_name": n.node_id.replace("_", " "), "stop_lat": n.location_x, "stop_lon": n.location_y} for n in nodes.values()]
    ).to_csv(out / "stops.txt", index=False)
    pd.DataFrame(
        [
            {
                "service_id": "daily",
                **{day: 1 for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")},
                "start_date": config.start_date,
                "end_date": config.end_date,
            }
        ]
    ).to_csv(out / "calendar.txt", index=False)

    routes, trips, stop_times = [], [], []
    for edge in edges.values():
        headway_day = config.headway_day_minutes[edge.mode]
        seats = trip_seats(edge, headway_day)
        headway_night = night_headway(edge, seats)
        routes.append(
            {
                "route_id": edge.edge_id,
                "agency_id": "hackathon",
                "route_short_name": edge.edge_id,
                "route_long_name": f"{edge.start_node} - {edge.end_node}",
                "route_type": MODE_TO_ROUTE_TYPE[edge.mode],
                "vehicle_capacity": seats,
            }
        )
        for direction, (a, b) in enumerate(((edge.start_node, edge.end_node), (edge.end_node, edge.start_node))):
            for period_start, period_end, headway in (
                (0.0, DAY_PERIOD_END_HOUR * 60.0, headway_day),
                (DAY_PERIOD_END_HOUR * 60.0, 24 * 60.0, headway_night),
            ):
                departure = period_start + (headway / 2.0 if direction else 0.0)
                while departure < period_end:
                    trip_id = f"{edge.edge_id}_{direction}_{format_gtfs_time(departure)[:5].replace(':', '')}"
                    trips.append({"route_id": edge.edge_id, "service_id": "daily", "trip_id": trip_id, "direction_id": direction})
                    arrival = departure + edge.base_travel_time_min
                    stop_times.append({"trip_id": trip_id, "arrival_time": format_gtfs_time(departure), "departure_time": format_gtfs_time(departure), "stop_id": a, "stop_sequence": 1})
                    stop_times.append({"trip_id": trip_id, "arrival_time": format_gtfs_time(arrival), "departure_time": format_gtfs_time(arrival), "stop_id": b, "stop_sequence": 2})
                    departure += headway
    pd.DataFrame(routes).to_csv(out / "routes.txt", index=False)
    pd.DataFrame(trips).to_csv(out / "trips.txt", index=False)
    pd.DataFrame(stop_times).to_csv(out / "stop_times.txt", index=False)
