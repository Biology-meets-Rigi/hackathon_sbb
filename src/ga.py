from __future__ import annotations

import os
import random
from dataclasses import dataclass
from multiprocessing import Pool
from typing import Dict, List, Tuple, Callable, Optional

import numpy as np
from deap import base, creator, tools

from data_models import Asset, Edge, Node, PassengerFlow, SimulationResult
from simulation import simulate_schedule

import logging 
import time
from pathlib import Path
import json


@dataclass
class GAConfig:
    population_size: int = 20
    generations: int = 15
    crossover_prob: float = 0.3
    mutation_prob: float = 0.1
    horizon_hours: float = 60 * 24.0
    seed: int = 42
    # Weights (condition, travel time, cost) for picking the compromise solution from the Pareto front
    selection_weights: Tuple[float, float, float] = (0.3, 0.4, 0.3)
    # Parallel simulations: 0 = all CPU cores, 1 = sequential in this process
    workers: int = 0


# Simulation inputs per process (set by _init_worker in the main process and in every pool worker)
_WORKER: Dict[str, object] = {}


def _init_worker(nodes, edges, assets, flows, horizon_hours: float, seed: int) -> None:
    _WORKER.update(nodes=nodes, edges=edges, assets=assets, flows=flows, horizon_hours=horizon_hours, seed=seed)


def _simulate(schedule: Dict[str, float]) -> Tuple[SimulationResult, float]:
    t0 = time.perf_counter()
    # Common Random Numbers: every schedule sees the same passenger demand realization,
    # so objective differences come from the schedule and not from sampling noise.
    rng = np.random.default_rng(_WORKER["seed"])
    result = simulate_schedule(
        _WORKER["nodes"],
        _WORKER["edges"],
        _WORKER["assets"],
        _WORKER["flows"],
        schedule,
        rng,
        _WORKER["horizon_hours"],
    )
    return result, time.perf_counter() - t0


