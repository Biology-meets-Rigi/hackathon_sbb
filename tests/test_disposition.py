import pytest

from assets import InfrastructureState
from data_models import Asset, Edge
from timetable.disposition import (
    CANCELLED,
    DELAYED,
    ON_TIME,
    REROUTED,
    DispositionConfig,
    plan_construction_timetable,
)
from timetable.gtfs import load_gtfs
from timetable.model import Leg, Timetable, Trip
from timetable.synthetic import generate_feed

HORIZON = 72.0


@pytest.fixture(scope="module")
def timetable(tmp_path_factory, network_data):
    nodes, edges, _, _ = network_data
    feed = tmp_path_factory.mktemp("gtfs")
    generate_feed(nodes, edges, str(feed))
    return load_gtfs(str(feed), edges, HORIZON)


def _max_excess(construction, edges, infra):
    """Independent check: vehicles on a section minus allowed vehicles, worst case over all sections."""
    worst = -1
    sections = [s for o in construction.trips if o.trip for leg in o.trip.legs for s in leg.sections]
    for edge_id, edge in edges.items():
        own = [s for s in sections if s.edge_id == edge_id]
        for probe in own:
            on_section = sum(1 for s in own if s.start <= probe.start < s.end)
            allowed = infra.edge_track_capacity(edge_id, edge.track_capacity, probe.start / 60.0)
            worst = max(worst, on_section - allowed)
    return worst


def test_regular_timetable_runs_unchanged(timetable, network_data):
    _, edges, assets, _ = network_data
    infra = InfrastructureState(assets, {}, HORIZON)
    construction = plan_construction_timetable(timetable, edges, infra)
    assert len(construction.trips) == len(timetable.trips)
    assert all(o.status == ON_TIME for o in construction.trips)
    assert all(o.trip.departure == o.planned.departure for o in construction.trips)
    assert _max_excess(construction, edges, infra) <= 0


def test_works_respect_reduced_capacity(timetable, network_data):
    _, edges, assets, _ = network_data
    infra = InfrastructureState(assets, {"Brücke_Luzern_Kü_2": 6.0}, HORIZON + 100)  # L_K_Z 2 -> 1 for 6 days
    construction = plan_construction_timetable(timetable, edges, infra)
    assert _max_excess(construction, edges, infra) <= 0
    summary = construction.summary()["L_K_Z"]
    assert summary[CANCELLED] > 0 and summary[DELAYED] > 0
    assert summary["planned"] == sum(summary[s] for s in ("on_time", "delayed", "rerouted", "short_turned", "cancelled"))
    untouched = construction.summary()["W_K_B"]
    assert untouched[ON_TIME] == untouched["planned"]
    for o in construction.trips:  # no trip departs before its scheduled time or later than the max delay
        if o.trip is not None:
            assert o.trip.departure >= o.planned.departure - 1e-9
            assert o.trip.departure <= o.planned.departure + DispositionConfig().max_delay_minutes + 1e-9


def _triangle():
    edges = {
        "AB": Edge("AB", "A", "B", 10, "Zug", 100, 100, 1),
        "AC": Edge("AC", "A", "C", 8, "Zug", 100, 100, 1),
        "CB": Edge("CB", "C", "B", 8, "Zug", 100, 100, 1),
    }
    assets = {"switch": Asset("switch", "AB", "Weiche", 1.0, 0.0, 1000.0, 2.0, 0.0, 0.0)}  # 24 h works on AB
    trips = [
        Trip("out", "R", "Zug", 100, [Leg("AB", "A", "B", 60.0, 70.0)], block_id="v1", pull_out=True),
        Trip("back", "R", "Zug", 100, [Leg("AB", "B", "A", 80.0, 90.0)], block_id="v1", pull_out=False),
    ]
    return edges, assets, Timetable(trips)


def test_closed_single_track_detour_or_cancel():
    edges, assets, timetable = _triangle()
    infra = InfrastructureState(assets, {"switch": 0.0}, 48.0)
    assert infra.edge_track_capacity("AB", 1, 1.0) == 0

    construction = plan_construction_timetable(timetable, edges, infra)
    out, back = construction.trips
    assert out.status == REROUTED and back.status == REROUTED
    assert [s.edge_id for s in out.trip.legs[0].sections] == ["AC", "CB"]
    assert out.trip.legs[0].from_station == "A" and out.trip.legs[0].to_station == "B"  # passengers still ride A -> B
    assert out.delay_minutes == pytest.approx(6.0)  # 16 min via C instead of 10 min

    no_detours = plan_construction_timetable(timetable, edges, infra, DispositionConfig(max_detour_sections=0))
    out, back = no_detours.trips
    assert out.status == CANCELLED and out.reason == "no capacity on AB"
    assert back.status == CANCELLED and back.reason == "vehicle not at start station"
