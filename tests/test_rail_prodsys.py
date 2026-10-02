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


def test_coupling_vehicles_and_slowdown(network, network_data):
    from prodsys.simulation.runner import Runner

    from rail_prodsys.runtime import InfrastructureCoupling

    _, _, assets, _ = network_data
    plan = {"Luzern_Weiche_1": 1.0}  # works on L_K_Z from 1 h to 13 h
    infra = InfrastructureState(assets, plan, HORIZON)
    runner = Runner(production_system_data=network.production_system.model_copy(deep=True))
    runner.initialize_simulation()
    coupling = InfrastructureCoupling(runner, network, infra)

    line = network.lines["L_K_Z"]
    assert coupling.vehicles_in_service(line, 0.5) == line.vehicles_day
    assert coupling.vehicles_in_service(line, 5.0) == round(line.vehicles_day * infra.capacity_factor)
    assert coupling.vehicles_in_service(line, 13.5) == line.vehicles_night  # night, works finished
    other = network.lines["W_K_B"]
    assert coupling.vehicles_in_service(other, 5.0) == other.vehicles_day

    coupling.start(HORIZON * 60.0)
    runner.run(HORIZON * 60.0)
    in_service = {(round(t), line_id): n for t, line_id, n in coupling.availability_log}
    assert in_service[(60, "L_K_Z")] == 2 and in_service[(720, "L_K_Z")] == 1 and in_service[(780, "L_K_Z")] == 2
    vehicle = runner.resource_factory.get_resource(line.vehicle_ids[0])
    assert all(hasattr(s.time_model, "_inner") for s in vehicle.production_states)


def test_maintenance_slows_down_trips(network_data):
    nodes, edges, assets, flows = network_data
    no_works = simulate_schedule_prodsys(nodes, edges, assets, flows, {}, np.random.default_rng(3), HORIZON)
    works = simulate_schedule_prodsys(
        nodes, edges, assets, flows, {"Luzern_Weiche_1": 1.0}, np.random.default_rng(3), HORIZON
    )
    assert works.generated_trips == no_works.generated_trips  # same demand (common random numbers)
    assert works.avg_travel_time > no_works.avg_travel_time
