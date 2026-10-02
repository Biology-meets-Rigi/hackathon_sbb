from __future__ import annotations

import heapq
from dataclasses import dataclass
from typing import Dict, Generator, List, Optional, Set, Tuple

import numpy as np
import simpy

from data_models import Asset, Edge, Node, PassengerFlow, SimulationResult

# Simulation time unit is hours; maintenance durations are given in half-days (12 h shifts).
HOURS_PER_HALF_DAY = 12.0


def maintenance_duration_hours(asset: Asset) -> float:
    return float(asset.maintenance_duration_half_days) * HOURS_PER_HALF_DAY


@dataclass
class MaintenanceWindow:
    start_hour: float
    end_hour: float


@dataclass
class NetworkGraph:
    nodes: Dict[str, Node]
    edges: Dict[str, Edge]
    adjacency: Dict[str, List[Tuple[str, str]]]  # node_id -> [(neighbor_id, edge_id)]

    @staticmethod
    def from_data(nodes: Dict[str, Node], edges: Dict[str, Edge]) -> "NetworkGraph":
        adjacency: Dict[str, List[Tuple[str, str]]] = {node_id: [] for node_id in nodes}
        for edge_id, edge in edges.items():
            adjacency[edge.start_node].append((edge.end_node, edge_id))
            # always bidirectional
            adjacency[edge.end_node].append((edge.start_node, edge_id))
        return NetworkGraph(nodes=nodes, edges=edges, adjacency=adjacency)

    def find_path(
        self,
        origin: str,
        destination: str,
        blocked_edges: Set[str],
        edge_weight: Dict[str, float],
    ) -> Optional[List[str]]:
        """Dijkstra shortest path: weight per edge_id is provided externally (can be time/condition dependent)."""
        if origin not in self.nodes or destination not in self.nodes:
            return None
        if origin == destination:
            return []

        distances: Dict[str, float] = {node_id: float("inf") for node_id in self.nodes}
        distances[origin] = 0.0
        previous: Dict[str, Optional[Tuple[str, str]]] = {node_id: None for node_id in self.nodes}
        pq: List[Tuple[float, str]] = [(0.0, origin)]
        visited: Set[str] = set()

        while pq:
            current_dist, current_node = heapq.heappop(pq)
            if current_node in visited:
                continue
            visited.add(current_node)
            if current_node == destination:
                break

            for neighbor, edge_id in self.adjacency[current_node]:
                if edge_id in blocked_edges:
                    continue
                w = edge_weight.get(edge_id, self.edges[edge_id].base_travel_time_min / 60.0)
                new_dist = current_dist + w
                if new_dist < distances[neighbor]:
                    distances[neighbor] = new_dist
                    previous[neighbor] = (current_node, edge_id)
                    heapq.heappush(pq, (new_dist, neighbor))

        if distances[destination] == float("inf"):
            return None

        path_edges: List[str] = []
        current = destination
        while previous[current] is not None:
            prev_node, edge_id = previous[current]
            path_edges.append(edge_id)
            current = prev_node
        path_edges.reverse()
        return path_edges


