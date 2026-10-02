import math

import numpy as np
import pytest

from assets import InfrastructureState
from rail_prodsys.backend import get_network, run_prodsys, simulate_schedule_prodsys
from rail_prodsys.network import RailModelConfig, find_route_variants

HORIZON = 14.0  # hours: short prodsys runs, but long enough for a 12 h job
WORKS = {"Luzern_Weiche_1": 1.0}  # L_K_Z from 1 h to 13 h: track capacity 2 -> 1, slower


@pytest.fixture(scope="module")
def network(network_data):
    nodes, edges, _, flows = network_data
    return get_network(nodes, edges, flows, RailModelConfig(), HORIZON)


@pytest.fixture(scope="module")
def works_run(network_data):
    nodes, edges, assets, flows = network_data
    return run_prodsys(nodes, edges, assets, flows, WORKS, np.random.default_rng(5), HORIZON)


def test_vehicles_and_lines_from_timetable(network, network_data):
    _, edges, _, flows = network_data
    blocks = network.timetable.blocks()
    assert set(network.vehicle_groups) == {f"veh_{b}" for b in blocks}
    for block, trips in blocks.items():
        assert network.vehicle_groups[f"veh_{block}"] == math.ceil(max(t.seats for t in trips) / network.config.group_size)
    assert set(network.lines) == set(edges)  # synthetic timetable: one route per edge
    assert set(network.routes) == {(f.origin, f.destination) for f in flows}
    product_ids = [v.product_id for vs in network.routes.values() for v in vs]
    assert len(product_ids) == len(set(product_ids))


def test_route_variants_with_transfer(network_data):
    _, edges, _, _ = network_data
    variants = find_route_variants("Luzern", "Küsnacht_am_Rigi", edges, RailModelConfig(max_route_variants=3))
    assert variants[0] == (["Luzern", "Küsnacht_am_Rigi"], ["L_K_Z"])
    assert variants[1] == (["Luzern", "Weggis", "Küsnacht_am_Rigi"], ["W_L_S", "W_K_B"])


def test_prodsys_run(network_data):
    nodes, edges, assets, flows = network_data
    result = simulate_schedule_prodsys(nodes, edges, assets, flows, WORKS, np.random.default_rng(1), HORIZON)
    again = simulate_schedule_prodsys(nodes, edges, assets, flows, WORKS, np.random.default_rng(1), HORIZON)
    assert result == again  # deterministic for the same seed

    assert 0 < result.served_trips <= result.generated_trips
    fastest_minutes = min(e.base_travel_time_min for e in edges.values())
    assert result.avg_travel_time * 60.0 > fastest_minutes
    infra = InfrastructureState(assets, WORKS, HORIZON)
    assert result.avg_condition == pytest.approx(infra.average_condition())
    assert result.total_cost == pytest.approx(infra.total_cost())
    assert result.schedule == WORKS


def test_vehicles_follow_construction_timetable(works_run):
    """Vehicles depart exactly as in the construction timetable and never carry more than their seats."""
    operated = works_run.construction.operated().trips
    planned_legs = {(f"veh_{t.block_id}", t.trip_id, leg.departure) for t in operated for leg in t.legs}
    departed = {(d.vehicle_id, d.trip_id, d.scheduled) for d in works_run.operator.departures}
    assert departed == {leg for leg in planned_legs if leg[2] < HORIZON * 60.0}
    assert all(abs(d.actual - d.scheduled) < 1e-6 for d in works_run.operator.departures)
    assert works_run.operator.capacity.wait_minutes == 0.0  # disposition already respects track capacity
    group = works_run.network.config.group_size
    seats = {t.trip_id: math.ceil(t.seats / group) for t in operated}
    assert all(d.boarded <= seats[d.trip_id] for d in works_run.operator.departures)
    assert sum(d.boarded for d in works_run.operator.departures) > 0
    cancelled = {o.planned.trip_id for o in works_run.construction.trips if o.trip is None}
    assert cancelled and not cancelled & {d.trip_id for d in works_run.operator.departures}


def test_maintenance_slows_down_trips(network_data, works_run):
    nodes, edges, assets, flows = network_data
    no_works = simulate_schedule_prodsys(nodes, edges, assets, flows, {}, np.random.default_rng(5), HORIZON)
    assert works_run.result.generated_trips == no_works.generated_trips  # same demand (common random numbers)
    assert works_run.result.avg_travel_time > no_works.avg_travel_time


def test_route_choice_avoids_works(network_data, works_run):
    """Works on the direct line Luzern-Kuessnacht shift passengers to the detour via Weggis."""
    nodes, edges, assets, flows = network_data
    no_works = run_prodsys(nodes, edges, assets, flows, {}, np.random.default_rng(5), HORIZON)
    shares = {}
    for label, run in (("no works", no_works), ("works", works_run)):
        counts = run.dispatcher.route_counts
        direct, detour = counts["pax_Luzern_Küsnacht_am_Rigi_r0"], counts["pax_Luzern_Küsnacht_am_Rigi_r1"]
        shares[label] = detour / (direct + detour)
    assert shares["works"] > shares["no works"]


_REPRO_SCRIPT = """
import sys
sys.path.insert(0, "src")
import numpy as np
from data_models import load_assets, load_edges, load_nodes, load_passenger_flows
from rail_prodsys.backend import simulate_schedule_prodsys
n = load_nodes("data/nodes_template.csv"); e = load_edges("data/edges_template.csv")
a = load_assets("data/assets_template.csv"); f = load_passenger_flows("data/passenger_flows_day.csv")
r = simulate_schedule_prodsys(n, e, a, f, {"Luzern_Weiche_1": 1.0}, np.random.default_rng(7), 14.0)
print(repr((r.served_trips, r.avg_travel_time)))
"""


def test_reproducible_across_processes():
    """GA workers are separate processes with their own string hash seed; results must not depend on it."""
    import os
    import subprocess
    import sys

    from conftest import ROOT

    outputs = set()
    for hash_seed in ("1", "2"):
        env = dict(os.environ, PYTHONHASHSEED=hash_seed, PYTHONDONTWRITEBYTECODE="1")
        proc = subprocess.run(
            [sys.executable, "-c", _REPRO_SCRIPT], cwd=ROOT, env=env, capture_output=True, text=True, check=True
        )
        outputs.add(proc.stdout.strip())
    assert len(outputs) == 1, outputs


def test_ga_backend_selection(network_data):
    import ga

    nodes, edges, assets, flows = network_data
    results = {}
    for backend in ga.BACKENDS:
        ga._init_worker(nodes, edges, assets, flows, HORIZON, 42, backend=backend, group_size=10)
        results[backend], _ = ga._simulate(WORKS)
    expected = simulate_schedule_prodsys(nodes, edges, assets, flows, WORKS, np.random.default_rng(42), HORIZON)
    assert results["prodsys"] == expected
    assert results["simpy"].avg_condition == pytest.approx(expected.avg_condition)  # same asset layer semantics
    with pytest.raises(ValueError):
        ga._init_worker(nodes, edges, assets, flows, HORIZON, 42, backend="unknown")
