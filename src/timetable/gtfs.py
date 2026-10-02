"""
GTFS import for the reference timetable (e.g. the Swiss yearly timetable from opentransportdata.swiss).

Supported files: stops.txt, routes.txt, trips.txt, stop_times.txt and calendar.txt and/or
calendar_dates.txt. Conventions for this project:
- stop_id equals the node_id of nodes_template.csv,
- every pair of consecutive stops of a trip is an edge of edges_template.csv,
- optional extension column vehicle_capacity (passengers) in trips.txt or routes.txt; otherwise
  the default seats per mode apply,
- block_id (optional) is a vehicle duty of one service day.
"""
from __future__ import annotations

import datetime as dt
import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from data_models import Edge
from timetable.model import Leg, Timetable, Trip
from timetable.vehicles import DEFAULT_TURNAROUND_MINUTES, assign_blocks, assign_duties_to_vehicles

ROUTE_TYPE_TO_MODE = {0: "Tram", 1: "Metro", 2: "Zug", 3: "Bus", 4: "Schiff", 5: "Seilbahn", 6: "Luftseilbahn", 7: "Standseilbahn"}
MODE_TO_ROUTE_TYPE = {"Zug": 2, "Bus": 3, "Schiff": 4, "Luftseilbahn": 6}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def parse_gtfs_time(value: str) -> float:
    """GTFS time 'H:MM:SS' (may exceed 24:00:00) -> minutes after midnight of the service day."""
    hours, minutes, seconds = (int(part) for part in str(value).strip().split(":"))
    return hours * 60.0 + minutes + seconds / 60.0


def format_gtfs_time(minutes: float) -> str:
    total_seconds = int(round(minutes * 60))
    return f"{total_seconds // 3600:02d}:{total_seconds % 3600 // 60:02d}:{total_seconds % 60:02d}"


def _read(feed_dir: Path, name: str, required: bool = True) -> Optional[pd.DataFrame]:
    path = feed_dir / name
    if not path.exists():
        if required:
            raise FileNotFoundError(f"GTFS feed {feed_dir} is missing {name}")
        return None
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def _parse_date(value: str) -> dt.date:
    return dt.datetime.strptime(str(value), "%Y%m%d").date()


class _ServiceCalendar:
    def __init__(self, calendar: Optional[pd.DataFrame], calendar_dates: Optional[pd.DataFrame]) -> None:
        self.weekly: Dict[str, Tuple[dt.date, dt.date, List[bool]]] = {}
        if calendar is not None:
            for row in calendar.itertuples(index=False):
                days = [getattr(row, day) == "1" for day in WEEKDAYS]
                self.weekly[row.service_id] = (_parse_date(row.start_date), _parse_date(row.end_date), days)
        self.exceptions: Dict[Tuple[str, dt.date], bool] = {}
        if calendar_dates is not None:
            for row in calendar_dates.itertuples(index=False):
                self.exceptions[(row.service_id, _parse_date(row.date))] = row.exception_type == "1"
        if not self.weekly and not self.exceptions:
            raise ValueError("GTFS feed needs calendar.txt or calendar_dates.txt")

    def first_date(self) -> dt.date:
        dates = [start for start, _, _ in self.weekly.values()] + [d for _, d in self.exceptions]
        return min(dates)

    def active(self, service_id: str, date: dt.date) -> bool:
        if (service_id, date) in self.exceptions:
            return self.exceptions[(service_id, date)]
        if service_id in self.weekly:
            start, end, days = self.weekly[service_id]
            return start <= date <= end and days[date.weekday()]
        return False


