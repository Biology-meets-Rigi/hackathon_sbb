"""
GTFS import for the reference timetable (e.g. the Swiss yearly timetable from opentransportdata.swiss).

Supported files: stops.txt, routes.txt, trips.txt, stop_times.txt and calendar.txt and/or
calendar_dates.txt. Two ways to use it:
- Project format (strict): stop_id equals the node_id of nodes_template.csv and every pair of
  consecutive stops of a trip is an edge of edges_template.csv; anything else is an error.
- Real feed (clip_to_network, e.g. with a stop mapping): stops are mapped to nodes via the mapping
  (stop_id or its parent_station -> node_id); unmapped stops are dropped, consecutive stops of
  the same node are merged, and a trip is split where two consecutive mapped stops are no edge.
Extension column vehicle_capacity (passengers) in trips.txt or routes.txt; otherwise the default
seats per mode apply. block_id (optional) is a vehicle duty of one service day. route_type may use
the basic or the extended GTFS codes.
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

ROUTE_TYPE_TO_MODE = {0: "Tram", 1: "Metro", 2: "Zug", 3: "Bus", 4: "Schiff", 5: "Seilbahn", 6: "Luftseilbahn", 7: "Standseilbahn", 11: "Bus", 12: "Zug"}
# Extended GTFS route types (e.g. used by the Swiss feed): code ranges in hundreds
EXTENDED_ROUTE_TYPE_TO_MODE = {1: "Zug", 2: "Bus", 4: "Zug", 7: "Bus", 8: "Bus", 9: "Tram", 10: "Schiff", 12: "Schiff", 13: "Luftseilbahn", 14: "Standseilbahn", 15: "Bus", 17: "Zug"}
MODE_TO_ROUTE_TYPE = {"Zug": 2, "Bus": 3, "Schiff": 4, "Luftseilbahn": 6}
STOP_TIME_COLUMNS = ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"]


def mode_of_route_type(route_type) -> str:
    code = int(route_type)
    if code in ROUTE_TYPE_TO_MODE:
        return ROUTE_TYPE_TO_MODE[code]
    return EXTENDED_ROUTE_TYPE_TO_MODE.get(code // 100, str(code))


def load_stop_mapping(path: str) -> Dict[str, str]:
    """CSV with columns stop_id,node_id (stop_id may also be a parent_station id)."""
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    return dict(zip(df["stop_id"], df["node_id"]))
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


def _stop_nodes(stops: pd.DataFrame, node_ids: set, stop_mapping: Optional[Dict[str, str]]) -> Dict[str, str]:
    """stop_id -> node_id via the mapping (stop or its parent station) or an identical node id."""
    parents = dict(zip(stops["stop_id"], stops["parent_station"])) if "parent_station" in stops.columns else {}
    result = {}
    for stop_id in stops["stop_id"]:
        for candidate in (stop_id, parents.get(stop_id, "")):
            if not candidate:
                continue
            if stop_mapping and candidate in stop_mapping:
                result[stop_id] = stop_mapping[candidate]
                break
            if candidate in node_ids:
                result[stop_id] = candidate
                break
    return result


def _read_stop_times(feed: Path, keep_stops: Optional[set]) -> pd.DataFrame:
    """stop_times.txt in chunks (real feeds have millions of rows), optionally only the given stops."""
    path = feed / "stop_times.txt"
    if not path.exists():
        raise FileNotFoundError(f"GTFS feed {feed} is missing stop_times.txt")
    chunks = []
    for chunk in pd.read_csv(path, dtype=str, keep_default_na=False, usecols=STOP_TIME_COLUMNS, chunksize=500_000):
        chunks.append(chunk if keep_stops is None else chunk[chunk["stop_id"].isin(keep_stops)])
    return pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame(columns=STOP_TIME_COLUMNS)


def load_gtfs(
    feed_dir: str,
    edges: Dict[str, Edge],
    horizon_hours: float,
    start_date: Optional[str] = None,
    default_seats: Optional[Dict[str, int]] = None,
    min_turnaround_minutes: float = DEFAULT_TURNAROUND_MINUTES,
    stop_mapping: Optional[Dict[str, str]] = None,
    clip_to_network: Optional[bool] = None,
) -> Timetable:
    """
    Instantiate the trips of every service day in the horizon (day d starts at minute d * 1440).
    clip_to_network defaults to True when a stop mapping is given (real feed), else strict.
    """
    feed = Path(feed_dir)
    clip = bool(stop_mapping) if clip_to_network is None else clip_to_network
    stops = _read(feed, "stops.txt")
    routes = _read(feed, "routes.txt")
    trips = _read(feed, "trips.txt")
    calendar = _ServiceCalendar(_read(feed, "calendar.txt", required=False), _read(feed, "calendar_dates.txt", required=False))
    default_seats = default_seats or {}

    edges_by_pair: Dict[Tuple[str, str], List[Edge]] = {}
    for edge in edges.values():
        edges_by_pair.setdefault((edge.start_node, edge.end_node), []).append(edge)
        edges_by_pair.setdefault((edge.end_node, edge.start_node), []).append(edge)
    node_ids = {n for pair in edges_by_pair for n in pair}
    stop_node = _stop_nodes(stops, node_ids, stop_mapping)

    route_info = {}
    for row in routes.to_dict("records"):
        capacity = row.get("vehicle_capacity", "")
        route_info[row["route_id"]] = (mode_of_route_type(row["route_type"]), int(capacity) if capacity else None)

    # Trip patterns of one service day: (node, arrival, departure) per mapped stop in sequence
    stop_times = _read_stop_times(feed, set(stop_node) if clip else None)
    stop_times = stop_times.assign(stop_sequence=stop_times["stop_sequence"].astype(int)).sort_values(["trip_id", "stop_sequence"])
    patterns: Dict[str, List[Tuple[str, float, float]]] = {}
    for trip_id, group in stop_times.groupby("trip_id", sort=False):
        pattern: List[Tuple[str, float, float]] = []
        for r in group.itertuples(index=False):
            node = stop_node.get(r.stop_id)
            if node is None:
                raise ValueError(f"trip {trip_id}: stop {r.stop_id} is no node of the network")
            arrival = parse_gtfs_time(r.arrival_time or r.departure_time)
            departure = parse_gtfs_time(r.departure_time or r.arrival_time)
            if pattern and pattern[-1][0] == node:  # several platforms of one station
                pattern[-1] = (node, pattern[-1][1], departure)
            else:
                pattern.append((node, arrival, departure))
        if len(pattern) >= 2:
            patterns[trip_id] = pattern

    def pieces(trip_id: str, mode: str, pattern) -> List[List[Tuple[str, str, float, float]]]:
        """Runs of consecutive legs on edges: [(edge_id, from, departure, arrival), ...]."""
        runs, current = [], []
        for (node_a, _, dep_a), (node_b, arr_b, _) in zip(pattern, pattern[1:]):
            candidates = edges_by_pair.get((node_a, node_b), [])
            if not candidates:
                if not clip:
                    raise ValueError(f"trip {trip_id}: no edge between {node_a} and {node_b}")
                if current:
                    runs.append(current)
                current = []
                continue
            edge = next((e for e in candidates if e.mode == mode), candidates[0])
            current.append((edge.edge_id, node_a, node_b, dep_a, arr_b))
        if current:
            runs.append(current)
        return runs

    first_day = _parse_date(start_date) if start_date else calendar.first_date()
    horizon_minutes = float(horizon_hours) * 60.0
    has_blocks = "block_id" in trips.columns and (trips["block_id"] != "").any()
    trip_rows = [row for row in trips.to_dict("records") if row["trip_id"] in patterns]
    instantiated: List[Trip] = []
    duties: Dict[str, List[Trip]] = {}
    for day in range(int(math.ceil(horizon_hours / 24.0))):
        date = first_day + dt.timedelta(days=day)
        offset = day * 1440.0
        for row in trip_rows:
            if not calendar.active(row["service_id"], date):
                continue
            mode, route_capacity = route_info[row["route_id"]]
            trip_capacity = row.get("vehicle_capacity", "")
            seats = int(trip_capacity) if trip_capacity else route_capacity or default_seats.get(mode)
            if not seats:
                raise ValueError(f"trip {row['trip_id']}: no vehicle_capacity and no default seats for mode {mode}")
            runs = pieces(row["trip_id"], mode, patterns[row["trip_id"]])
            for index, run in enumerate(runs):
                if run[0][3] + offset >= horizon_minutes:
                    continue
                legs = [Leg(e, a, b, dep + offset, arr + offset) for e, a, b, dep, arr in run]
                suffix = f"#{index}" if len(runs) > 1 else ""
                trip = Trip(f"{row['trip_id']}{suffix}@{day}", row["route_id"], mode, int(seats), legs)
                instantiated.append(trip)
                if has_blocks and row.get("block_id"):
                    duties.setdefault(f"{row['block_id']}@{day}", []).append(trip)

    for duty in duties.values():
        duty.sort(key=lambda t: t.departure)
    assign_duties_to_vehicles(duties, min_turnaround_minutes)
    assign_blocks(instantiated, min_turnaround_minutes)  # trips without block_id
    return Timetable(instantiated)
