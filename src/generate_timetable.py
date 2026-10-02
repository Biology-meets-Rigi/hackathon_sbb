"""Write the synthetic GTFS timetable (data/gtfs) derived from the network data."""
from __future__ import annotations

import argparse

from data_models import load_edges, load_nodes
from timetable.synthetic import generate_feed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nodes", default="data/nodes_template.csv")
    parser.add_argument("--edges", default="data/edges_template.csv")
    parser.add_argument("--out", default="data/gtfs")
    args = parser.parse_args()
    generate_feed(load_nodes(args.nodes), load_edges(args.edges), args.out)
    print(f"wrote synthetic GTFS feed to {args.out}")


if __name__ == "__main__":
    main()
