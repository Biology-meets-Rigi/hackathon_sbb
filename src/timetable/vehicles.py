"""Vehicle blocks (Umläufe): which vehicle runs which trips."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List

from timetable.model import Trip

DEFAULT_TURNAROUND_MINUTES = 2.0


@dataclass
class _Vehicle:
    vehicle_id: str
    station: str
    ready: float  # minute from which the vehicle can depart again


def assign_blocks(trips: List[Trip], min_turnaround_minutes: float = DEFAULT_TURNAROUND_MINUTES) -> None:
    """
    Greedy vehicle scheduling per route for trips without block_id: a trip takes the vehicle that
    stands at its start station and has been ready longest; otherwise a new vehicle starts there
    (pull-out from the depot). Sets trip.block_id and trip.pull_out in place.
    """
    fleet: Dict[str, List[_Vehicle]] = defaultdict(list)
    for trip in sorted(trips, key=lambda t: (t.departure, t.trip_id)):
        if trip.block_id is not None:
            continue
        vehicles = fleet[trip.route_id]
        ready = [v for v in vehicles if v.station == trip.start_station and v.ready <= trip.departure + 1e-9]
        if ready:
            vehicle = min(ready, key=lambda v: (v.ready, v.vehicle_id))
            trip.pull_out = False
        else:
            vehicle = _Vehicle(f"{trip.route_id}_v{len(vehicles)}", trip.start_station, trip.departure)
            vehicles.append(vehicle)
            trip.pull_out = True
        trip.block_id = vehicle.vehicle_id
        vehicle.station = trip.end_station
        vehicle.ready = trip.arrival + min_turnaround_minutes


def assign_duties_to_vehicles(
    duties: Dict[str, List[Trip]], min_turnaround_minutes: float = DEFAULT_TURNAROUND_MINUTES
) -> None:
    """
    GTFS blocks are duties of one service day. Map duties onto physical vehicles: a vehicle takes the
    next duty that starts after its previous duty ended (with turnaround); the first trip of every
    duty is a pull-out (overnight repositioning). Sets trip.block_id to the vehicle and trip.pull_out.
    """
    vehicles: List[_Vehicle] = []
    ordered = sorted(duties.items(), key=lambda kv: (kv[1][0].departure, kv[0]))
    for _, duty_trips in ordered:
        start = duty_trips[0].departure
        free = [v for v in vehicles if v.ready <= start + 1e-9]
        if free:
            vehicle = min(free, key=lambda v: (v.ready, v.vehicle_id))
        else:
            vehicle = _Vehicle(f"veh{len(vehicles)}", duty_trips[0].start_station, start)
            vehicles.append(vehicle)
        for index, trip in enumerate(duty_trips):
            trip.block_id = vehicle.vehicle_id
            trip.pull_out = index == 0
        vehicle.station = duty_trips[-1].end_station
        vehicle.ready = duty_trips[-1].arrival + min_turnaround_minutes
