"""
Infrastructure asset layer (Anlagen): condition, maintenance windows, deferral and cost.

Backend-independent: both the SimPy and the prodsys simulation query this layer. All times are
hours since the start of the horizon. Conditions are scores in [min_condition, 1] (1 = new).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np

from data_models import Asset

# Maintenance durations are given in half-days (12 h shifts).
HOURS_PER_HALF_DAY = 12.0


def maintenance_duration_hours(asset: Asset) -> float:
    return float(asset.maintenance_duration_half_days) * HOURS_PER_HALF_DAY


def maintenance_fits_horizon(asset: Asset, start_hour: float, horizon_hours: float) -> bool:
    """Jobs that would not finish within the horizon are deferred to a later planning period."""
    return float(start_hour) + maintenance_duration_hours(asset) <= float(horizon_hours)


@dataclass(frozen=True)
class MaintenanceWindow:
    start_hour: float
    end_hour: float

    def contains(self, t: float) -> bool:
        return self.start_hour <= t < self.end_hour


class InfrastructureState:
    """
    Condition and maintenance state of all assets for one maintenance plan.

    - Condition degrades linearly with condition_transition_rate per day.
    - During maintenance the asset condition is at most condition_during_works.
    - After maintenance the asset is new (1.0) and degrades again from the window end.
    - An edge is as good as its worst asset; maintenance on any of its assets reduces the
      edge capacity (capacity_factor) and slows traffic down (slowdown_factor).
    """

    def __init__(
        self,
        assets: Dict[str, Asset],
        plan: Dict[str, float],
        horizon_hours: float,
        *,
        min_condition: float = 0.1,
        condition_during_works: float = 0.2,
        capacity_factor: float = 0.5,
        slowdown_factor: float = 1.5,
    ) -> None:
        self.assets = assets
        self.horizon_hours = float(horizon_hours)
        self.min_condition = float(min_condition)
        self.condition_during_works = float(condition_during_works)
        self.capacity_factor = float(capacity_factor)
        self.slowdown_factor = float(slowdown_factor)

        # Deferred jobs get no window and cost nothing in this horizon
        self.windows: Dict[str, MaintenanceWindow] = {}
        for asset_id, start in plan.items():
            asset = assets[asset_id]
            if maintenance_fits_horizon(asset, start, self.horizon_hours):
                self.windows[asset_id] = MaintenanceWindow(float(start), float(start) + maintenance_duration_hours(asset))
        self.deferred_assets: List[str] = sorted(a for a in plan if a not in self.windows)

        self.edge_assets: Dict[str, List[str]] = {}
        for asset_id, asset in assets.items():
            self.edge_assets.setdefault(asset.edge_id, []).append(asset_id)

    # --- assets -------------------------------------------------------------------------------

    def asset_condition(self, asset_id: str, t: float) -> float:
        asset = self.assets[asset_id]
        w = self.windows.get(asset_id)
        if w is not None and t >= w.end_hour:
            base, since = 1.0, w.end_hour
        else:
            base, since = asset.condition_initial, 0.0
        degraded = asset.condition_transition_rate * max(0.0, t - since) / 24.0
        condition = max(self.min_condition, min(1.0, base - degraded))
        if w is not None and w.contains(t):
            condition = max(self.min_condition, min(condition, self.condition_during_works))
        return condition

    def average_condition(self, step_hours: float = 1.0) -> float:
        """Mean condition over all assets, sampled every step_hours in [0, horizon)."""
        samples = np.arange(0.0, self.horizon_hours, step_hours)
        if len(samples) == 0 or not self.assets:
            return 1.0
        return float(np.mean([self.asset_condition(a, float(t)) for a in self.assets for t in samples]))

    def total_cost(self) -> float:
        return float(
            sum(
                self.assets[a].maintenance_cost_per_unit * self.assets[a].maintenance_duration_half_days
                for a in self.windows
            )
        )

    # --- edges --------------------------------------------------------------------------------

    def edge_condition(self, edge_id: str, t: float) -> float:
        """Worst asset condition on the edge (1.0 for edges without assets)."""
        return min((self.asset_condition(a, t) for a in self.edge_assets.get(edge_id, [])), default=1.0)

    def edge_under_maintenance(self, edge_id: str, t: float) -> bool:
        return any(
            self.windows[a].contains(t) for a in self.edge_assets.get(edge_id, []) if a in self.windows
        )

    def edge_slowdown(self, edge_id: str, t: float) -> float:
        """Travel time factor: base * (1 + (1 - condition)), times slowdown_factor during works."""
        factor = 1.0 + (1.0 - self.edge_condition(edge_id, t))
        if self.edge_under_maintenance(edge_id, t):
            factor *= self.slowdown_factor
        return factor

    def edge_slowdown_vs_timetable(self, edge_id: str, t: float) -> float:
        """
        Run time factor relative to the timetable: a timetable already contains the run times for the
        infrastructure state at the start of the horizon, so only further degradation and works slow
        trains down (never below 1: renewed track does not make trains faster than scheduled).
        """
        reference = 1.0 + (1.0 - min((self.assets[a].condition_initial for a in self.edge_assets.get(edge_id, [])), default=1.0))
        return max(1.0, self.edge_slowdown(edge_id, t) / reference)

    def edge_capacity_factor(self, edge_id: str, t: float) -> float:
        return self.capacity_factor if self.edge_under_maintenance(edge_id, t) else 1.0

    def edge_track_capacity(self, edge_id: str, track_capacity: Optional[int], t: float) -> Optional[int]:
        """
        Vehicles allowed on the section at time t: works block tracks, floor(capacity * factor).
        A single-track section is closed during works. None = unlimited (no track capacity given).
        """
        if track_capacity is None:
            return None
        if self.edge_under_maintenance(edge_id, t):
            return int(math.floor(track_capacity * self.capacity_factor + 1e-9))
        return int(track_capacity)

    def edge_change_points(self, edge_id: str) -> List[float]:
        """Sorted maintenance start/end times on the edge (where capacity and slowdown jump)."""
        points = set()
        for a in self.edge_assets.get(edge_id, []):
            w = self.windows.get(a)
            if w is not None:
                points.update((w.start_hour, w.end_hour))
        return sorted(p for p in points if 0.0 <= p <= self.horizon_hours)
