"""
Runtime coupling with a prodsys simulation, attached after Runner.initialize_simulation().

PassengerDispatcher: releases passenger groups with time-of-day dependent Poisson arrivals through
the prodsys sources (whose native arrival loops are switched off) and records travel times.

TimetableOperator: runs every vehicle along the construction timetable. The line vehicles are not
in the router's free resources, so prodsys never dispatches them on demand. At every departure the
vehicle occupies its section (track capacity), takes the passenger groups waiting for exactly this
leg in waiting order up to its seats (prodsys transport requests routed to this vehicle as one lot)
and runs the leg in the time of the construction timetable. Empty legs only move the vehicle.

TimetableRouteChooser: route choice at departure from the origin (Umrouten), earliest arrival in
the operated timetable, taking the groups already waiting for the first leg into account.
"""
from __future__ import annotations

import math
from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Dict, Generator, List, Optional, Tuple

import numpy as np
import simpy
from prodsys.simulation.request import RequestType
from prodsys.simulation.runner import Runner

from assets import InfrastructureState
from data_models import Edge, PassengerFlow
from rail_prodsys.network import RailNetwork, RouteVariant
from timetable.disposition import ConstructionTimetable
from timetable.model import Leg, Trip

MINUTES_PER_DAY = 24 * 60.0
CAPACITY_BLOCK_PRIORITY = 0  # works take the section before waiting vehicles
VEHICLE_PRIORITY = 1

# (origin, destination, now in minutes) -> chosen route variant
RouteChooser = Callable[[str, str, float], RouteVariant]


class PassengerDispatcher:
    def __init__(
        self,
        runner: Runner,
        network: RailNetwork,
        flows: List[PassengerFlow],
        rng: np.random.Generator,
        choose_route: Optional[RouteChooser] = None,
    ) -> None:
        self.env = runner.env
        self.network = network
        self.flows = flows
        self.rng = rng
        self.choose_route = choose_route or (lambda o, d, now: network.routes[(o, d)][0])
        self.sources = runner.source_factory.sources

        self.generated = 0  # passenger groups released
        self.released_at: Dict[str, float] = {}  # product id -> release time
        self.trips: List[Tuple[str, float, float]] = []  # (product type, release, arrival) of finished trips
        self.route_counts: Dict[str, int] = defaultdict(int)  # product type -> groups released

        product_factory = runner.product_factory
        register_finished = product_factory.register_finished_product

        def register_and_record(product) -> None:
            released = self.released_at.pop(product.data.ID, None)
            if released is not None:
                self.trips.append((product.data.type, released, self.env.now))
            register_finished(product)

        product_factory.register_finished_product = register_and_record  # this runner instance only

    def start(self, horizon_minutes: float) -> None:
        for flow in self.flows:
            self.env.process(self._arrivals(flow, horizon_minutes))

    def _arrivals(self, flow: PassengerFlow, horizon_minutes: float) -> Generator:
        # Flow windows are hours of a day (0-24) and repeat every day; one arrival = one group.
        groups_per_minute = flow.rate_per_hour / 60.0 / self.network.config.group_size
        if groups_per_minute <= 0:
            return
        day_start = 0.0
        while day_start < horizon_minutes:
            t = day_start + flow.start_time_hour * 60.0
            window_end = min(day_start + flow.end_time_hour * 60.0, horizon_minutes)
            t += float(self.rng.exponential(1.0 / groups_per_minute))
            while t < window_end:
                yield self.env.timeout(t - self.env.now)
                yield from self._release(flow.origin, flow.destination)
                t += float(self.rng.exponential(1.0 / groups_per_minute))
            day_start += MINUTES_PER_DAY

    def _release(self, origin: str, destination: str) -> Generator:
        """Create one passenger group at the source of the chosen route (same steps as prodsys' source loop)."""
        variant = self.choose_route(origin, destination, self.env.now)
        src = self.sources[variant.source_id]
        pax = src.product_factory.create_product(src.product_data, src.data.routing_heuristic)
        pax.update_location(src)
        for queue in src.ports:  # source queues are unbounded
            yield from queue.put(pax.data)
            pax.update_location(queue)
        self.env.process(src.product_processor.process_product(pax, order_ID=None))
        self.released_at[pax.data.ID] = self.env.now
        self.generated += 1
        self.route_counts[pax.data.type] += 1


class _LegTimeModel:
    """
    Vehicle time model wrapper: a hop between two track nodes takes the run time of the current leg
    from the construction timetable; connector hops (station <-> track node) keep the inner model (~0).
    """

    def __init__(self, inner, leg_minutes: Callable[[], float], track_locations: set) -> None:
        self._inner = inner
        self._leg_minutes = leg_minutes
        self._track_locations = track_locations

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def get_next_time(self, origin=None, target=None) -> float:
        if origin is not None and target is not None:
            a, b = _location_key(origin), _location_key(target)
            if a != b and a in self._track_locations and b in self._track_locations:
                return self._leg_minutes()
        return self._inner.get_next_time(origin=origin, target=target)


