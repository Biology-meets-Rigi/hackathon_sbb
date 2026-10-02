"""
Build a prodsys production system from the rail network data.

- station   -> production resource with the processes transfer_<S> and alight_<S>,
               plus a walkway resource that brings passengers from the station to the exit (sink)
- edge      -> line: a LinkTransportProcess over two track nodes, served by its own vehicles
- passengers-> products (one product = a group of passengers), one product type per
               origin/destination pair and route variant, each with its own source and sink

Time unit in prodsys is minutes; locations are metres in a local projection of lat/lon.
Note: avoid local names like `resources`, `process`, `product`, `source`, `sink`, `node` here.
pydantic resolves prodsys' forward references in the caller's namespace on the first instantiation.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import prodsys.express as psx
from prodsys.models.production_system_data import ProductionSystemData

from assets import HOURS_PER_HALF_DAY
from data_models import Edge, Node, PassengerFlow

PASSENGER_CAPABILITY = "passenger_transport"
# Offsets of the station and walkway resources from the track node (production resources may not
# share coordinates with each other).
STATION_OFFSET_M = (30.0, 30.0)
WALKWAY_OFFSET_M = (-30.0, -30.0)


@dataclass
class RailModelConfig:
    group_size: int = 10  # passengers per simulated product
    vehicles_per_line: int = 4
    dwell_minutes: float = 2.0  # transfer / alighting at a station
    walk_speed_m_per_min: float = 80.0  # station -> exit
    station_capacity: int = 50  # passenger groups processed in parallel at a station
    max_route_variants: int = 3  # alternative routes per origin/destination pair
    max_detour_factor: float = 3.0  # drop variants slower than factor * fastest variant (base times)


@dataclass
class Line:
    edge_id: str
    mode: str
    stations: Tuple[str, str]
    base_minutes: float
    vehicle_ids: List[str]
    seats: int  # passenger groups per vehicle
    vehicles_day: int
    vehicles_night: int

    @property
    def round_trip_minutes(self) -> float:
        return 2.0 * self.base_minutes


@dataclass
class RouteVariant:
    origin: str
    destination: str
    index: int
    stations: List[str]
    edges: List[str]
    product_id: str
    source_id: str
    sink_id: str

    @property
    def transfer_stations(self) -> List[str]:
        return self.stations[1:-1]


@dataclass
class RailNetwork:
    config: RailModelConfig
    production_system: ProductionSystemData
    lines: Dict[str, Line]
    routes: Dict[Tuple[str, str], List[RouteVariant]]


def _projection(nodes: Dict[str, Node]):
    """Equirectangular projection around the network centre, in metres (location_x = lat, location_y = lon)."""
    lat0 = sum(n.location_x for n in nodes.values()) / len(nodes)
    lon0 = sum(n.location_y for n in nodes.values()) / len(nodes)

    def to_xy(lat: float, lon: float) -> List[float]:
        return [(lon - lon0) * 111_320.0 * math.cos(math.radians(lat0)), (lat - lat0) * 110_540.0]

    return to_xy


def _offset(xy: List[float], offset: Tuple[float, float]) -> List[float]:
    return [xy[0] + offset[0], xy[1] + offset[1]]


def _size_line(edge: Edge, config: RailModelConfig) -> Tuple[int, int, int]:
    """Seats (groups) per vehicle so that the line carries capacity_at_day per 12 h; vehicles at night."""
    per_hour = edge.capacity_at_day / HOURS_PER_HALF_DAY
    round_trip_hours = 2.0 * edge.base_travel_time_min / 60.0
    seats_pax = per_hour * round_trip_hours / config.vehicles_per_line
    seats = max(1, math.ceil(seats_pax / config.group_size))
    night_share = edge.capacity_at_night / edge.capacity_at_day if edge.capacity_at_day > 0 else 1.0
    vehicles_night = max(1, round(config.vehicles_per_line * night_share))
    return seats, config.vehicles_per_line, vehicles_night


def find_route_variants(
    origin: str, destination: str, edges: Dict[str, Edge], config: RailModelConfig
) -> List[Tuple[List[str], List[str]]]:
    """Simple paths (stations, edges) from origin to destination, fastest first, pruned by detour factor."""
    adjacency: Dict[str, List[Tuple[str, str]]] = {}
    for edge in edges.values():
        adjacency.setdefault(edge.start_node, []).append((edge.end_node, edge.edge_id))
        adjacency.setdefault(edge.end_node, []).append((edge.start_node, edge.edge_id))

    paths: List[Tuple[float, List[str], List[str]]] = []

    def dfs(station: str, stations: List[str], path_edges: List[str], minutes: float) -> None:
        if station == destination:
            paths.append((minutes, list(stations), list(path_edges)))
            return
        for neighbour, edge_id in adjacency.get(station, []):
            if neighbour in stations:
                continue
            transfer = config.dwell_minutes if path_edges else 0.0
            stations.append(neighbour)
            path_edges.append(edge_id)
            dfs(neighbour, stations, path_edges, minutes + transfer + edges[edge_id].base_travel_time_min)
            stations.pop()
            path_edges.pop()

    dfs(origin, [origin], [], 0.0)
    if not paths:
        return []
    paths.sort(key=lambda p: p[0])
    fastest = paths[0][0]
    kept = [p for p in paths if p[0] <= fastest * config.max_detour_factor][: config.max_route_variants]
    return [(stations, path_edges) for _, stations, path_edges in kept]


def build_network(
    nodes: Dict[str, Node],
    edges: Dict[str, Edge],
    flows: List[PassengerFlow],
    config: Optional[RailModelConfig] = None,
) -> RailNetwork:
    config = config or RailModelConfig()
    to_xy = _projection(nodes)
    station_xy = {sid: to_xy(n.location_x, n.location_y) for sid, n in nodes.items()}

    # Stations: track node + station resource (transfer / alight)
    track_nodes = {sid: psx.Node(location=xy, ID=f"node_{sid}") for sid, xy in station_xy.items()}
    dwell = psx.FunctionTimeModel("constant", config.dwell_minutes, ID="t_dwell")
    station_procs = {}
    station_res = {}
    for sid, xy in station_xy.items():
        station_procs[f"transfer_{sid}"] = psx.ProductionProcess(dwell, ID=f"transfer_{sid}")
        station_procs[f"alight_{sid}"] = psx.ProductionProcess(dwell, ID=f"alight_{sid}")
        station_res[sid] = psx.Resource(
            [station_procs[f"transfer_{sid}"], station_procs[f"alight_{sid}"]],
            location=_offset(xy, STATION_OFFSET_M),
            capacity=config.station_capacity,
            ID=f"station_{sid}",
        )

    # Passenger products: one product type per origin/destination pair and route variant
    ride = psx.RequiredCapabilityProcess(capability=PASSENGER_CAPABILITY, ID="ride")
    # Arrivals are released by the runtime dispatcher; the native source loop stops immediately.
    no_native_arrivals = psx.ScheduledTimeModel(schedule=[0.0], absolute=False, cyclic=False, ID="no_native_arrivals")
    routes: Dict[Tuple[str, str], List[RouteVariant]] = {}
    pax_sources: Dict[str, psx.Source] = {}
    pax_sinks: Dict[str, psx.Sink] = {}
    for od in sorted({(f.origin, f.destination) for f in flows}):
        origin, destination = od
        variants = []
        for index, (stations, path_edges) in enumerate(find_route_variants(origin, destination, edges, config)):
            suffix = f"{origin}_{destination}_r{index}"
            steps = [station_procs[f"transfer_{s}"] for s in stations[1:-1]] + [station_procs[f"alight_{destination}"]]
            pax = psx.Product(process=steps, transport_process=ride, ID=f"pax_{suffix}")
            pax_sources[f"src_{suffix}"] = psx.Source(
                pax, no_native_arrivals, location=station_xy[origin], ID=f"src_{suffix}"
            )
            pax_sinks[f"snk_{suffix}"] = psx.Sink(pax, location=station_xy[destination], ID=f"snk_{suffix}")
            variants.append(
                RouteVariant(origin, destination, index, stations, path_edges, f"pax_{suffix}", f"src_{suffix}", f"snk_{suffix}")
            )
        if not variants:
            raise ValueError(f"No route from {origin} to {destination}")
        routes[od] = variants

    # Lines: one per edge, with its own vehicles
    lines: Dict[str, Line] = {}
    vehicle_res = []
    for edge_id, edge in edges.items():
        a, b = edge.start_node, edge.end_node
        seats, vehicles_day, vehicles_night = _size_line(edge, config)
        distance = math.dist(station_xy[a], station_xy[b])
        travel = psx.DistanceTimeModel(
            speed=distance / edge.base_travel_time_min, reaction_time=0.0, metric="euclidean", ID=f"t_line_{edge_id}"
        )
        links = [[track_nodes[a], track_nodes[b]], [station_res[a], track_nodes[a]], [station_res[b], track_nodes[b]]]
        for src_obj in pax_sources.values():
            for s in (a, b):
                if src_obj.location == station_xy[s]:
                    links.append([src_obj, track_nodes[s]])
        lot = psx.LotDependency(min_lot_size=1, max_lot_size=seats, ID=f"lot_line_{edge_id}")
        line_proc = psx.LinkTransportProcess(
            time_model=travel, capability=PASSENGER_CAPABILITY, ID=f"line_{edge_id}", dependencies=[lot], links=links
        )
        vehicle_ids = []
        for i in range(vehicles_day):
            home = a if i % 2 == 0 else b
            vid = f"veh_{edge_id}_{i}"
            vehicle_res.append(psx.Resource([line_proc], location=station_xy[home], capacity=seats, ID=vid))
            vehicle_ids.append(vid)
        lines[edge_id] = Line(
            edge_id, edge.mode, (a, b), float(edge.base_travel_time_min), vehicle_ids, seats, vehicles_day, vehicles_night
        )

    # Walkways: station -> exits (sinks) of all routes ending there
    walk = psx.DistanceTimeModel(speed=config.walk_speed_m_per_min, reaction_time=0.0, metric="euclidean", ID="t_walk")
    walkway_res = []
    for sid, xy in station_xy.items():
        exits = [pax_sinks[v.sink_id] for variants in routes.values() for v in variants if v.destination == sid]
        if not exits:
            continue
        walk_lot = psx.LotDependency(min_lot_size=1, max_lot_size=config.station_capacity, ID=f"lot_walk_{sid}")
        walk_proc = psx.LinkTransportProcess(
            time_model=walk,
            capability=PASSENGER_CAPABILITY,
            ID=f"walk_{sid}",
            dependencies=[walk_lot],
            links=[[station_res[sid], exit_] for exit_ in exits],
        )
        walkway_res.append(
            psx.Resource([walk_proc], location=_offset(xy, WALKWAY_OFFSET_M), capacity=config.station_capacity, ID=f"walkway_{sid}")
        )

    system = psx.ProductionSystem(
        list(station_res.values()) + vehicle_res + walkway_res,
        list(pax_sources.values()),
        list(pax_sinks.values()),
    )
    data = system.to_model()
    data.validate_configuration()
    return RailNetwork(config=config, production_system=data, lines=lines, routes=routes)
