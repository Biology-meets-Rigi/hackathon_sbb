"""
prodsys simulation backend with the same interface as simulation.simulate_schedule.

The production system is built once per process and input data; every evaluation runs a fresh
prodsys Runner on a copy of it, attaches the runtime coupling and derives the objectives.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import prodsys
from prodsys.simulation import sim
from prodsys.simulation.runner import Runner

from assets import InfrastructureState
from data_models import Asset, Edge, Node, PassengerFlow, SimulationResult
from rail_prodsys.network import RailModelConfig, RailNetwork, build_network
from rail_prodsys.runtime import InfrastructureCoupling, PassengerDispatcher

sim.VERBOSE = 0  # no tqdm progress bar per run
prodsys.set_logging("WARNING")

_NETWORK_CACHE: Dict[Tuple[int, int, int, str], RailNetwork] = {}


def get_network(
    nodes: Dict[str, Node], edges: Dict[str, Edge], flows: List[PassengerFlow], config: RailModelConfig
) -> RailNetwork:
    key = (id(nodes), id(edges), id(flows), repr(config))
    if key not in _NETWORK_CACHE:
        _NETWORK_CACHE[key] = build_network(nodes, edges, flows, config)
    return _NETWORK_CACHE[key]


def simulate_schedule_prodsys(
    nodes: Dict[str, Node],
    edges: Dict[str, Edge],
    assets: Dict[str, Asset],
    flows: List[PassengerFlow],
    schedule: Dict[str, float],
    rng: np.random.Generator,
    horizon_hours: float = 24.0,
    config: Optional[RailModelConfig] = None,
) -> SimulationResult:
    config = config or RailModelConfig()
    network = get_network(nodes, edges, flows, config)
    infra = InfrastructureState(assets, schedule, horizon_hours)
    horizon_minutes = float(horizon_hours) * 60.0

    data = network.production_system.model_copy(deep=True)
    data.seed = int(rng.integers(0, 2**31 - 1))
    runner = Runner(production_system_data=data)
    runner.initialize_simulation()
    coupling = InfrastructureCoupling(runner, network, infra)
    coupling.start(horizon_minutes)
    dispatcher = PassengerDispatcher(runner, network, flows, rng)
    dispatcher.start(horizon_minutes)
    runner.run(horizon_minutes)

    group = config.group_size
    travel_minutes = [arrival - release for _, release, arrival in dispatcher.trips]
    avg_travel_hours = float(np.mean(travel_minutes)) / 60.0 if travel_minutes else 0.0
    return SimulationResult(
        avg_condition=infra.average_condition(),
        avg_travel_time=avg_travel_hours,
        served_trips=len(dispatcher.trips) * group,
        generated_trips=dispatcher.generated * group,
        failed_trips=0,
        total_cost=infra.total_cost(),
        schedule={a: w.start_hour for a, w in infra.windows.items()},
    )
