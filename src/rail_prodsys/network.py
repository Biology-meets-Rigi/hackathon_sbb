"""
Build a prodsys production system from the rail network data.

- station   -> production resource with the processes transfer_<S> and alight_<S>, plus a
               walkway resource that brings passengers from the station to the exits (sinks).
               Sources release new passengers directly into the station queue, so everybody
               boarding at a station waits in the same queue and the vehicle takes them as one lot.
- vehicle   -> transport resource running one block (Umlauf) of the timetable; vehicles with the
               same sections share a LinkTransportProcess over the track nodes of these sections
- passengers-> products (one product = a group of passengers), one product type per
               origin/destination pair and route variant, each with its own source and sink
Vehicles do not move on demand: the runtime operator (rail_prodsys.runtime) runs them along the
construction timetable and boards the waiting passengers at every departure.

Time unit in prodsys is minutes; locations are metres in a local projection of lat/lon.
Note: avoid local names like `resources`, `process`, `product`, `source`, `sink`, `node` here.
pydantic resolves prodsys' forward references in the caller's namespace on the first instantiation.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import prodsys.express as psx
from prodsys.models.production_system_data import ProductionSystemData

from data_models import Edge, Node, PassengerFlow
from timetable.disposition import DispositionConfig
from timetable.model import Timetable

DEFAULT_GTFS_DIR = str(Path(__file__).resolve().parents[2] / "data" / "gtfs")

PASSENGER_CAPABILITY = "passenger_transport"
# Offsets of the station and walkway resources from the track node (production resources may not
# share coordinates with each other).
STATION_OFFSET_M = (30.0, 30.0)
WALKWAY_OFFSET_M = (-30.0, -30.0)


@dataclass
class RailModelConfig:
    group_size: int = 10  # passengers per simulated product
    gtfs_dir: str = DEFAULT_GTFS_DIR  # timetable (reference year or synthetic, see generate_timetable.py)
    timetable_start_date: Optional[str] = None  # YYYYMMDD of horizon start; default: first service day
    # Real feeds: CSV stop_id,node_id; the timetable is then clipped to the modelled network
    stop_mapping_path: Optional[str] = None
    # Passengers per vehicle where the feed has no vehicle_capacity (assumptions per mode)
    default_seats: Dict[str, int] = field(
        default_factory=lambda: {"Zug": 400, "Bus": 70, "Schiff": 500, "Luftseilbahn": 80, "Standseilbahn": 100, "Tram": 180}
    )
    disposition: DispositionConfig = field(default_factory=DispositionConfig)
    dwell_minutes: float = 2.0  # transfer / alighting at a station
    walk_speed_m_per_min: float = 80.0  # station -> exit
    station_capacity: int = 50  # passenger groups processed in parallel at a station
    max_route_variants: int = 3  # alternative routes per origin/destination pair
    max_detour_factor: float = 3.0  # drop variants slower than factor * fastest variant (base times)


@dataclass
class Line:
    """A timetable route (GTFS route) and the vehicles running its trips."""

    route_id: str
    mode: str
    edges: List[str]
    stations: List[str]
    vehicle_ids: List[str]
    seats: int  # passengers per vehicle (largest trip capacity)


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
    timetable: Timetable  # regular timetable of the horizon
    lines: Dict[str, Line]  # per route_id
    vehicle_groups: Dict[str, int]  # vehicle resource id -> passenger groups it can carry
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
    timetable: Timetable,
    config: Optional[RailModelConfig] = None,
) -> RailNetwork:
    config = config or RailModelConfig()
    served_edges = {e: edges[e] for e in sorted({leg.edge_id for t in timetable.trips for leg in t.legs})}
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
        for index, (stations, path_edges) in enumerate(find_route_variants(origin, destination, served_edges, config)):
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

    # Vehicles: one transport resource per block; vehicles with the same sections share a process.
    # Section run times come from the construction timetable at runtime (rail_prodsys.runtime), so
    # the distance time model only covers the short connector hops station <-> track node.
    blocks = timetable.blocks()
    connector = psx.DistanceTimeModel(speed=1e6, reaction_time=0.0, metric="euclidean", ID="t_vehicle_connector")

    def sections_of(block_trips) -> Tuple[str, ...]:
        return tuple(sorted({leg.edge_id for t in block_trips for leg in t.legs}))

    def groups_of(block_trips) -> int:
        return max(1, math.ceil(max(t.seats for t in block_trips) / config.group_size))

    vehicle_res, vehicle_groups, line_procs = [], {}, {}
    for vid, block_trips in sorted(blocks.items()):
        sections = sections_of(block_trips)
        if sections not in line_procs:
            line_stations = sorted({st for e in sections for st in (edges[e].start_node, edges[e].end_node)})
            links = [[track_nodes[edges[e].start_node], track_nodes[edges[e].end_node]] for e in sections]
            links += [[station_res[st], track_nodes[st]] for st in line_stations]
            max_groups = max(groups_of(b) for b in blocks.values() if sections_of(b) == sections)
            lot = psx.LotDependency(min_lot_size=1, max_lot_size=max_groups, ID=f"lot_line_{len(line_procs)}")
            line_procs[sections] = psx.LinkTransportProcess(
                time_model=connector,
                capability=PASSENGER_CAPABILITY,
                ID=f"line_{'+'.join(sections)}",
                dependencies=[lot],
                links=links,
            )
        groups = groups_of(block_trips)
        vehicle_res.append(
            psx.Resource(
                [line_procs[sections]], location=station_xy[block_trips[0].start_station], capacity=groups, ID=f"veh_{vid}"
            )
        )
        vehicle_groups[f"veh_{vid}"] = groups

    lines: Dict[str, Line] = {}
    for trip in timetable.trips:
        line = lines.setdefault(trip.route_id, Line(trip.route_id, trip.mode, [], [], [], 0))
        for leg in trip.legs:
            if leg.edge_id not in line.edges:
                line.edges.append(leg.edge_id)
            for st in (leg.from_station, leg.to_station):
                if st not in line.stations:
                    line.stations.append(st)
        if f"veh_{trip.block_id}" not in line.vehicle_ids:
            line.vehicle_ids.append(f"veh_{trip.block_id}")
        line.seats = max(line.seats, trip.seats)

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
    # Sources release into the queue of their station (no access transport, one boarding queue)
    station_queue = {r.ID: r.ports[0] for r in data.resource_data if r.ID.startswith("station_")}
    for source_data in data.source_data:
        origin = next(v.origin for variants in routes.values() for v in variants if v.source_id == source_data.ID)
        source_data.ports = [station_queue[f"station_{origin}"]]
    data.validate_configuration()
    return RailNetwork(
        config=config,
        production_system=data,
        timetable=timetable,
        lines=lines,
        vehicle_groups=vehicle_groups,
        routes=routes,
    )
