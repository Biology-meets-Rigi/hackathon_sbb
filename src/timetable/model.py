"""
Internal timetable model. Times are minutes since the start of the simulation horizon.

A trip runs from stop to stop; every pair of consecutive stops is one leg on one section (edge).
A vehicle block (Umlauf) is the ordered list of trips one vehicle runs.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional


@dataclass(frozen=True)
class Leg:
    edge_id: str
    from_station: str
    to_station: str
    departure: float
    arrival: float

    @property
    def run_minutes(self) -> float:
        return self.arrival - self.departure


@dataclass
class Trip:
    trip_id: str
    route_id: str
    mode: str
    seats: int  # passengers per vehicle on this trip
    legs: List[Leg]
    block_id: Optional[str] = None  # vehicle (Umlauf) running the trip
    pull_out: bool = True  # first trip of a duty: the vehicle may be positioned at the start beforehand

    @property
    def departure(self) -> float:
        return self.legs[0].departure

    @property
    def arrival(self) -> float:
        return self.legs[-1].arrival

    @property
    def start_station(self) -> str:
        return self.legs[0].from_station

    @property
    def end_station(self) -> str:
        return self.legs[-1].to_station

    def shifted(self, minutes: float) -> "Trip":
        legs = [replace(leg, departure=leg.departure + minutes, arrival=leg.arrival + minutes) for leg in self.legs]
        return replace(self, legs=legs)


@dataclass
class Timetable:
    trips: List[Trip] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.trips.sort(key=lambda t: (t.departure, t.trip_id))

    def blocks(self) -> Dict[str, List[Trip]]:
        """Trips per vehicle block in running order (trips without block_id are left out)."""
        result: Dict[str, List[Trip]] = {}
        for trip in self.trips:
            if trip.block_id is not None:
                result.setdefault(trip.block_id, []).append(trip)
        return result