class NSGA2Optimizer:
    """
    True multi-objective NSGA-II:
      Maximize: served_trips, avg_condition
      Minimize: avg_travel_time, total_cost
    """

    def __init__(
        self,
        nodes: Dict[str, Node],
        edges: Dict[str, Edge],
        assets: Dict[str, Asset],
        flows_day: List[PassengerFlow],
        config: GAConfig,
    ) -> None:
        self.nodes = nodes
        self.edges = edges
        self.assets = assets
        self.flows_day = flows_day
        self.config = config
        self.asset_ids = list(assets.keys())
        self.logger = logging.getLogger("ga")
        if config.population_size < 4 or config.population_size % 4 != 0:
            raise ValueError("population_size must be a positive multiple of 4 (NSGA-II tournament selection)")

        # Reproducibility
        random.seed(config.seed)
        self.base_seed = int(config.seed)
        self.eval_counter = 0

        self._worker_args = (nodes, edges, assets, flows_day, config.horizon_hours, self.base_seed)
        _init_worker(*self._worker_args)
        self._pool: Optional[Pool] = None

        self.toolbox = base.Toolbox()
        self._setup_deap()

        self._progress_cb: Optional[Callable[[int, Dict[str, float], tuple, tools.ParetoFront], None]] = None

    def _setup_deap(self) -> None:
        # (served_trips, avg_condition, avg_travel_time, total_cost)
        # we want: max, max, min, min
        weights = (1.0, 1.0, -1.0, -1.0)

        if not hasattr(creator, "FitnessMulti"):
            creator.create("FitnessMulti", base.Fitness, weights=weights)
        if not hasattr(creator, "Individual"):
            creator.create("Individual", list, fitness=creator.FitnessMulti)

        self.toolbox.register("attr_time", self._random_start)
        self.toolbox.register(
            "individual",
            tools.initRepeat,
            creator.Individual,
            self.toolbox.attr_time,
            n=len(self.asset_ids),
        )
        self.toolbox.register("population", tools.initRepeat, list, self.toolbox.individual)

        # Better operators for bounded continuous genes:
        # - SBX crossover
        # - Polynomial bounded mutation
        self.toolbox.register("mate", tools.cxTwoPoint)# , alpha=0.5) # .cxSimulatedBinaryBounded, low=0.0, up=self.config.horizon_hours, eta=0.5)
        # a = [0.1, 0.2, 0.3, 0.4]
        # print("a", a)
        # b = [0.5, 0.6, 0.7, 0.8]
        # print("b", b)
        # c = tools.cxTwoPoint(a, b)
        # print("c", c)
        # exit(0)
        self.toolbox.register(
            "mutate",
            tools.mutPolynomialBounded,
            low=0.0,
            up=self.config.horizon_hours,
            eta=0.2,
            indpb=0.3,
        )
        # print(tools.mutPolynomialBounded([0,5,15, 20], eta=0.2, indpb=0.3, low=0.0, up=self.config.horizon_hours))
        # exit()
        self.toolbox.register("select", tools.selNSGA2)
        # Parent selection: binary tournament on dominance, ties broken by crowding distance
        self.toolbox.register("select_parents", tools.selTournamentDCD)

    def _random_start(self) -> float:
        return random.uniform(0.0, self.config.horizon_hours)

    def _bounds_correction(self, individual: List[float]) -> None:
        # Genes may exceed horizon - duration: such a job does not finish in the horizon and
        # is deferred (no cost, no condition reset). That lets the GA trade cost against condition.
        for i in range(len(individual)):
            individual[i] = max(0.0, min(self.config.horizon_hours, float(individual[i])))

    def _evaluate_all(self, individuals: List) -> None:
        """Simulate all individuals (in parallel if a worker pool is active) and set their fitness."""
        schedules = []
        for ind in individuals:
            self._bounds_correction(ind)
            schedules.append({asset_id: float(ind[i]) for i, asset_id in enumerate(self.asset_ids)})
        if self._pool is not None:
            results = self._pool.map(_simulate, schedules)
        else:
            results = [_simulate(s) for s in schedules]
        for ind, (result, dt) in zip(individuals, results):
            ind.fitness.values = self._objectives(result, dt)

    def _objectives(self, result: SimulationResult, dt: float) -> tuple:
        self.eval_counter += 1

        # Objectives:
        # - maximize served trips
        # - maximize avg condition
        # - minimize avg travel time
        # - minimize total cost
        served = float(result.served_trips)

        # Optional: penalize failures explicitly by reducing served (soft)
        # If you prefer hard service-level constraints, do that after Pareto filtering.
        served_adjusted = served #- 2.0 * float(result.failed_trips)

        if self.eval_counter % 50 == 0:  # alle 50 Evaluations
            self.logger.info(
                "Eval %d | served=%d failed=%d avg_time=%.3f avg_cond=%.3f cost=%.1f | sim=%.2fs",
                self.eval_counter,
                result.served_trips,
                result.failed_trips,
                result.avg_travel_time,
                result.avg_condition,
                result.total_cost,
                dt,
            )       


        return (
            served_adjusted,
            float(result.avg_condition),
            float(result.avg_travel_time),
            float(result.total_cost),
        )

    def _choose_solution_from_pareto(self, pareto: List, eps: float = 1e-9):
        """
        Choose a single solution from Pareto set using weighted normalized distance to ideal point:
        ideal = (max served, max cond, min time, min cost)
        Weights come from config.selection_weights = (condition, travel time, cost). Served trips are
        nearly constant (only trips still underway at the horizon end differ), so they get weight 0 here.
        """
        w_cond, w_time, w_cost = self.config.selection_weights
        w = np.array([0.0, w_cond, w_time, w_cost], dtype=float)

        fits = np.array([ind.fitness.values for ind in pareto], dtype=float)
        ideal = np.array([fits[:, 0].max(), fits[:, 1].max(), fits[:, 2].min(), fits[:, 3].min()], dtype=float)

        # normalize each objective to [0,1] range to avoid scale dominance
        mins = fits.min(axis=0)
        maxs = fits.max(axis=0)
        denom = np.maximum(maxs - mins, eps)
        norm = (fits - mins) / denom

        ideal_norm = (ideal - mins) / denom
        d = np.sqrt((w * (norm - ideal_norm) ** 2).sum(axis=1))
        return pareto[int(np.argmin(d))]

    def run(self, progress_cb: Optional[Callable[[int, Dict[str, float], tuple, tools.ParetoFront], None]] = None):
        self._progress_cb = progress_cb

        workers = min(self.config.workers or os.cpu_count() or 1, self.config.population_size)
        if workers <= 1:
            return self._run()
        with Pool(workers, initializer=_init_worker, initargs=self._worker_args) as pool:
            self._pool = pool
            try:
                return self._run()
            finally:
                self._pool = None

    def _run(self):
        pop = self.toolbox.population(n=self.config.population_size)

        # Evaluate initial population
        self._evaluate_all(pop)

        # NSGA-II requires initial sorting
        pop = self.toolbox.select(pop, len(pop))
        hall = tools.ParetoFront()
        hall.update(pop)

        # Generation 0 snapshot (after initial evaluation)
        best0 = self._choose_solution_from_pareto(list(hall))
        best_schedule0 = {asset_id: float(best0[i]) for i, asset_id in enumerate(self.asset_ids)}
        best_metrics0 = best0.fitness.values
        if self._progress_cb is not None:
            self._progress_cb(0, best_schedule0, best_metrics0, hall)

        for _gen in range(1, self.config.generations + 1):
            gen_start_time = time.perf_counter()
            # Standard NSGA-II variation: as many offspring as parents; crossover per pair with
            # crossover_prob, mutation per individual with mutation_prob.
            offspring = self.toolbox.select_parents(pop, len(pop))
            offspring = list(map(self.toolbox.clone, offspring))

            for c1, c2 in zip(offspring[::2], offspring[1::2]):
                if random.random() <= self.config.crossover_prob:
                    self.toolbox.mate(c1, c2)
                    del c1.fitness.values, c2.fitness.values

            for mutant in offspring:
                if random.random() <= self.config.mutation_prob:
                    self.toolbox.mutate(mutant)
                    del mutant.fitness.values

            invalid = [ind for ind in offspring if not ind.fitness.valid]
            self._evaluate_all(invalid)
            whole = pop + offspring
            # print(len(whole))
            # print()
            new_pop = self.toolbox.select(whole, self.config.population_size)
            # print("new_pop", new_pop)
            pop = new_pop
            # print("population after mating, mutation and selection", pop)
            hall.update(pop)
            # Write progress file so frontends can monitor progress
            try:
                outdir = Path("output")
                outdir.mkdir(parents=True, exist_ok=True)
                prog = {"current_generation": int(_gen), "total_generations": int(self.config.generations)}
                with open(outdir / "progress.json", "w", encoding="utf-8") as pf:
                    json.dump(prog, pf)
            except Exception as e:
                print(f"[DEBUG ga] failed to write progress.json: {e}", flush=True)

            # Choose representative “balanced” schedule from current Pareto
            best_gen = self._choose_solution_from_pareto(list(hall))
            best_schedule_gen = {asset_id: float(best_gen[i]) for i, asset_id in enumerate(self.asset_ids)}
            best_metrics_gen = best_gen.fitness.values
            if self._progress_cb is not None:
                self._progress_cb(_gen, best_schedule_gen, best_metrics_gen, hall)
            
            gen_elapsed = time.perf_counter() - gen_start_time
            print(f"[BENCHMARK] Generation {_gen} completed in {gen_elapsed:.2f} seconds", flush=True)

        # Final best (already selected in last iteration above, but recompute for clarity)
        best = self._choose_solution_from_pareto(list(hall))

        best_schedule = {asset_id: float(best[i]) for i, asset_id in enumerate(self.asset_ids)}
        best_metrics = best.fitness.values  # already evaluated

        if _gen % 1 == 0:
            self.logger.info(
                "Gen %d/%d | Pareto size=%d | pop[0] fitness=%s",
                _gen,
                self.config.generations,
                len(hall),
                pop[0].fitness.values,
            )

        return best_schedule, best_metrics, hall