def _location_key(location) -> Tuple[float, float]:
    return round(float(location[0]), 1), round(float(location[1]), 1)


class SectionCapacity:
    """Runtime safety net for track capacity: one slot per vehicle on a section, works hold slots."""

    def __init__(self, env: simpy.Environment, edges: Dict[str, Edge], infra: InfrastructureState) -> None:
        self.env = env
        self.edges = edges
        self.infra = infra
        self.sections = {
            e: simpy.PriorityResource(env, capacity=edge.track_capacity) for e, edge in edges.items() if edge.track_capacity
        }
        self.wait_minutes = 0.0  # total time vehicles waited for a section (0 if the disposition is consistent)

    def start(self, horizon_minutes: float) -> None:
        for edge_id in self.sections:
            self.env.process(self._works_controller(edge_id, horizon_minutes))

    def _works_controller(self, edge_id: str, horizon_minutes: float) -> Generator:
        section = self.sections[edge_id]
        held: List[simpy.resources.resource.PriorityRequest] = []
        for t_hours in self.infra.edge_change_points(edge_id):
            if t_hours * 60.0 >= horizon_minutes:
                break
            yield self.env.timeout(t_hours * 60.0 - self.env.now)
            allowed = self.infra.edge_track_capacity(edge_id, section.capacity, t_hours)
            while len(held) < section.capacity - allowed:
                held.append(section.request(priority=CAPACITY_BLOCK_PRIORITY))
            while len(held) > section.capacity - allowed:
                request = held.pop()
                if request.triggered:
                    section.release(request)
                else:
                    request.cancel()

    def occupy(self, edge_id: str) -> Generator:
        """Wait for a slot on the section; returns the request (or None for sections without capacity)."""
        section = self.sections.get(edge_id)
        if section is None:
            return None
        requested = self.env.now
        request = section.request(priority=VEHICLE_PRIORITY)
        yield request
        self.wait_minutes += self.env.now - requested
        return request

    def hold_then_continue(self, first_request, leg: Leg) -> Generator:
        """Keep the first section for its run time, then occupy the remaining sections of a detour in turn."""
        sections = leg.sections
        yield self.env.timeout(sections[0].end - sections[0].start)
        if first_request is not None:
            self.sections[sections[0].edge_id].release(first_request)
        for section in sections[1:]:
            request = yield from self.occupy(section.edge_id)
            yield self.env.timeout(section.end - section.start)
            if request is not None:
                self.sections[section.edge_id].release(request)


@dataclass
class Departure:
    vehicle_id: str
    trip_id: str
    route_id: str
    edge_id: str
    scheduled: float  # departure in the construction timetable
    actual: float
    boarded: int  # passenger groups
    left_behind: int  # groups waiting for this leg that did not fit


