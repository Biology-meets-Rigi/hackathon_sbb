"""
Runtime coupling with a prodsys simulation, attached after Runner.initialize_simulation().

PassengerDispatcher: releases passenger groups with time-of-day dependent Poisson arrivals through
the prodsys sources (whose native arrival loops are switched off) and records travel times.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Callable, Dict, Generator, List, Optional, Tuple

import numpy as np
from prodsys.simulation.runner import Runner

from data_models import PassengerFlow
from rail_prodsys.network import RailNetwork, RouteVariant

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
