from collections import defaultdict

import pandas as pd
import pytest

from assets import InfrastructureState
from data_models import Edge
from timetable.gtfs import load_gtfs, parse_gtfs_time
from timetable.synthetic import generate_feed
from timetable.vehicles import DEFAULT_TURNAROUND_MINUTES


@pytest.fixture(scope="module")
def synthetic_feed(tmp_path_factory, network_data):
    nodes, edges, _, _ = network_data
    feed = tmp_path_factory.mktemp("gtfs")
    generate_feed(nodes, edges, str(feed))
    return str(feed)


def test_synthetic_feed_carries_capacity(synthetic_feed, network_data):
    _, edges, _, _ = network_data
    timetable = load_gtfs(synthetic_feed, edges, 24.0)
    for edge in edges.values():
        outbound = [t for t in timetable.trips if t.route_id == edge.edge_id and t.start_station == edge.start_node]
        seats = outbound[0].seats
        day = sum(t.seats for t in outbound if t.departure < 720)
        night = sum(t.seats for t in outbound if t.departure >= 720)
        assert edge.capacity_at_day <= day < edge.capacity_at_day + seats
        assert abs(night - edge.capacity_at_night) < seats
        assert all(leg.run_minutes == edge.base_travel_time_min for t in outbound for leg in t.legs)


def test_vehicle_blocks_are_consistent(synthetic_feed, network_data):
    _, edges, _, _ = network_data
    timetable = load_gtfs(synthetic_feed, edges, 48.0)
    assert len({t.trip_id for t in timetable.trips}) == len(timetable.trips)
    for vehicle, trips in timetable.blocks().items():
        assert trips[0].pull_out
        for previous, trip in zip(trips, trips[1:]):
            if trip.pull_out:
                continue
            assert previous.end_station == trip.start_station
            assert trip.departure >= previous.arrival + DEFAULT_TURNAROUND_MINUTES - 1e-9


def _write_feed(path, stop_times, trips, calendar_dates=None):
    pd.DataFrame([{"stop_id": s, "stop_name": s, "stop_lat": 0, "stop_lon": 0} for s in ("A", "B", "C")]).to_csv(path / "stops.txt", index=False)
    pd.DataFrame([{"route_id": "R1", "route_type": 2}]).to_csv(path / "routes.txt", index=False)
    pd.DataFrame(trips).to_csv(path / "trips.txt", index=False)
    pd.DataFrame(stop_times).to_csv(path / "stop_times.txt", index=False)
    pd.DataFrame(
        [{"service_id": "wd", "monday": 1, "tuesday": 1, "wednesday": 1, "thursday": 1, "friday": 1, "saturday": 0, "sunday": 0,
          "start_date": "20260105", "end_date": "20261231"}]  # 2026-01-05 is a Monday
    ).to_csv(path / "calendar.txt", index=False)
    if calendar_dates:
        pd.DataFrame(calendar_dates).to_csv(path / "calendar_dates.txt", index=False)


def test_gtfs_import(tmp_path):
    edges = {
        "AB": Edge("AB", "A", "B", 10, "Zug", 100, 50, 1),
        "BC": Edge("BC", "B", "C", 10, "Zug", 100, 50, 1),
    }
    trips = [
        {"route_id": "R1", "service_id": "wd", "trip_id": "late", "block_id": "blk1", "vehicle_capacity": 120},
        {"route_id": "R1", "service_id": "wd", "trip_id": "back", "block_id": "blk1", "vehicle_capacity": 120},
    ]
    stop_times = [
        {"trip_id": "late", "arrival_time": "23:50:00", "departure_time": "23:50:00", "stop_id": "A", "stop_sequence": 1},
        {"trip_id": "late", "arrival_time": "24:00:00", "departure_time": "24:02:00", "stop_id": "B", "stop_sequence": 2},
        {"trip_id": "late", "arrival_time": "24:12:00", "departure_time": "24:12:00", "stop_id": "C", "stop_sequence": 3},
        {"trip_id": "back", "arrival_time": "08:00:00", "departure_time": "08:00:00", "stop_id": "C", "stop_sequence": 1},
        {"trip_id": "back", "arrival_time": "08:10:00", "departure_time": "08:10:00", "stop_id": "B", "stop_sequence": 2},
    ]
    # Tuesday removed, Saturday added
    exceptions = [{"service_id": "wd", "date": "20260106", "exception_type": 2}, {"service_id": "wd", "date": "20260110", "exception_type": 1}]
    _write_feed(tmp_path, stop_times, trips, exceptions)

    assert parse_gtfs_time("24:12:00") == 24 * 60 + 12
    timetable = load_gtfs(str(tmp_path), edges, horizon_hours=6 * 24)
    days = sorted({int(t.trip_id.split("@")[1]) for t in timetable.trips})
    assert days == [0, 2, 3, 4, 5]  # Mon, (Tue removed), Wed, Thu, Fri, Sat (added)
    late = next(t for t in timetable.trips if t.trip_id == "late@0")
    assert [leg.edge_id for leg in late.legs] == ["AB", "BC"]
    assert late.legs[1].departure == 24 * 60 + 2 and late.arrival == 24 * 60 + 12
    assert late.seats == 120
    # one GTFS block per day is a duty; duties are mapped onto the same physical vehicle
    assert {t.block_id for t in timetable.trips} == {"veh0"}
    assert all(t.pull_out == t.trip_id.startswith("back") for t in timetable.trips)

    stop_times[1]["stop_id"] = "C"  # A -> C is no edge
    _write_feed(tmp_path, stop_times, trips)
    with pytest.raises(ValueError, match="no edge between A and C"):
        load_gtfs(str(tmp_path), edges, horizon_hours=24)