class TimetableOperator:
    def __init__(
        self, runner: Runner, network: RailNetwork, construction: ConstructionTimetable, infra: InfrastructureState, edges: Dict[str, Edge]
    ) -> None:
        self.env = runner.env
        self.network = network
        self.construction = construction
        self.router = runner.router_factory.global_system_router
        self.resource_factory = runner.resource_factory
        self.capacity = SectionCapacity(self.env, edges, infra)
        self.departures: List[Departure] = []
        self._leg_minutes: Dict[str, float] = {}
        self._trips_per_vehicle: Dict[str, List[Trip]] = defaultdict(list)
        for trip in construction.operated().trips:
            self._trips_per_vehicle[f"veh_{trip.block_id}"].append(trip)

        # Station resources and their queues -> station (passengers board from the station queue)
        self.station_of: Dict[str, str] = {}
        self.stations = {}
        for station in {s for variants in network.routes.values() for v in variants for s in v.stations} | {
            s for line in network.lines.values() for s in line.stations
        }:
            resource = self.resource_factory.get_resource(f"station_{station}")
            self.stations[station] = resource
            for locatable in [resource] + list(resource.ports):
                self.station_of[locatable.data.ID] = station
        self._walkway_ids = {r.ID for r in network.production_system.resource_data if r.ID.startswith("walkway_")}
        self.track_locations = {
            _location_key(runner.node_factory.get_node(f"node_{s}").get_location()) for s in self.stations
        }

    def start(self, horizon_minutes: float) -> None:
        self.capacity.start(horizon_minutes)
        for station in self.stations:
            # Walkways start at their station queue: a free-floating start position needs prodsys'
            # nearest-locatable link, which is ambiguous when entrances and exits share coordinates.
            walkway = self.resource_factory.get_resource(f"walkway_{station}") if f"walkway_{station}" in self._walkway_ids else None
            if walkway is not None:
                self._park(walkway, station)
        for vehicle_id in self.network.vehicle_groups:
            vehicle = self.resource_factory.get_resource(vehicle_id)
            self.router.free_resources.pop(vehicle_id, None)  # never dispatched on demand
            for transport_state in vehicle.production_states:
                transport_state.time_model = _LegTimeModel(
                    transport_state.time_model, lambda vid=vehicle_id: self._leg_minutes.get(vid, 0.0), self.track_locations
                )
            self.env.process(self._run_vehicle(vehicle, self._trips_per_vehicle.get(vehicle_id, [])))

    def _run_vehicle(self, vehicle, trips: List[Trip]) -> Generator:
        vehicle_id = vehicle.data.ID
        for trip in trips:
            for index, leg in enumerate(trip.legs):
                if self.env.now < leg.departure:
                    yield self.env.timeout(leg.departure - self.env.now)
                if index == 0 and trip.pull_out:
                    self._park(vehicle, trip.start_station)
                first_section = yield from self.capacity.occupy(leg.sections[0].edge_id)
                self.env.process(self.capacity.hold_then_continue(first_section, leg))
                self._leg_minutes[vehicle_id] = leg.run_minutes
                seats = min(self.network.vehicle_groups[vehicle_id], math.ceil(trip.seats / self.network.config.group_size))
                boarded, left_behind = self._board(vehicle, leg, seats)
                self.departures.append(
                    Departure(vehicle_id, trip.trip_id, trip.route_id, leg.edge_id, leg.departure, self.env.now, len(boarded), left_behind)
                )
                if boarded:
                    yield simpy.AllOf(self.env, [info.request_completion_event for info in boarded])
                else:
                    yield self.env.timeout(leg.run_minutes)
                    self._park(vehicle, leg.to_station)

    def _park(self, vehicle, station: str) -> None:
        # A queue of the station, not the resource: prodsys' route graph consists of the queues.
        vehicle.set_location(self.stations[station].ports[0])

    def waiting_groups(self, origin: str, destination: str) -> int:
        """Passenger groups waiting for a vehicle from origin to the next station destination."""
        return sum(1 for _ in self._waiting(origin, destination, vehicle_id=None))

    def _waiting(self, origin: str, destination: str, vehicle_id: Optional[str]):
        handler = self.router.request_handler
        for key in handler.pending_resource_requests:
            info = handler.request_infos[key]
            if info.request_type != RequestType.TRANSPORT:
                continue
            if vehicle_id is not None and vehicle_id not in info.resource_mappings:
                continue
            if self.station_of.get(info.origin.data.ID) == origin and self.station_of.get(info.target.data.ID) == destination:
                yield key, info

    def _board(self, vehicle, leg: Leg, seats: int):
        """Route the waiting transport requests of this leg to the vehicle (what prodsys' router does)."""
        vehicle_id = vehicle.data.ID
        waiting = list(self._waiting(leg.from_station, leg.to_station, vehicle_id))
        chosen = waiting[:seats]
        handler = self.router.request_handler
        for key, info in chosen:
            request = handler.create_resource_request(info, vehicle, info.resource_mappings[vehicle_id][0])
            handler.pending_requests[id(request.completed)] = info
            handler.pending_resource_requests.remove(key)
            handler.mark_routing(request)
            self.env.process(self.router.execute_resource_routing(request))
        return [info for _, info in chosen], len(waiting) - len(chosen)


class TimetableRouteChooser:
    """Earliest arrival over the operated timetable; the first leg skips departures already full."""

    def __init__(self, network: RailNetwork, construction: ConstructionTimetable, operator: TimetableOperator) -> None:
        self.network = network
        self.operator = operator
        group = network.config.group_size
        legs: Dict[Tuple[str, str], List[Tuple[float, float, int]]] = defaultdict(list)
        for trip in construction.operated().trips:
            for leg in trip.legs:
                legs[(leg.from_station, leg.to_station)].append((leg.departure, leg.arrival, math.ceil(trip.seats / group)))
        self.legs = {hop: sorted(entries) for hop, entries in legs.items()}
        self.departures = {hop: [e[0] for e in entries] for hop, entries in self.legs.items()}

    def expected_arrival(self, variant: RouteVariant, now: float) -> float:
        t = now
        for index, hop in enumerate(zip(variant.stations, variant.stations[1:])):
            if index > 0:
                t += self.network.config.dwell_minutes  # transfer
            entries = self.legs.get(hop)
            if not entries:
                return math.inf
            k = bisect_left(self.departures[hop], t)
            if index == 0:  # skip departures the groups already waiting will fill
                waiting = self.operator.waiting_groups(*hop)
                while k < len(entries) and waiting >= entries[k][2]:
                    waiting -= entries[k][2]
                    k += 1
            if k >= len(entries):
                return math.inf
            t = entries[k][1]
        return t

    def __call__(self, origin: str, destination: str, now: float) -> RouteVariant:
        variants = self.network.routes[(origin, destination)]
        if len(variants) == 1:
            return variants[0]
        return min(variants, key=lambda v: (self.expected_arrival(v, now), v.index))