class NetworkSimulator:
    """
    Simpy-based network simulation:
    - Edge capacities are modeled using simpy.Resource.
    - Maintenance reduces capacity and increases travel time (slowdown).
    - Asset condition degrades with time and usage (edge traversals).
    """

    def __init__(
        self,
        nodes: Dict[str, Node],
        edges: Dict[str, Edge],
        assets: Dict[str, Asset],
        flows: List[PassengerFlow],
        maintenance_plan: Dict[str, float],
        horizon_hours: float,
        rng: np.random.Generator,
        maintenance_capacity_factor: float = 0.5,  # NEW: capacity reduction during maintenance
        maintenance_slowdown_factor: float = 1.5,  # NEW: extra slowdown during maintenance
        min_condition: float = 0.1,
    ) -> None:
        self.nodes = nodes
        self.edges = edges
        self.assets = assets
        self.flows = flows
        self.horizon_hours = horizon_hours
        self.rng = rng
        self.env = simpy.Environment()
        self.graph = NetworkGraph.from_data(nodes, edges)

        # Maintenance windows
        self.maintenance_windows: Dict[str, MaintenanceWindow] = {}
        for asset_id, start in maintenance_plan.items():
            asset = assets[asset_id]
            self.maintenance_windows[asset_id] = MaintenanceWindow(
                start_hour=float(start),
                end_hour=float(start) + maintenance_duration_hours(asset),
            )

        # Map assets to edges
        self.edge_assets: Dict[str, List[str]] = {edge_id: [] for edge_id in edges}
        for asset_id, asset in assets.items():
            self.edge_assets[asset.edge_id].append(asset_id)

        # Edge capacity resources
        self.edge_resources: Dict[str, simpy.Resource] = {}
        for edge_id, edge in edges.items():
            cap = max(int(edge.capacity_at_day), 1) # TODO NAcht
            self.edge_resources[edge_id] = simpy.Resource(self.env, capacity=cap)

        # Condition state
        self.asset_last_maintenance: Dict[str, float] = {k: 0.0 for k in assets}
        self.asset_condition_after_maintenance: Dict[str, float] = {
            k: v.condition_initial for k, v in assets.items()
        }
        self.min_condition = float(min_condition)

        # Parameters
        self.maintenance_capacity_factor = float(maintenance_capacity_factor)
        self.maintenance_slowdown_factor = float(maintenance_slowdown_factor)

        # Metrics
        self.total_travel_time = 0.0
        self.served_trips = 0
        self.failed_trips = 0
        self.generated_trips = 0
        self.condition_samples = 0
        self.condition_sum = 0.0
        self.total_cost = self._compute_cost(maintenance_plan)

    def _compute_cost(self, plan: Dict[str, float]) -> float:
        cost = 0.0
        for asset_id in plan:
            asset = self.assets[asset_id]
            cost += asset.maintenance_cost_per_unit * asset.maintenance_duration_half_days
        return cost

    def _is_under_maintenance(self, asset_id: str, t: float) -> bool:
        w = self.maintenance_windows.get(asset_id)
        return (w is not None) and (w.start_hour <= t < w.end_hour)

    def _get_blocked_edges(self, current_time: float) -> Set[str]:
        """
        Optional: edges can be fully blocked only if you want hard closures.
        Right now we do NOT hard-block by default (maintenance reduces capacity & slows down).
        Keeping function for extensibility (e.g., asset_type == 'bridge' => full closure).
        """
        blocked: Set[str] = set()
        # Example hard-block rule (disabled):
        # for asset_id, w in self.maintenance_windows.items():
        #     if w.start_hour <= current_time < w.end_hour:
        #         if self.assets[asset_id].asset_type.lower() in {"bridge_replacement"}:
        #             blocked.add(self.assets[asset_id].edge_id)
        return blocked

    def _asset_condition(self, asset_id: str, current_time: float) -> float:
        asset = self.assets[asset_id]
        last_maintenance = self.asset_last_maintenance[asset_id]
        elapsed_days = max(0.0, (current_time - last_maintenance) / 24.0)
        base_condition = self.asset_condition_after_maintenance[asset_id]
        degraded = asset.condition_transition_rate * elapsed_days
        condition = max(self.min_condition, min(1.0, base_condition - degraded))

        # Apply maintenance effects: during maintenance, condition is low; after, it's restored.
        w = self.maintenance_windows.get(asset_id)
        if w is not None:
            if w.start_hour <= current_time < w.end_hour:
                condition = max(self.min_condition, min(condition, 0.2))
            elif current_time >= w.end_hour and last_maintenance < w.end_hour:
                self.asset_last_maintenance[asset_id] = w.end_hour
                self.asset_condition_after_maintenance[asset_id] = 1.0
                condition = 1.0
        return condition

    def _edge_condition(self, edge_id: str, t: float) -> float:
        # Worst-asset condition on the edge
        c = 1.0
        for asset_id in self.edge_assets[edge_id]:
            c = min(c, self._asset_condition(asset_id, t))
        return c

    def _edge_weight_for_routing(self, t: float) -> Dict[str, float]:
        """
        Provide time-dependent edge weights to the router.
        Approximate: base time * (1 + (1 - condition)) and add maintenance slowdown if any asset under maintenance.
        """
        weights: Dict[str, float] = {}
        for edge_id, edge in self.edges.items():
            edge_cond = self._edge_condition(edge_id, t)
            slowdown = 1.0 + (1.0 - edge_cond)

            # If any asset on edge under maintenance, extra slowdown
            if any(self._is_under_maintenance(a, t) for a in self.edge_assets[edge_id]):
                slowdown *= self.maintenance_slowdown_factor

            weights[edge_id] = (edge.base_travel_time_min / 60.0) * slowdown
        return weights

    def _sample_conditions(self) -> Generator[simpy.events.Timeout, None, None]:
        while self.env.now <= self.horizon_hours:
            for asset_id in self.assets:
                condition = self._asset_condition(asset_id, self.env.now)
                self.condition_sum += condition
                self.condition_samples += 1
            yield self.env.timeout(1.0)

    def _degrade_assets_by_usage(self, edge_id: str) -> None:
        """
        Usage-based degradation: called once per traversal.
        """
        for asset_id in self.edge_assets[edge_id]:
            asset = self.assets[asset_id]
            k = float(asset.usage_degradation_per_passage)
            if k <= 0:
                continue
            # reduce "after maintenance base" a bit; clamp
            self.asset_condition_after_maintenance[asset_id] = max(
                self.min_condition,
                self.asset_condition_after_maintenance[asset_id] - k,
            )

    def _traverse_edge(self, edge_id: str) -> Generator[simpy.events.Timeout, None, None]:
        """
        Traverse an edge under capacity constraints.
        We model capacity as concurrent slots; maintenance reduces effective capacity by holding slots.
        """
        edge = self.edges[edge_id]
        resource = self.edge_resources[edge_id]

        # Maintenance capacity reduction by occupying some slots.
        # For simplicity: if any asset under maintenance -> reduce effective capacity by reserving slots.
        maintenance_active = any(self._is_under_maintenance(a, self.env.now) for a in self.edge_assets[edge_id])
        reserved = 0
        if maintenance_active:
            cap = max(resource.capacity, 1)
            reserved = max(0, int(round(cap * (1.0 - self.maintenance_capacity_factor))))
        reserved_reqs = []
        for _ in range(reserved):
            req = resource.request()
            reserved_reqs.append(req)
            yield req  # occupy slots

        # Now request one slot for traversal
        req = resource.request()
        yield req

        # Compute travel time with condition + maintenance slowdown
        edge_cond = self._edge_condition(edge_id, self.env.now)
        slowdown = 1.0 + (1.0 - edge_cond)
        if maintenance_active:
            slowdown *= self.maintenance_slowdown_factor
        travel_time = (edge.base_travel_time_min / 60.0) * slowdown

        # Usage degradation
        self._degrade_assets_by_usage(edge_id)

        yield self.env.timeout(travel_time)

        # release traversal slot and reserved slots
        resource.release(req)
        for r in reserved_reqs:
            resource.release(r)

    def _passenger_trip(self, flow: PassengerFlow, start_time: float) -> Generator[simpy.events.Timeout, None, None]:
        self.generated_trips += 1
        travel_start = float(start_time)
        yield self.env.timeout(max(0.0, travel_start - self.env.now))

        # Pathfinding with time-dependent weights, optional hard-blocking
        t = self.env.now
        blocked_edges = self._get_blocked_edges(t)
        weights = self._edge_weight_for_routing(t)
        edge_path = self.graph.find_path(flow.origin, flow.destination, blocked_edges, weights)

        if edge_path is None:
            self.failed_trips += 1
            return

        for edge_id in edge_path:
            # edge = self.edges[edge_id]
            # 
            # # Check if edge is blocked during travel
            # blocked_assets = self._get_blocked_assets(current_time)
            # edge_blocked_assets = [a for a in self.edge_assets[edge_id] if a in blocked_assets]
            # 
            # if edge_blocked_assets:
            #     # Wait for all maintenance on this edge to finish
            #     earliest_end = min(
            #         self.maintenance_windows[a].end_hour 
            #         for a in edge_blocked_assets
            #     )
            #     yield self.env.timeout(max(0.0, earliest_end - current_time))
            #     current_time = self.env.now
            #     # Update maintenance flags
            #     for asset_id in edge_blocked_assets:
            #         self.asset_last_maintenance[asset_id] = current_time
            #         self.asset_condition_after_maintenance[asset_id] = 1.0
