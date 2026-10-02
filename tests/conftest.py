import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from data_models import load_assets, load_edges, load_nodes, load_passenger_flows  # noqa: E402


@pytest.fixture(scope="session")
def network_data():
    data = ROOT / "data"
    return (
        load_nodes(str(data / "nodes_template.csv")),
        load_edges(str(data / "edges_template.csv")),
        load_assets(str(data / "assets_template.csv")),
        load_passenger_flows(str(data / "passenger_flows_day.csv")),
    )
