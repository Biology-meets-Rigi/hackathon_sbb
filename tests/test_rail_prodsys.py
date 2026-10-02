import numpy as np
import pytest

from assets import InfrastructureState
from rail_prodsys.backend import get_network, simulate_schedule_prodsys
from rail_prodsys.network import RailModelConfig, find_route_variants

HORIZON = 14.0  # hours: short prodsys runs, but long enough for a 12 h job


@pytest.fixture(scope="module")
def network(network_data):
    nodes, edges, _, flows = network_data
    return get_network(nodes, edges, flows, RailModelConfig())


def test_lines_carry_nominal_capacity(network, network_data):
    _, edges, _, _ = network_data
    config = network.config
    assert set(network.lines) == set(edges)
    for edge_id, line in network.lines.items():
        per_hour = line.vehicles_day * line.seats * config.group_size / (line.round_trip_minutes / 60.0)
        assert per_hour >= edges[edge_id].capacity_at_day / 12.0
        assert 1 <= line.vehicles_night <= line.vehicles_day


def test_routes_and_ids(network, network_data):
    _, _, _, flows = network_data
    assert set(network.routes) == {(f.origin, f.destination) for f in flows}
    product_ids = [v.product_id for vs in network.routes.values() for v in vs]
    assert len(product_ids) == len(set(product_ids))
    for (o, d), variants in network.routes.items():
        assert variants[0].stations[0] == o and variants[0].stations[-1] == d


def test_route_variants_with_transfer(network_data):
    _, edges, _, _ = network_data
    config = RailModelConfig(max_route_variants=3)
    variants = find_route_variants("Luzern", "Küsnacht_am_Rigi", edges, config)
    assert variants[0] == (["Luzern", "Küsnacht_am_Rigi"], ["L_K_Z"])
    assert variants[1] == (["Luzern", "Weggis", "Küsnacht_am_Rigi"], ["W_L_S", "W_K_B"])


def test_prodsys_run(network_data):
    nodes, edges, assets, flows = network_data
    plan = {"Luzern_Weiche_1": 1.0}
    result = simulate_schedule_prodsys(nodes, edges, assets, flows, plan, np.random.default_rng(1), HORIZON)
    again = simulate_schedule_prodsys(nodes, edges, assets, flows, plan, np.random.default_rng(1), HORIZON)
    assert result == again  # deterministic for the same seed

    assert 0 < result.served_trips <= result.generated_trips
    fastest_minutes = min(e.base_travel_time_min for e in edges.values())
    assert result.avg_travel_time * 60.0 > fastest_minutes
    infra = InfrastructureState(assets, plan, HORIZON)
    assert result.avg_condition == pytest.approx(infra.average_condition())
    assert result.total_cost == pytest.approx(infra.total_cost())
    assert result.schedule == {"Luzern_Weiche_1": 1.0}
