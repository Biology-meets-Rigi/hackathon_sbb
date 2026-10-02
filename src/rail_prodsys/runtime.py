"""
Runtime coupling with a prodsys simulation, attached after Runner.initialize_simulation().

PassengerDispatcher: releases passenger groups with time-of-day dependent Poisson arrivals through
the prodsys sources (whose native arrival loops are switched off) and records travel times.

InfrastructureCoupling: applies the asset layer to the running system. Vehicles are withdrawn
gracefully at night and during maintenance (no new assignments, running trips are finished), and
every hop is slowed down by the edge's condition / maintenance factor.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Callable, Dict, Generator, List, Optional, Tuple

import numpy as np
from prodsys.simulation.runner import Runner

from assets import HOURS_PER_HALF_DAY, InfrastructureState
from data_models import DAY_PERIOD_END_HOUR, PassengerFlow
from rail_prodsys.network import Line, RailNetwork, RouteVariant

MINUTES_PER_DAY = 24 * 60.0

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


class _SlowedTimeModel:
    """Vehicle travel time model wrapper: each hop takes the base time times the edge factor at hop start."""

    def __init__(self, inner, factor: Callable[[], float]) -> None:
        self._inner = inner
        self._factor = factor

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def get_next_time(self, origin=None, target=None) -> float:
        return self._inner.get_next_time(origin=origin, target=target) * self._factor()


class InfrastructureCoupling:
    def __init__(self, runner: Runner, network: RailNetwork, infra: InfrastructureState) -> None:
        self.env = runner.env
        self.network = network
        self.infra = infra
        self.router = runner.router_factory.global_system_router
        self.vehicles = {
            line_id: [runner.resource_factory.get_resource(vid) for vid in line.vehicle_ids]
            for line_id, line in network.lines.items()
        }
        self.availability_log: List[Tuple[float, str, int]] = []  # (minute, line, vehicles in service)

    def start(self, horizon_minutes: float) -> None:
        for line_id, line in self.network.lines.items():
            for vehicle in self.vehicles[line_id]:
                for transport_state in vehicle.production_states:
                    transport_state.time_model = _SlowedTimeModel(
                        transport_state.time_model, lambda edge=line.edge_id: self.slowdown(edge)
                    )
            self.env.process(self._availability_loop(line, horizon_minutes))

    def slowdown(self, edge_id: str) -> float:
        return self.infra.edge_slowdown(edge_id, self.env.now / 60.0)

    def vehicles_in_service(self, line: Line, t_hours: float) -> int:
        nominal = line.vehicles_day if (t_hours % 24.0) < DAY_PERIOD_END_HOUR else line.vehicles_night
        return max(1, round(nominal * self.infra.edge_capacity_factor(line.edge_id, t_hours)))

    def _availability_loop(self, line: Line, horizon_minutes: float) -> Generator:
        horizon_hours = horizon_minutes / 60.0
        shift_changes = np.arange(0.0, horizon_hours, HOURS_PER_HALF_DAY)  # day/night boundaries
        change_points = sorted(set(shift_changes.tolist()) | set(self.infra.edge_change_points(line.edge_id)))
        for t_hours in change_points:
            if t_hours >= horizon_hours:
                break
            yield self.env.timeout(t_hours * 60.0 - self.env.now)
            self._set_in_service(line, self.vehicles_in_service(line, t_hours))

    def _set_in_service(self, line: Line, count: int) -> None:
        """The first `count` vehicles take new assignments; the others finish their queue and then idle."""
        added = False
        for index, vehicle in enumerate(self.vehicles[line.edge_id]):
            vid = vehicle.data.ID
            if index < count:
                if vid not in self.router.free_resources:
                    self.router.free_resources[vid] = vehicle
                    added = True
            else:
                self.router.free_resources.pop(vid, None)
        if added and not self.router.resource_got_free.triggered:
            self.router.resource_got_free.succeed()  # route requests that waited for a vehicle
        self.availability_log.append((self.env.now, line.edge_id, count))


class RouteChooser:
    """
    Route choice at release (Umrouten): the variant with the smallest expected travel time.
    Per leg: travel time with the edge factor, half a headway of the vehicles in service, and the
    time to clear the groups already queued at the line's vehicles; plus the dwell time per transfer.
    """

    def __init__(self, network: RailNetwork, coupling: InfrastructureCoupling) -> None:
        self.network = network
        self.coupling = coupling

    def expected_minutes(self, variant: RouteVariant, now: float) -> float:
        t_hours = now / 60.0
        minutes = self.network.config.dwell_minutes * len(variant.transfer_stations)
        for edge_id in variant.edges:
            line = self.network.lines[edge_id]
            travel = line.base_minutes * self.coupling.infra.edge_slowdown(edge_id, t_hours)
            round_trip = 2.0 * travel
            in_service = self.coupling.vehicles_in_service(line, t_hours)
            queued = sum(len(v.controller.requests) for v in self.coupling.vehicles[edge_id])
            groups_per_minute = in_service * line.seats / round_trip
            minutes += travel + round_trip / (2.0 * in_service) + queued / groups_per_minute
        return minutes

    def __call__(self, origin: str, destination: str, now: float) -> RouteVariant:
        variants = self.network.routes[(origin, destination)]
        if len(variants) == 1:
            return variants[0]
        return min(variants, key=lambda v: self.expected_minutes(v, now))
