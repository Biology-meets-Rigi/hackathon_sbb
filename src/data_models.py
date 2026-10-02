from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional
import pandas as pd

# Each 24 h cycle is split like passenger_flows_day.csv: [0, 12) day, [12, 24) night.
# capacity_at_day / capacity_at_night are passengers per such 12 h period.
DAY_PERIOD_END_HOUR = 12.0


@dataclass
class Node:
    node_id: str
    location_x: float
    location_y: float


@dataclass
class Edge:
    edge_id: str
    start_node: str
    end_node: str
    base_travel_time_min: int
    mode: str
    capacity_at_day: int
    capacity_at_night: int


@dataclass
class Asset:
    asset_id: str
    edge_id: str
    asset_type: str
    condition_initial: float                  # score 0..1 (1 = new), converted from the condition class
    condition_transition_rate: float          # time-based degradation per day
    maintenance_cost_per_unit: float
    maintenance_duration_half_days: float
    location_x: float
    location_y: float
    usage_degradation_per_passage: float = 0.0  # NEW: degradation per traversal (tunable)


@dataclass
class PassengerFlow:
    flow_id: str
    origin: str
    destination: str
    start_time_hour: int
    end_time_hour: int
    rate_per_hour: float


@dataclass
class SimulationResult:
    avg_condition: float
    avg_travel_time: float
    served_trips: int
    generated_trips: int
    failed_trips: int
    total_cost: float
    schedule: Dict[str, float]


CONDITION_CLASS_BEST = 1   # gut / neuwertig
CONDITION_CLASS_WORST = 5  # schlecht / alarmierend


def condition_class_to_score(condition_class: float) -> float:
    """Map a condition class (1 = good ... 5 = bad) to the simulation score (1.0 ... 0.2)."""
    c = float(condition_class)
    if not CONDITION_CLASS_BEST <= c <= CONDITION_CLASS_WORST:
        raise ValueError(
            f"condition class {c} outside {CONDITION_CLASS_BEST}..{CONDITION_CLASS_WORST}"
        )
    return 1.0 - (c - CONDITION_CLASS_BEST) * 0.2


def _check_unique_ids(df: pd.DataFrame, column: str, path: str) -> None:
    """Duplicate ids would silently overwrite each other in the id-keyed dicts."""
    dupes = sorted(df.loc[df[column].duplicated(), column].astype(str).unique())
    if dupes:
        raise ValueError(f"{path}: duplicate {column} values {dupes}")


def load_nodes(path: str) -> Dict[str, Node]:
    df = pd.read_csv(path)
    _check_unique_ids(df, "node_id", path)
    nodes: Dict[str, Node] = {}
    for _, row in df.iterrows():
        nodes[str(row["node_id"])] = Node(
            node_id=str(row["node_id"]),
            location_x=float(row["location_x"]),
            location_y=float(row["location_y"]),
        )
    return nodes


def load_edges(path: str) -> Dict[str, Edge]:
    df = pd.read_csv(path)
    _check_unique_ids(df, "edge_id", path)
    edges: Dict[str, Edge] = {}
    for _, row in df.iterrows():
        bidirectional = bool(row.get("bidirectional", True))
        edge = Edge(
            edge_id=str(row["edge_id"]),
            start_node=str(row["start_node"]),
            end_node=str(row["end_node"]),
            mode=str(row["mode"]),
            base_travel_time_min=float(row["base_travel_time_min"]),
            capacity_at_day=int(row["capacity_at_day"]),
            capacity_at_night=int(row["capacity_at_night"]),
        )
        edges[edge.edge_id] = edge
    return edges


def load_assets(path: str) -> Dict[str, Asset]:
    df = pd.read_csv(path)
    _check_unique_ids(df, "asset_id", path)
    assets: Dict[str, Asset] = {}
    for _, row in df.iterrows():
        assets[str(row["asset_id"])] = Asset(
            asset_id=str(row["asset_id"]),
            edge_id=str(row["edge_id"]),
            asset_type=str(row["asset_type"]),
            condition_initial=condition_class_to_score(row["condition_initial"]),
            condition_transition_rate=float(row["condition_transition_rate"]),
            location_x=float(row["location_x"]),
            location_y=float(row["location_y"]),
            maintenance_cost_per_unit=float(row["maintenance_cost_per_unit"]),
            maintenance_duration_half_days=float(row["maintenance_duration_half_days"]),
            usage_degradation_per_passage=float(row.get("usage_degradation_per_passage", 0.0)),
        )
    return assets


def load_passenger_flows(path: str) -> List[PassengerFlow]:
    df = pd.read_csv(path)
    flows: List[PassengerFlow] = []
    for _, row in df.iterrows():
        flow = PassengerFlow(
            flow_id=str(row["flow_id"]),
            origin=str(row["origin"]),
            destination=str(row["destination"]),
            start_time_hour=int(row["start_time_hour"]),
            end_time_hour=int(row["end_time_hour"]),
            rate_per_hour=float(row["rate_per_hour"]),
        )
        flows.append(flow)
    return flows
