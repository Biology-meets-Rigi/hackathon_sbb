"""
Disposition: the construction timetable (Baufahrplan) of one maintenance plan.

Trips are planned in the order of their scheduled departure. Section capacity: at every moment at
most edge_track_capacity vehicles (reduced by works) may be on a section. Rules per trip:
  1. the vehicle has to stand at the start station and be ready (a pull-out may start anywhere),
  2. every leg departs at the earliest time >= its scheduled departure (keeping the dwell time) at
     which its section has room for the whole run; run time = scheduled run time * slowdown factor
     of the section relative to the timetable,
  3. if no slot exists within max_delay (or the section is closed), a detour over sections of the
     same mode is tried; failing that the trip ends before that leg: cancelled if it is the first
     leg, otherwise short-turned. A vehicle that does not reach the end of its trip misses the
     following trips of its block until it is back at a start station.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from assets import InfrastructureState
from data_models import Edge
from timetable.model import Leg, Section, Timetable, Trip
from timetable.vehicles import DEFAULT_TURNAROUND_MINUTES

ON_TIME = "on_time"
DELAYED = "delayed"
REROUTED = "rerouted"
SHORT_TURNED = "short_turned"
CANCELLED = "cancelled"
STATUSES = (ON_TIME, DELAYED, REROUTED, SHORT_TURNED, CANCELLED)


@dataclass
class DispositionConfig:
    max_delay_minutes: float = 30.0  # later departures are not offered; detour or cancel instead
    punctuality_minutes: float = 3.0  # arrival delay below this counts as on time
    min_turnaround_minutes: float = DEFAULT_TURNAROUND_MINUTES
    max_detour_sections: int = 4  # 0 disables detours


@dataclass
class OperatedTrip:
    planned: Trip
    trip: Optional[Trip]  # as operated; None if cancelled
    status: str
    delay_minutes: float = 0.0  # arrival delay at the last operated stop
    reason: str = ""


@dataclass
class ConstructionTimetable:
    trips: List[OperatedTrip]

    def operated(self) -> Timetable:
        return Timetable([o.trip for o in self.trips if o.trip is not None])

    def summary(self) -> Dict[str, Dict[str, float]]:
        """Per route: planned trips, trips per status and mean arrival delay of operated trips."""
        result: Dict[str, Dict[str, float]] = {}
        delays: Dict[str, List[float]] = defaultdict(list)
        for o in self.trips:
            route = result.setdefault(o.planned.route_id, {"planned": 0, **{s: 0 for s in STATUSES}})
            route["planned"] += 1
            route[o.status] += 1
            if o.trip is not None:
                delays[o.planned.route_id].append(o.delay_minutes)
        for route_id, route in result.items():
            values = delays.get(route_id, [])
            route["mean_delay_minutes"] = sum(values) / len(values) if values else 0.0
        return result


class _SectionOccupancy:
    """Scheduled occupation intervals per section and the section capacity over time."""

    def __init__(self, edges: Dict[str, Edge], infra: InfrastructureState) -> None:
        self.edges = edges
        self.infra = infra
        self.intervals: Dict[str, List[Tuple[float, float]]] = defaultdict(list)
        self._change_points = {e: [p * 60.0 for p in infra.edge_change_points(e)] for e in edges}

    def capacity(self, edge_id: str, minute: float) -> Optional[int]:
        return self.infra.edge_track_capacity(edge_id, self.edges[edge_id].track_capacity, minute / 60.0)

    def prune(self, before: float) -> None:
        for edge_id, intervals in self.intervals.items():
            self.intervals[edge_id] = [iv for iv in intervals if iv[1] > before]

    def fits(self, edge_id: str, start: float, end: float) -> bool:
        if self.edges[edge_id].track_capacity is None:
            return True
        intervals = self.intervals[edge_id]
        points = {start}
        points.update(p for iv in intervals for p in iv if start < p < end)
        points.update(p for p in self._change_points[edge_id] if start < p < end)
        for p in points:
            occupied = sum(1 for s, e in intervals if s <= p < e)
            if occupied + 1 > self.capacity(edge_id, p):
                return False
        return True

    def next_candidate(self, edge_id: str, after: float) -> Optional[float]:
        """Next moment at which the section might have room: an occupation ends or works end."""
        ends = [e for _, e in self.intervals[edge_id] if e > after]
        ends += [p for p in self._change_points[edge_id] if p > after]
        return min(ends) if ends else None

    def add(self, section: Section) -> None:
        self.intervals[section.edge_id].append((section.start, section.end))


def _find_slot(
    occupancy: _SectionOccupancy, edge_id: str, earliest: float, latest: float, run_at: Callable[[float], float]
) -> Optional[Tuple[float, float]]:
    t = earliest
    while t <= latest + 1e-9:
        run = run_at(t)
        if occupancy.fits(edge_id, t, t + run):
            return t, t + run
        t = occupancy.next_candidate(edge_id, t)
        if t is None:
            return None
    return None


class _Disposition:
    def __init__(self, edges: Dict[str, Edge], infra: InfrastructureState, config: DispositionConfig) -> None:
        self.edges = edges
        self.infra = infra
        self.config = config
        self.occupancy = _SectionOccupancy(edges, infra)
        self.adjacency: Dict[Tuple[str, str], List[Tuple[str, str]]] = defaultdict(list)  # (mode, station) -> (neighbour, edge)
        for edge in edges.values():
            self.adjacency[(edge.mode, edge.start_node)].append((edge.end_node, edge.edge_id))
            self.adjacency[(edge.mode, edge.end_node)].append((edge.start_node, edge.edge_id))

    def run_at(self, edge_id: str, scheduled_run: float) -> Callable[[float], float]:
        return lambda minute: scheduled_run * self.infra.edge_slowdown_vs_timetable(edge_id, minute / 60.0)

    def detour_paths(self, mode: str, origin: str, destination: str, avoid_edge: str) -> List[List[str]]:
        """Simple paths over sections of the same mode, fastest (by base run time) first."""
        paths: List[Tuple[float, List[str]]] = []

        def dfs(station: str, visited: List[str], path: List[str], minutes: float) -> None:
            if station == destination:
                paths.append((minutes, list(path)))
                return
            if len(path) >= self.config.max_detour_sections:
                return
            for neighbour, edge_id in self.adjacency[(mode, station)]:
                if edge_id == avoid_edge or neighbour in visited:
                    continue
                visited.append(neighbour)
                path.append(edge_id)
                dfs(neighbour, visited, path, minutes + self.edges[edge_id].base_travel_time_min)
                visited.pop()
                path.pop()

        dfs(origin, [origin], [], 0.0)
        return [p for _, p in sorted(paths, key=lambda x: x[0])]

    def schedule_detour(self, trip: Trip, leg: Leg, earliest: float, latest: float) -> Optional[Leg]:
        for path in self.detour_paths(trip.mode, leg.from_station, leg.to_station, leg.edge_id):
            t, sections = earliest, []
            for edge_id in path:
                slot = _find_slot(
                    self.occupancy, edge_id, t, t + self.config.max_delay_minutes,
                    self.run_at(edge_id, float(self.edges[edge_id].base_travel_time_min)),
                )
                if slot is None:
                    break
                sections.append(Section(edge_id, *slot))
                t = slot[1]
            else:
                if sections and sections[0].start <= latest + 1e-9:
                    return Leg(leg.edge_id, leg.from_station, leg.to_station, sections[0].start, sections[-1].end, tuple(sections))
        return None

    def schedule(self, trip: Trip, ready: float) -> Tuple[List[Leg], str]:
        legs: List[Leg] = []
        for index, leg in enumerate(trip.legs):
            if index == 0:
                earliest = max(leg.departure, ready)
            else:
                dwell = leg.departure - trip.legs[index - 1].arrival
                earliest = max(leg.departure, legs[-1].arrival + dwell)
            latest = leg.departure + self.config.max_delay_minutes
            slot = _find_slot(self.occupancy, leg.edge_id, earliest, latest, self.run_at(leg.edge_id, leg.run_minutes))
            if slot is not None:
                legs.append(Leg(leg.edge_id, leg.from_station, leg.to_station, *slot))
                continue
            detour = self.schedule_detour(trip, leg, earliest, latest) if self.config.max_detour_sections > 0 else None
            if detour is None:
                reason = "vehicle more than max delay late" if earliest > latest else f"no capacity on {leg.edge_id}"
                return legs, reason
            legs.append(detour)
        return legs, ""


def plan_construction_timetable(
    timetable: Timetable,
    edges: Dict[str, Edge],
    infra: InfrastructureState,
    config: Optional[DispositionConfig] = None,
) -> ConstructionTimetable:
    config = config or DispositionConfig()
    disposition = _Disposition(edges, infra, config)
    vehicles: Dict[str, Tuple[str, float]] = {}  # vehicle -> (station, ready from minute)
    results: List[OperatedTrip] = []
    for trip in timetable.trips:
        disposition.occupancy.prune(trip.departure)
        station, ready = vehicles.get(trip.block_id, (trip.start_station, -math.inf))
        if trip.pull_out:
            station = trip.start_station
        if station != trip.start_station:
            results.append(OperatedTrip(trip, None, CANCELLED, reason="vehicle not at start station"))
            continue

        legs, reason = disposition.schedule(trip, ready)
        if not legs:
            results.append(OperatedTrip(trip, None, CANCELLED, reason=reason))
            vehicles[trip.block_id] = (station, ready)
            continue
        for leg in legs:
            for section in leg.sections:
                disposition.occupancy.add(section)
        operated = Trip(trip.trip_id, trip.route_id, trip.mode, trip.seats, legs, trip.block_id, trip.pull_out)
        delay = legs[-1].arrival - trip.legs[len(legs) - 1].arrival
        if len(legs) < len(trip.legs):
            status = SHORT_TURNED
        elif any(leg.detour for leg in legs):
            status = REROUTED
        elif delay >= config.punctuality_minutes:
            status = DELAYED
        else:
            status = ON_TIME
        results.append(OperatedTrip(trip, operated, status, delay, reason))
        vehicles[trip.block_id] = (operated.end_station, operated.arrival + config.min_turnaround_minutes)
    return ConstructionTimetable(results)