def load_gtfs(
    feed_dir: str,
    edges: Dict[str, Edge],
    horizon_hours: float,
    start_date: Optional[str] = None,
    default_seats: Optional[Dict[str, int]] = None,
    min_turnaround_minutes: float = DEFAULT_TURNAROUND_MINUTES,
) -> Timetable:
    """Instantiate the trips of every service day in the horizon (day d starts at minute d * 1440)."""
    feed = Path(feed_dir)
    stops = _read(feed, "stops.txt")
    routes = _read(feed, "routes.txt")
    trips = _read(feed, "trips.txt")
    stop_times = _read(feed, "stop_times.txt")
    calendar = _ServiceCalendar(_read(feed, "calendar.txt", required=False), _read(feed, "calendar_dates.txt", required=False))
    default_seats = default_seats or {}

    edge_by_pair = {}
    for edge in edges.values():
        edge_by_pair[(edge.start_node, edge.end_node)] = edge.edge_id
        edge_by_pair[(edge.end_node, edge.start_node)] = edge.edge_id
    stop_ids = set(stops["stop_id"])

    route_info = {}
    for row in routes.to_dict("records"):
        mode = ROUTE_TYPE_TO_MODE.get(int(row["route_type"]), str(row["route_type"]))
        capacity = row.get("vehicle_capacity", "")
        route_info[row["route_id"]] = (mode, int(capacity) if capacity else None)

    # Trip patterns of one service day: (stop, arrival, departure) per stop in sequence
    stop_times = stop_times.assign(stop_sequence=stop_times["stop_sequence"].astype(int)).sort_values(["trip_id", "stop_sequence"])
    patterns: Dict[str, List[Tuple[str, float, float]]] = {}
    for trip_id, group in stop_times.groupby("trip_id", sort=False):
        patterns[trip_id] = [
            (r.stop_id, parse_gtfs_time(r.arrival_time or r.departure_time), parse_gtfs_time(r.departure_time or r.arrival_time))
            for r in group.itertuples(index=False)
        ]

    first_day = _parse_date(start_date) if start_date else calendar.first_date()
    horizon_minutes = float(horizon_hours) * 60.0
    has_blocks = "block_id" in trips.columns and (trips["block_id"] != "").any()
    instantiated: List[Trip] = []
    duties: Dict[str, List[Trip]] = {}
    for day in range(int(math.ceil(horizon_hours / 24.0))):
        date = first_day + dt.timedelta(days=day)
        offset = day * 1440.0
        for row in trips.to_dict("records"):
            if not calendar.active(row["service_id"], date):
                continue
            pattern = patterns.get(row["trip_id"])
            if not pattern or len(pattern) < 2 or pattern[0][2] + offset >= horizon_minutes:
                continue
            mode, route_capacity = route_info[row["route_id"]]
            legs = []
            for (stop_a, _, dep_a), (stop_b, arr_b, _) in zip(pattern, pattern[1:]):
                for stop in (stop_a, stop_b):
                    if stop not in stop_ids:
                        raise ValueError(f"trip {row['trip_id']}: stop {stop} not in stops.txt")
                edge_id = edge_by_pair.get((stop_a, stop_b))
                if edge_id is None:
                    raise ValueError(f"trip {row['trip_id']}: no edge between {stop_a} and {stop_b}")
                legs.append(Leg(edge_id, stop_a, stop_b, dep_a + offset, arr_b + offset))
            trip_capacity = row.get("vehicle_capacity", "")
            seats = int(trip_capacity) if trip_capacity else route_capacity or default_seats.get(mode)
            if not seats:
                raise ValueError(f"trip {row['trip_id']}: no vehicle_capacity and no default seats for mode {mode}")
            trip = Trip(f"{row['trip_id']}@{day}", row["route_id"], mode, int(seats), legs)
            instantiated.append(trip)
            if has_blocks and row.get("block_id"):
                duties.setdefault(f"{row['block_id']}@{day}", []).append(trip)

    for duty in duties.values():
        duty.sort(key=lambda t: t.departure)
    assign_duties_to_vehicles(duties, min_turnaround_minutes)
    assign_blocks(instantiated, min_turnaround_minutes)  # trips without block_id
    return Timetable(instantiated)