# 
            # # Calculate edge condition (worst asset condition on edge)
            # edge_condition = 1.0
            # for asset_id in self.edge_assets[edge_id]:
            #     asset_condition = self._asset_condition(asset_id, current_time)
            #     edge_condition = min(edge_condition, asset_condition)
            # 
            # # Travel time increases with degraded condition
            # slowdown = 1.0 + (1.0 - edge_condition)
            # travel_time = (edge.capacity_at_day / 60.0) * slowdown # TODO Night also
            # yield self.env.timeout(travel_time)
            # current_time = self.env.now
            # If after waiting/traversals time changed a lot, you can reroute per hop (optional).
            yield self.env.process(self._traverse_edge(edge_id))

        total_time = self.env.now - travel_start
        self.total_travel_time += total_time
        self.served_trips += 1

    def _passenger_source(self, flow: PassengerFlow) -> Generator[simpy.events.Timeout, None, None]:
        # Flow windows are given in hours of a single day (0-24) and repeat every day of the horizon.
        lam = max(float(flow.rate_per_hour), 1e-6)
        day_start = 0.0
        while day_start < self.horizon_hours:
            current_time = day_start + float(flow.start_time_hour)
            window_end = min(day_start + float(flow.end_time_hour), self.horizon_hours)
            while current_time < window_end:
                yield self.env.timeout(max(0.0, current_time - self.env.now))
                self.env.process(self._passenger_trip(flow, current_time))
                current_time += float(self.rng.exponential(1.0 / lam))
            day_start += 24.0

    def run(self) -> SimulationResult:
        for flow in self.flows:
            self.env.process(self._passenger_source(flow))
        self.env.process(self._sample_conditions())
        self.env.run(until=self.horizon_hours)

        avg_condition = self.condition_sum / max(self.condition_samples, 1)
        avg_travel_time = self.total_travel_time / max(self.served_trips, 1)

        schedule_out = {aid: w.start_hour for aid, w in self.maintenance_windows.items()}

        return SimulationResult(
            avg_condition=float(avg_condition),
            avg_travel_time=float(avg_travel_time),
            served_trips=int(self.served_trips),
            generated_trips=int(self.generated_trips),
            failed_trips=int(self.failed_trips),
            total_cost=float(self.total_cost),
            schedule=schedule_out,
        )


def simulate_schedule(
    nodes: Dict[str, Node],
    edges: Dict[str, Edge],
    assets: Dict[str, Asset],
    flows: List[PassengerFlow],
    schedule: Dict[str, float],
    rng: np.random.Generator,
    horizon_hours: float = 24.0,
) -> SimulationResult:
    simulator = NetworkSimulator(nodes, edges, assets, flows, schedule, horizon_hours, rng)
    return simulator.run()
