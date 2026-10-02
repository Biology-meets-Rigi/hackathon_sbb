"""
prodsys simulation backend with the same interface as simulation.simulate_schedule.

Per process and input data the timetable is loaded and the production system built once. Every
evaluation plans the construction timetable (Baufahrplan) for the maintenance plan, runs a fresh
prodsys Runner along it and derives the objectives.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import prodsys
from prodsys.simulation import sim
from prodsys.simulation.runner import Runner

from assets import InfrastructureState
from data_models import Asset, Edge, Node, PassengerFlow, SimulationResult
from rail_prodsys.network import RailModelConfig, RailNetwork, build_network
from rail_prodsys.runtime import PassengerDispatcher, TimetableOperator, TimetableRouteChooser
from timetable.disposition import ConstructionTimetable, plan_construction_timetable
from timetable.gtfs import load_gtfs

sim.VERBOSE = 0  # no tqdm progress bar per run
prodsys.set_logging("WARNING")

_NETWORK_CACHE: Dict[Tuple, RailNetwork] = {}


def get_network(
    nodes: Dict[str, Node],
    edges: Dict[str, Edge],
    flows: List[PassengerFlow],
    config: RailModelConfig,
    horizon_hours: float,
) -> RailNetwork:
    key = (id(nodes), id(edges), id(flows), repr(config), float(horizon_hours))
    if key not in _NETWORK_CACHE:
        timetable = load_gtfs(
            config.gtfs_dir,
            edges,
            horizon_hours,
            start_date=config.timetable_start_date,
            min_turnaround_minutes=config.disposition.min_turnaround_minutes,
        )
        _NETWORK_CACHE[key] = build_network(nodes, edges, flows, timetable, config)
    return _NETWORK_CACHE[key]


@dataclass
class ProdsysRun:
    """A finished prodsys simulation with its runtime coupling (for objectives and detailed KPIs)."""

    result: SimulationResult
    network: RailNetwork
    infra: InfrastructureState
    construction: ConstructionTimetable
    runner: Runner
    operator: TimetableOperator
    dispatcher: PassengerDispatcher


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
    return run_prodsys(nodes, edges, assets, flows, schedule, rng, horizon_hours, config).result


def run_prodsys(
    nodes: Dict[str, Node],
    edges: Dict[str, Edge],
    assets: Dict[str, Asset],
    flows: List[PassengerFlow],
    schedule: Dict[str, float],
    rng: np.random.Generator,
    horizon_hours: float = 24.0,
    config: Optional[RailModelConfig] = None,
) -> ProdsysRun:
    config = config or RailModelConfig()
    network = get_network(nodes, edges, flows, config, horizon_hours)
    infra = InfrastructureState(assets, schedule, horizon_hours)
    construction = plan_construction_timetable(network.timetable, edges, infra, config.disposition)
    horizon_minutes = float(horizon_hours) * 60.0

    data = network.production_system.model_copy(deep=True)
    data.seed = int(rng.integers(0, 2**31 - 1))
    runner = Runner(production_system_data=data)
    runner.initialize_simulation()
    operator = TimetableOperator(runner, network, construction, infra, edges)
    operator.start(horizon_minutes)
    dispatcher = PassengerDispatcher(
        runner, network, flows, rng, choose_route=TimetableRouteChooser(network, construction, operator)
    )
    dispatcher.start(horizon_minutes)
    runner.run(horizon_minutes)

    group = config.group_size
    travel_minutes = [arrival - release for _, release, arrival in dispatcher.trips]
    avg_travel_hours = float(np.mean(travel_minutes)) / 60.0 if travel_minutes else 0.0
    result = SimulationResult(
        avg_condition=infra.average_condition(),
        avg_travel_time=avg_travel_hours,
        served_trips=len(dispatcher.trips) * group,
        generated_trips=dispatcher.generated * group,
        failed_trips=0,
        total_cost=infra.total_cost(),
        schedule={a: w.start_hour for a, w in infra.windows.items()},
    )
    return ProdsysRun(result, network, infra, construction, runner, operator, dispatcher)