def test_works_reduce_track_capacity(network_data):
    _, _, assets, _ = network_data
    infra = InfrastructureState(assets, {"Luzern_Weiche_1": 0.0}, 48.0)  # works on L_K_Z 0..12 h
    assert infra.edge_track_capacity("L_K_Z", 2, 1.0) == 1
    assert infra.edge_track_capacity("L_K_Z", 1, 1.0) == 0  # single track closed
    assert infra.edge_track_capacity("L_K_Z", 2, 13.0) == 2
    assert infra.edge_track_capacity("L_K_Z", None, 1.0) is None


def test_route_type_modes():
    from timetable.gtfs import mode_of_route_type

    assert [mode_of_route_type(c) for c in (2, 3, 4, 6, 106, 700, 1000, 1300, 1400)] == [
        "Zug", "Bus", "Schiff", "Luftseilbahn", "Zug", "Bus", "Schiff", "Luftseilbahn", "Standseilbahn"
    ]


def test_real_feed_is_clipped_to_the_network(tmp_path, network_data):
    """Swiss-style feed: platforms with parent stations, stops outside the model, extended route types."""
    _, edges, _, _ = network_data
    pd.DataFrame(
        [
            {"stop_id": "Parent8505000", "stop_name": "Luzern", "parent_station": ""},
            {"stop_id": "8505000:0:1", "stop_name": "Luzern", "parent_station": "Parent8505000"},
            {"stop_id": "8505000:0:2", "stop_name": "Luzern", "parent_station": "Parent8505000"},
            {"stop_id": "8505201", "stop_name": "Meggen", "parent_station": ""},  # not modelled
            {"stop_id": "8505110", "stop_name": "Kuessnacht", "parent_station": ""},
            {"stop_id": "8508000", "stop_name": "Arth-Goldau", "parent_station": ""},
            {"stop_id": "8503000", "stop_name": "Zuerich HB", "parent_station": ""},  # outside the network
        ]
    ).to_csv(tmp_path / "stops.txt", index=False)
    pd.DataFrame([{"route_id": "S3", "route_type": 106}]).to_csv(tmp_path / "routes.txt", index=False)
    pd.DataFrame(
        [{"route_id": "S3", "service_id": "all", "trip_id": t} for t in ("through", "split", "platforms")]
    ).to_csv(tmp_path / "trips.txt", index=False)
    rows = []
    for trip, stops in {
        "through": [("8503000", "06:00"), ("8505000:0:1", "06:50"), ("8505201", "06:58"), ("8505110", "07:06"), ("8508000", "07:16")],
        "split": [("8505000:0:2", "08:00"), ("8508000", "08:20"), ("8505110", "08:30")],  # Luzern-Arth is no edge
        "platforms": [("8505000:0:1", "09:00"), ("8505000:0:2", "09:03"), ("8505110", "09:18")],
    }.items():
        for seq, (stop, time) in enumerate(stops, start=1):
            rows.append({"trip_id": trip, "arrival_time": f"{time}:00", "departure_time": f"{time}:00", "stop_id": stop, "stop_sequence": seq})
    pd.DataFrame(rows).to_csv(tmp_path / "stop_times.txt", index=False)
    pd.DataFrame([{"service_id": "all", "date": "20260105", "exception_type": 1}]).to_csv(tmp_path / "calendar_dates.txt", index=False)
    mapping = {"Parent8505000": "Luzern", "8505110": "Küsnacht_am_Rigi", "8508000": "Arth_Goldau"}

    timetable = load_gtfs(str(tmp_path), edges, 24.0, default_seats={"Zug": 400}, stop_mapping=mapping)
    trips = {t.trip_id: t for t in timetable.trips}
    assert set(trips) == {"through@0", "split@0", "platforms@0"}
    through = trips["through@0"]
    assert [(l.edge_id, l.from_station, l.to_station) for l in through.legs] == [
        ("L_K_Z", "Luzern", "Küsnacht_am_Rigi"), ("K_A_Z", "Küsnacht_am_Rigi", "Arth_Goldau")
    ]
    assert through.departure == 6 * 60 + 50 and through.legs[0].arrival == 7 * 60 + 6
    assert through.seats == 400 and through.mode == "Zug"
    assert [l.edge_id for l in trips["split@0"].legs] == ["K_A_Z"]  # the part without edge is dropped
    platforms = trips["platforms@0"]
    assert platforms.departure == 9 * 60 + 3 and [l.edge_id for l in platforms.legs] == ["L_K_Z"]
