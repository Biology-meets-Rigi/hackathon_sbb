from __future__ import annotations

import argparse
import json
from pathlib import Path
import pandas as pd

import numpy as np

from data_models import load_assets, load_edges, load_nodes, load_passenger_flows
from ga import BACKENDS, GAConfig, NSGA2Optimizer
from assets import maintenance_fits_horizon

BACKEND_NOTES = {
    "simpy": [
        "Simulation backend: own SimPy model; edge capacity as simpy slots, passengers routed by expected time.",
        "Maintenance reduces edge capacity & increases travel time.",
    ],
    "prodsys": [
        "Simulation backend: prodsys (stations = production resources, lines = link transport processes "
        "with vehicles, passengers = products in groups).",
        "Maintenance withdraws vehicles of the line (capacity) and slows it down; passengers choose the "
        "fastest route variant at departure.",
    ],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NSGA-II maintenance scheduler (multi-objective)")
    parser.add_argument("--nodes", default="data/nodes_template.csv", help="Path to nodes csv")
    parser.add_argument("--edges", default="data/edges_template.csv", help="Path to edges csv")
    parser.add_argument("--assets", default="data/assets_template.csv", help="Path to assets csv")
    parser.add_argument("--flowsday", default="data/passenger_flows_day.csv", help="Path to passenger flows at day csv")
    parser.add_argument(
        "--weights",
        type=float,
        nargs=3,
        default=(0.3, 0.4, 0.3),
        help="Weights for condition, travel time, cost (sum=1) used to pick the compromise from the Pareto front",
    )
    parser.add_argument("--horizon", type=float, default=168.0, help="Simulation horizon in hours")
    parser.add_argument("--population", type=int, default=4, help="NSGA-II population size")
    parser.add_argument("--generations", type=int, default=1, help="NSGA-II generations")
    parser.add_argument("--seed", type=int, default=45, help="Random seed")
    parser.add_argument("--cx", type=float, default=0.3, help="Crossover")
    parser.add_argument("--mut", type=float, default=0.1, help="Mutation")
    parser.add_argument("--workers", type=int, default=0, help="Parallel simulations (0 = all CPU cores, 1 = sequential)")
    parser.add_argument("--backend", choices=BACKENDS, default="prodsys", help="Simulation backend")
    parser.add_argument("--group-size", type=int, default=10, help="prodsys backend: passengers per simulated product")
    parser.add_argument("--output", default="output/best_schedule.csv", help="Output csv for best schedule")
    parser.add_argument("--pareto_out", default="output/pareto_front.csv", help="Output csv for Pareto front")
    return parser.parse_args()


def deferred_assets(schedule: dict, assets: dict, horizon_hours: float) -> list:
    """Assets whose maintenance does not finish within the horizon (not executed in this plan)."""
    return sorted(aid for aid, sh in schedule.items() if not maintenance_fits_horizon(assets[aid], sh, horizon_hours))


def build_summary(gen: int, schedule: dict, metrics: tuple, assets: dict, horizon_hours: float, backend: str) -> dict:
    return {
        "generation": int(gen),
        "backend": backend,
        "horizon_hours": float(horizon_hours),
        "best_schedule": schedule,
        "deferred_assets": deferred_assets(schedule, assets, horizon_hours),
        "objectives": {
            "served_adjusted": metrics[0],
            "avg_condition": metrics[1],
            "avg_travel_time": metrics[2],
            "total_cost": metrics[3],
        },
        "notes": [
            "Objectives are true multi-objective (NSGA-II): maximize served, condition; minimize travel time, cost.",
            *BACKEND_NOTES[backend],
            "Asset condition degrades with time; maintenance renews it (asset layer, src/assets.py).",
            "start_hour is in hours since horizon start; jobs not finishing within the horizon are deferred.",
        ],
    }


def save_schedule(path: str, schedule: dict, deferred: list) -> None:
    df = pd.DataFrame(
        [{"asset_id": aid, "start_hour": sh, "executed": aid not in deferred} for aid, sh in schedule.items()]
    ).sort_values("asset_id")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def save_pareto(path: str, pareto) -> None:
    rows = []
    for ind in pareto:
        f = ind.fitness.values
        rows.append(
            {
                "served_adjusted": f[0],
                "avg_condition": f[1],
                "avg_travel_time": f[2],
                "total_cost": f[3],
                "genes": json.dumps(list(map(float, ind))),
            }
        )
    df = pd.DataFrame(rows)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def main() -> None:
    args = parse_args()

    nodes = load_nodes(args.nodes)
    edges = load_edges(args.edges)
    assets = load_assets(args.assets)
    flows = load_passenger_flows(args.flowsday)

    config = GAConfig(
        population_size=args.population,
        generations=args.generations,
        crossover_prob=args.cx,
        mutation_prob=args.mut,
        horizon_hours=args.horizon,
        seed=args.seed,
        selection_weights=tuple(args.weights),
        workers=args.workers,
        backend=args.backend,
        group_size=args.group_size,
    )

    # Write summary.json after each generation
    summary_path = Path(args.output).parent / "summary.json"

    def _write_summary(gen: int, schedule: dict, metrics: tuple, _pareto) -> None:
        summary = build_summary(gen, schedule, metrics, assets, config.horizon_hours, config.backend)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)

    optimizer = NSGA2Optimizer(nodes, edges, assets, flows, config)
    # Initialize progress file so frontends know a run has started (generation 0)
    try:
        progress_path = Path(args.output).parent / "progress.json"
        progress_path.parent.mkdir(parents=True, exist_ok=True)
        with open(progress_path, "w", encoding="utf-8") as pf:
            json.dump({"current_generation": 0, "total_generations": int(args.generations)}, pf)
        print(f"[DEBUG] wrote initial progress to {progress_path}", flush=True)
    except Exception as e:
        print(f"[DEBUG] failed to write initial progress.json: {e}", flush=True)
    best_schedule, best_metrics, pareto = optimizer.run(progress_cb=_write_summary)

    final_summary = build_summary(
        config.generations, best_schedule, best_metrics, assets, config.horizon_hours, config.backend
    )
    if config.backend == "prodsys":
        # Re-run the chosen schedule (same seed as in the GA) for detailed prodsys KPIs
        from rail_prodsys.backend import run_prodsys
        from rail_prodsys.details import collect_details
        from rail_prodsys.network import RailModelConfig

        run = run_prodsys(
            nodes, edges, assets, flows, best_schedule, np.random.default_rng(config.seed),
            config.horizon_hours, RailModelConfig(group_size=config.group_size),
        )
        final_summary["details"] = collect_details(run)
    save_schedule(args.output, best_schedule, final_summary["deferred_assets"])
    save_pareto(args.pareto_out, pareto)

    # Also print + persist final summary
    print(json.dumps(final_summary, indent=2))
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(final_summary, f, indent=2)


if __name__ == "__main__":
    main()
