import numpy as np
import pytest

from assets import InfrastructureState, maintenance_duration_hours, maintenance_fits_horizon
from simulation import NetworkSimulator, simulate_schedule

HORIZON = 72.0
PLAN = {"AG_Kü_Weiche_3": 10.0, "Luzern_Weiche_1": 30.0, "Brücke_Luzern_Kü_2": 50.0}  # bridge (144 h) is deferred


def test_duration_and_deferral(network_data):
    _, _, assets, _ = network_data
    bridge = assets["Brücke_Luzern_Kü_2"]
    assert maintenance_duration_hours(bridge) == 144.0
    assert maintenance_fits_horizon(bridge, 0.0, 144.0)
    assert not maintenance_fits_horizon(bridge, 0.1, 144.0)

    infra = InfrastructureState(assets, PLAN, HORIZON)
    assert set(infra.windows) == {"AG_Kü_Weiche_3", "Luzern_Weiche_1"}
    assert infra.deferred_assets == ["Brücke_Luzern_Kü_2"]


def test_condition_before_during_after_works(network_data):
    _, _, assets, _ = network_data
    infra = InfrastructureState(assets, PLAN, HORIZON)
    a = assets["AG_Kü_Weiche_3"]  # class 5 -> 0.2, 4 half-days -> works 10..58 h
    assert infra.asset_condition("AG_Kü_Weiche_3", 0.0) == pytest.approx(a.condition_initial)
    degraded = a.condition_initial - a.condition_transition_rate * 20 / 24
    assert infra.asset_condition("AG_Kü_Weiche_3", 20.0) == pytest.approx(min(degraded, infra.condition_during_works))
    assert infra.asset_condition("AG_Kü_Weiche_3", 58.0) == pytest.approx(1.0)
    assert infra.asset_condition("AG_Kü_Weiche_3", 70.0) == pytest.approx(1.0 - a.condition_transition_rate * 12 / 24)


def test_edge_effects(network_data):
    _, _, assets, _ = network_data
    infra = InfrastructureState(assets, PLAN, HORIZON)
    assert infra.edge_under_maintenance("K_A_Z", 20.0)
    assert not infra.edge_under_maintenance("K_A_Z", 60.0)
    assert infra.edge_capacity_factor("K_A_Z", 20.0) == infra.capacity_factor
    assert infra.edge_capacity_factor("W_K_B", 20.0) == 1.0
    cond = infra.edge_condition("K_A_Z", 20.0)
    assert infra.edge_slowdown("K_A_Z", 20.0) == pytest.approx((2.0 - cond) * infra.slowdown_factor)
    assert infra.edge_change_points("K_A_Z") == [10.0, 58.0]
    assert infra.edge_condition("UNKNOWN_EDGE", 0.0) == 1.0


def test_parity_with_simpy_model(network_data):
    """The asset layer reproduces the condition and cost semantics of the SimPy simulation."""
    nodes, edges, assets, flows = network_data
    infra = InfrastructureState(assets, PLAN, HORIZON)

    sim = NetworkSimulator(nodes, edges, assets, flows, PLAN, HORIZON, np.random.default_rng(0))
    for t in np.arange(0.0, HORIZON, 0.5):  # SimPy's condition has side effects: query in time order
        for asset_id in assets:
            assert infra.asset_condition(asset_id, float(t)) == pytest.approx(sim._asset_condition(asset_id, float(t)))

    result = simulate_schedule(nodes, edges, assets, flows, PLAN, np.random.default_rng(0), HORIZON)
    assert infra.average_condition() == pytest.approx(result.avg_condition)
    assert infra.total_cost() == pytest.approx(result.total_cost)


def test_slowdown_relative_to_timetable(network_data):
    _, _, assets, _ = network_data
    infra = InfrastructureState(assets, {"Luzern_Weiche_1": 30.0}, HORIZON)  # works on L_K_Z 30..42 h
    assert infra.edge_slowdown_vs_timetable("L_K_Z", 0.0) == pytest.approx(1.0)  # timetable state
    assert 1.0 < infra.edge_slowdown_vs_timetable("L_K_Z", 29.0) < 1.05  # degradation since the start
    assert infra.edge_slowdown_vs_timetable("L_K_Z", 31.0) > infra.slowdown_factor - 1e-9  # works
    assert infra.edge_slowdown_vs_timetable("W_K_B", 0.0) == pytest.approx(1.0)
