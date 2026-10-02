# filepath: /Users/eliowanner/Documents/local_stuff/hackathon_sbb_2026/test2/hackathon_sbb/src/frontend.py
# Streamlit frontend (self-contained, hardcoded sample data)
# run with "streamlit run src/frontend.py"

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import date, timedelta
import math
from pathlib import Path
import random
from deap import tools as deap_tools
import json
import subprocess
import sys
import urllib.parse
import urllib.request
import hashlib
import time

# Reduce top whitespace (Streamlit header) so the title starts higher
st.markdown(
    """
    <style>
      header[data-testid="stHeader"] { display: none; }
      div[data-testid="stToolbar"] { visibility: hidden; height: 0%; position: fixed; }
      .block-container { padding-top: 0.5rem; padding-bottom: 1rem; }
      /* Streamlit adds a top spacer in some versions */
      div[data-testid="stVerticalBlock"] > div:has(> div.st-emotion-cache-1jicfl2) { margin-top: 0rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

# Extra overrides targeting slider track and progress fill specifically
st.markdown(
        """
        <style>
            /* Slider track (container) */
            [data-testid="stSlider"] div[style*="height"],
            [data-baseweb="slider"] div[style*="height"],
            [data-testid="stSlider"] .st-an,
            [data-baseweb="slider"] .st-an {
                background: #28a745 !important;
                border-radius: 6px !important;
                height: 6px !important;
            }

            /* Force the filled portion and overall track to use the primary green */
            [data-baseweb="slider"] .st-af, [data-baseweb="slider"] .st-ae, [data-testid="stSlider"] .st-af {
                background: linear-gradient(90deg, none 0%, none 100%) !important;
            }

            /* Progressbar: show a light-green track and a darker green filled portion.
               Avoid coloring the outer container solid green so the fill width remains visible. */
            [data-baseweb="progress-bar"] {
                background-color: #cfead6 !important; /* track */
                border-radius: 6px !important;
                padding: 2px !important;
            }

            /* Target the inner div that typically receives an inline width (the filled portion). */
            [data-baseweb="progress-bar"] > div > div > div,
            [data-baseweb="progress-bar"] div[style*="width"],
            div[role="progressbar"] > div > div > div,
            div[role="progressbar"] div[style*="width"] {
                background-color: #28a745 !important; /* filled */
                background-image: none !important;
                box-shadow: none !important;
                border-radius: 6px !important;
            }

            /* Additional targets: Streamlit's div-based sliders often nest the fill in child divs.
               Remove gradients and force a solid green fill on any matching elements. */
            div[role="slider"] {
                color: #28a745 !important;
                background-image: none !important;
                background-color: #28a745 !important;
                border-radius: 0px !important;
                height: 12px !important;
            }
            /* Direct child elements that render the filled portion */
            div[role="slider"] > div,
            div[role="slider"] > div > div,
            div[role="slider"] [style*="linear-gradient"] {
                background-image: none !important;
                background: none !important;
                background-color: none !important;
                box-shadow: none !important;
                border-radius: 12px !important;
                height: 12px !important;
            }

            /* Range input pseudo-elements: WebKit + Firefox */
            input[type="range"]::-webkit-slider-runnable-track { background: #28a745 !important; }
            input[type="range"]::-webkit-slider-thumb { background: #28a745 !important; }
            input[type="range"]::-moz-range-track { background: #28a745 !important; }
            input[type="range"]::-moz-range-progress { background: #28a745 !important; }

            /* Slider thumb numeric label (the little value bubble) */
            div[data-testid="stSliderThumbValue"],
            [data-testid="stSlider"] [data-testid="stSliderThumbValue"],
            .stSlider [data-testid="stSliderThumbValue"] {
                color: #90EE90 !important; /* light green */
                background-color: transparent !important;
                font-weight: 700 !important;
            }

            /* Fallback for generic progress elements */
            progress::-webkit-progress-value { background-color: #28a745 !important; }
            progress::-moz-progress-bar { background-color: #28a745 !important; }
        </style>
        """,
        unsafe_allow_html=True,
)

# Style the "Run Genetic Algorithm" button green using its aria-label
st.markdown(
        """
        <style>
            button[aria-label="Run Genetic Algorithm"] {
                background-color: #28a745 !important;
                color: #ffffff !important;
                border: none !important;
            }
            button[aria-label="Run Genetic Algorithm"]:hover {
                background-color: #218838 !important;
            }
        </style>
        """,
        unsafe_allow_html=True,
)

# Additional (broader) styling: target buttons in the sidebar to ensure the Run button becomes green
st.markdown(
        """
        <style>
            [data-testid="stSidebar"] button {
                background-color: #28a745 !important;
                color: #ffffff !important;
                border: none !important;
            }
            [data-testid="stSidebar"] button:hover {
                background-color: #218838 !important;
            }
        </style>
        """,
        unsafe_allow_html=True,
)

# Make sliders and progress bars green (multiple fallbacks for different Streamlit/ browser versions)
st.markdown(
        """
        <style>
            :root {
                --primary-color: #28a745;
                --primary: #28a745;
            }

            /* modern browsers: color range inputs */
            input[type="range"] {
                accent-color: #28a745;
            }

            /* WebKit slider thumb track color fallback */
            input[type="range"]::-webkit-slider-runnable-track { background: #28a745 !important; }
            input[type="range"]::-webkit-slider-thumb { background: #28a745 !important; }

            /* Firefox thumb color */
            input[type="range"]::-moz-range-thumb { background: #28a745 !important; }

            /* Streamlit progress bar (role-based selector) */
            [role="progressbar"] > div { background-color: #28a745 !important; }
            /* Generic progress element */
            progress::-webkit-progress-value { background-color: #28a745 !important; }
            progress::-moz-progress-bar { background-color: #28a745 !important; }
        </style>
        """,
        unsafe_allow_html=True,
)

# Stronger, broader overrides for Streamlit widgets (fallbacks and multiple selectors)
st.markdown(
        """
        <style>
            /* Try forcing Streamlit's primary color variables */
            :root, .stApp, body {
                --primaryColor: #28a745 !important;
                --accent-color: #28a745 !important;
                --secondary: #e9f7ec !important;
            }

            /* Buttons in sidebar and main area */
            .stButton>button, .stButton>div>button, [data-testid="stSidebar"] button {
                background-color: #28a745 !important;
                color: #fff !important;
                border-color: #28a745 !important;
            }

            /* Range inputs and slider thumbs */
            input[type="range"] {
                accent-color: #28a745 !important;
                background: linear-gradient(90deg, #28a745 0%, #28a745 100%) !important;
            }
            input[type="range"]::-webkit-slider-thumb { background: #28a745 !important; }
            input[type="range"]::-moz-range-thumb { background: #28a745 !important; }
            /* Streamlit's div-based slider handles */
            div[role="slider"] {
                background-color: #28a745 !important;
                box-shadow: none !important;
            }

            /* Progress bars */
            .stProgress .css-6ntbpo, .stProgress > div > div {
                background-color: #28a745 !important;
            }
            .stProgress progress::-webkit-progress-value { background-color: #28a745 !important; }

            /* Tweak slider fill (some Streamlit versions use a pseudo-element) */
            .stSlider .css-1q8dd3e { background: #28a745 !important; }
        </style>
        """,
        unsafe_allow_html=True,
)

# --- Optional OSRM routing (for Bus edges) ---
# Uses the public demo server by default. For production, run your own OSRM instance.
OSRM_BASE_URL = "https://router.project-osrm.org"


def _hex_color_from_string(s: str) -> str:
    """Deterministic bright-ish color for a given key."""
    h = hashlib.md5(s.encode("utf-8")).hexdigest()
    # Keep it a bit brighter by mixing with a baseline.
    r = (int(h[0:2], 16) // 2) + 80
    g = (int(h[2:4], 16) // 2) + 80
    b = (int(h[4:6], 16) // 2) + 80
    return f"#{r:02x}{g:02x}{b:02x}"


@st.cache_data(show_spinner=False, ttl=60 * 60)
def _osrm_route_lon_lat(
    lon_a: float,
    lat_a: float,
    lon_b: float,
    lat_b: float,
    *,
    profile: str = "driving",
    overview: str = "full",
    geometries: str = "geojson",
    timeout_s: float = 10.0,
) -> list[tuple[float, float]]:
    """Fetch a route polyline from OSRM.

    Returns list of (lon, lat) points. Empty list on errors.
    """
    try:
        coords = f"{lon_a:.6f},{lat_a:.6f};{lon_b:.6f},{lat_b:.6f}"
        params = {
            "overview": overview,
            "geometries": geometries,
        }
        url = f"{OSRM_BASE_URL}/route/v1/{profile}/{coords}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"User-Agent": "hackathon_sbb_frontend"})
        with urllib.request.urlopen(req, timeout=float(timeout_s)) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        routes = payload.get("routes")
        if not routes:
            return []
        geom = routes[0].get("geometry")
        if not geom:
            return []
        # With geometries=geojson, geometry is {"coordinates": [[lon,lat], ...], "type": "LineString"}
        coords_list = geom.get("coordinates") if isinstance(geom, dict) else None
        if not isinstance(coords_list, list):
            return []
        pts: list[tuple[float, float]] = []
        for p in coords_list:
            if isinstance(p, (list, tuple)) and len(p) >= 2:
                try:
                    pts.append((float(p[0]), float(p[1])))
                except Exception:
                    continue
        return pts
    except Exception:
        return []
# removed components-based auto-refresh (manual refresh added below)


st.set_page_config(page_title="Schedule Visualizer", layout="wide")

st.title("Evolution x Mobility")

# Load best_result from output/summary.json (fallback to a small hardcoded example)
project_root = Path(__file__).resolve().parents[1]
summary_json_path = project_root / "output" / "summary.json"

fallback_best_result = {"best_schedule": {
        "A1": 3.1712530261677268,
        "A2": 1.508604770429887,
        "A3": 0.0,
        "A4": 60.61600586577646,
    },
    # New schema: objectives
    "objectives": {
        "condition_penalty": 0.024060787510129356,
        "travel_penalty": 0.09585103276242014,
        "cost_penalty": 1134000.0,
    },
}

# ---- NSGA-II controls (sidebar) ----
with st.sidebar:
    st.header("Parameters")
    w1 = st.slider("Objective Weight: Track Condition", 0.0, 1.0, 0.3, 0.01)
    w2 = st.slider("Objective Weight: Travel Time", 0.0, 1.0, 0.4, 0.01)
    w3 = st.slider("Objective Weight: Cost", 0.0, 1.0, 0.3, 0.01)
    # Normalize weights so they sum to 1 (avoid passing all zeros)
    _total_w = float(w1 + w2 + w3)
    if _total_w <= 0:
        weights = (0.33, 0.34, 0.33)
    else:
        weights = (w1 / _total_w, w2 / _total_w, w3 / _total_w)
    # st.write(f"Normalized weights: {weights[0]:.2f}, {weights[1]:.2f}, {weights[2]:.2f}")

    backend = st.selectbox(
        "Simulation backend",
        ["prodsys", "simpy"],
        help="prodsys: stations, lines and vehicles as a prodsys production system. simpy: own, faster model.",
    )
    group_size = st.number_input(
        "Passengers per simulated group (prodsys)", min_value=1, max_value=50, value=10, step=1,
        disabled=backend != "prodsys",
    )
    # prodsys needs ~10 s per evaluated plan and simulated week, so the default horizon is two weeks
    horizon = st.slider("Observation period (Days)", 1.0, 200.0, 14.0, 1.0)
    population = st.slider("Population", 4, 200, 24, 4)
    cx = st.slider("Crossover", 0.0, 1.0, 0.6, 0.01)
    mut = st.slider("Mutation", 0.0, 1.0, 0.3, 0.01)
    # Optionally show generations/seed inputs if needed
    generations = st.number_input("Generations", min_value=1, max_value=10000, value=10, step=1)
    
    run_now = st.button("Run Genetic Algorithm", )
    status_area = st.empty()
    

try:
    with open(summary_json_path, "r", encoding="utf-8") as f:
        best_result = json.load(f)
    if not isinstance(best_result, dict):
        raise ValueError("summary.json did not contain a JSON object")
except Exception as e:
    st.warning(f"Could not read {summary_json_path}: {e}. Falling back to hardcoded sample data.")
    best_result = fallback_best_result
else:
    # Show last-modified time (if available)
    try:
        mtime = summary_json_path.stat().st_mtime
        import datetime

        lm = datetime.datetime.fromtimestamp(mtime).isoformat()
        st.sidebar.write(f"Loaded summary.json ({lm}, backend: {best_result.get('backend', 'unknown')})")
    except Exception:
        pass
        st.sidebar.write("Loaded summary.json")

# Remove internal/unused keys from the loaded result so they are not shown in the frontend
if isinstance(best_result, dict):
    # top-level key
    best_result.pop("served_adjusted", None)
    # nested in objectives or weighted_objectives if present
    if "objectives" in best_result and isinstance(best_result["objectives"], dict):
        best_result["objectives"].pop("served_adjusted", None)
    if "weighted_objectives" in best_result and isinstance(best_result["weighted_objectives"], dict):
        best_result["weighted_objectives"].pop("served_adjusted", None)

# Show generation progress if available from output/progress.json
progress_path = project_root / "output" / "progress.json"
# Initialize session state for progress tracking so we can persist a last-known value
if "ga_progress" not in st.session_state:
    st.session_state.ga_progress = {"cur": 0, "tot": int(generations) if 'generations' in locals() else 1}

# Robustly attempt to read progress.json (retry on transient read/write races)
prog = None
for _attempt in range(5):
    try:
        with open(progress_path, "r", encoding="utf-8") as pf:
            prog = json.load(pf)
        break
    except (json.JSONDecodeError, OSError):
        time.sleep(0.05)
    except Exception:
        prog = None
        break

if prog:
    try:
        cur = int(prog.get("current_generation", 0))
        tot = int(prog.get("total_generations", int(generations) if 'generations' in locals() else 1))
        st.session_state.ga_progress = {"cur": cur, "tot": tot}
    except Exception:
        pass

# Display the last-known progress (updates even if reading failed briefly)
cur = st.session_state.ga_progress.get("cur", 0)
tot = st.session_state.ga_progress.get("tot", int(generations) if 'generations' in locals() else 1)
pct = int((cur / tot) * 100) if tot > 0 else 0
st.sidebar.write(f"Generation: {cur}/{tot}")
st.sidebar.progress(pct)

# Backwards-compatible shim (if some older file still uses weighted_objectives)
if "objectives" not in best_result and "weighted_objectives" in best_result:
    best_result["objectives"] = best_result.get("weighted_objectives")

# Part 1: Map with nodes and edges
col_left, col_right = st.columns([1, 1], gap="large")

with col_left:
    st.subheader("Network map")

# Load nodes from CSV (data/nodes.csv). Format:
# node_id,location_x,location_y  (location_x=lat, location_y=lon)
data_dir = Path(__file__).resolve().parents[1] / "data"
nodes_csv_path = data_dir / "nodes_template.csv"
edges_csv_path = data_dir / "edges_template.csv"

fallback_nodes = [
    {"id": "N1", "lat": 47.3769, "lon": 8.5417},  # Zurich
    {"id": "N2", "lat": 46.9480, "lon": 7.4474},  # Bern
    {"id": "N3", "lat": 46.2044, "lon": 6.1432},  # Geneva
    {"id": "N4", "lat": 47.5596, "lon": 7.5886},  # Basel
    {"id": "N5", "lat": 46.5197, "lon": 6.6323},  # Lausanne
]

try:
    print(f"[DEBUG frontend] reading nodes from {nodes_csv_path}", flush=True)
    df_nodes = pd.read_csv(nodes_csv_path)
    required_cols = {"node_id", "location_x", "location_y"}
    if not required_cols.issubset(set(df_nodes.columns)):
        raise ValueError(
            f"nodes.csv must contain columns {sorted(required_cols)}; got {sorted(df_nodes.columns)}"
        )

    df_nodes = df_nodes.dropna(subset=["node_id", "location_x", "location_y"]).copy()
    df_nodes["location_x"] = pd.to_numeric(df_nodes["location_x"], errors="coerce")
    df_nodes["location_y"] = pd.to_numeric(df_nodes["location_y"], errors="coerce")
    df_nodes = df_nodes.dropna(subset=["location_x", "location_y"]).reset_index(drop=True)

    nodes = [
        {"id": str(r.node_id), "lat": float(r.location_x), "lon": float(r.location_y)}
        for r in df_nodes.itertuples(index=False)
    ]
    print(f"[DEBUG frontend] loaded {len(nodes)} nodes", flush=True)
    if len(nodes) == 0:
        nodes = fallback_nodes
except Exception as e:
    st.warning(f"Could not read nodes from {nodes_csv_path}: {e}. Falling back to hardcoded nodes.")
    nodes = fallback_nodes

# Load edges from CSV (data/edges.csv). For the map we only use start_node and end_node.
fallback_edges = [
    ("N1", "N2"),
    ("N1", "N4"),
    ("N2", "N5"),
    ("N3", "N5"),
    ("N4", "N1"),
]

def _parse_lon_lat_path(value) -> list[tuple[float, float]]:
    """Parse a polyline from CSV.

    Supported formats:
    - JSON array of [lon, lat] pairs: [[8.5, 47.3], [8.6, 47.35]]
    - JSON array of objects: [{"lon": 8.5, "lat": 47.3}, ...]

    Returns list of (lon, lat). Empty list on missing/invalid.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []

    if isinstance(value, (list, tuple)):
        raw = value
    else:
        s = str(value).strip()
        if not s:
            return []
        try:
            raw = json.loads(s)
        except Exception:
            return []

    pts: list[tuple[float, float]] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                try:
                    lon = float(item[0])
                    lat = float(item[1])
                    pts.append((lon, lat))
                except Exception:
                    continue
            elif isinstance(item, dict) and "lon" in item and "lat" in item:
                try:
                    pts.append((float(item["lon"]), float(item["lat"])))
                except Exception:
                    continue
    return pts


def _edge_key(a: str, b: str) -> str:
    return f"{a}->{b}"

try:
    print(f"[DEBUG frontend] reading edges from {edges_csv_path}", flush=True)
    df_edges = pd.read_csv(edges_csv_path)
    required_edge_cols = {"start_node", "end_node", "capacity_at_day"}
    if not required_edge_cols.issubset(set(df_edges.columns)):
        raise ValueError(
            f"edges.csv must contain columns {sorted(required_edge_cols)}; got {sorted(df_edges.columns)}"
        )
    df_edges = df_edges.dropna(subset=["start_node", "end_node"]).copy()
    df_edges["capacity_at_day"] = pd.to_numeric(df_edges["capacity_at_day"], errors="coerce")

    # Keep capacity + mode with each edge so we can style and optionally route it.
    edges = []
    for r in df_edges.itertuples(index=False):
        a = str(getattr(r, "start_node"))
        b = str(getattr(r, "end_node"))
        cap = getattr(r, "capacity_at_day", None)
        cap_val = float(cap) if cap is not None and not pd.isna(cap) else None
        mode_val = getattr(r, "mode", None) if "mode" in df_edges.columns else None
        edge_id_val = getattr(r, "edge_id", None) if "edge_id" in df_edges.columns else None
        edges.append(
            {
                "a": a,
                "b": b,
                "edge_id": (str(edge_id_val) if edge_id_val is not None and not pd.isna(edge_id_val) else _edge_key(a, b)),
                "capacity_at_day": cap_val,
                "mode": (str(mode_val) if mode_val is not None and not pd.isna(mode_val) else None),
            }
        )
    print(f"[DEBUG frontend] loaded {len(edges)} edges", flush=True)
    if len(edges) == 0:
        edges = fallback_edges
except Exception as e:
    st.warning(f"Could not read edges from {edges_csv_path}: {e}. Falling back to hardcoded edges.")
    edges = fallback_edges

# Load asset->edge mapping so the schedule timeline can share colors with edges.
assets_csv_path = data_dir / "assets_template.csv"
asset_edge_by_asset_id: dict[str, str] = {}

# Load assets for plotting on the map (emoji by type)
_assets_for_map: list[dict] = []

# Asset marker label (single-letter) is always used (emojis/symbols are unreliable in Plotly map traces).

def _asset_label(asset_type: str | None) -> str:
    t = (asset_type or "").strip()
    return {
        "Kai": "K",
        "Weiche": "W",
        "Brücke": "B",
        "Tunnel": "T",
        "Seilen": "S",
        "K_Straße": "R",  # road
        "Kabine": "G",   # gondola
    }.get(t, "?")

def _asset_color(asset_type: str | None) -> str:
    t = (asset_type or "").strip()
    return {
        "Kai": "#1f77b4",      # blue
        "Weiche": "#ff7f0e",   # orange
        "Brücke": "#7f7f7f",   # gray
        "Tunnel": "#2ca02c",   # green
        "Seilen": "#9467bd",   # purple
        "K_Straße": "#d62728", # red
    }.get(t, "#111111")

try:
    df_assets_for_edges = pd.read_csv(assets_csv_path)
    if {"asset_id", "edge_id"}.issubset(set(df_assets_for_edges.columns)):
        df_assets_for_edges = df_assets_for_edges.dropna(subset=["asset_id", "edge_id"]).copy()
        asset_edge_by_asset_id = {
            str(r.asset_id): str(r.edge_id) for r in df_assets_for_edges.itertuples(index=False)
        }

    # Assets for the map (optional columns are tolerated)
    required_map_cols = {"asset_id", "asset_type", "location_x", "location_y"}
    if required_map_cols.issubset(set(df_assets_for_edges.columns)):
        df_map = df_assets_for_edges.dropna(subset=["asset_id", "asset_type", "location_x", "location_y"]).copy()
        df_map["location_x"] = pd.to_numeric(df_map["location_x"], errors="coerce")  # lat
        df_map["location_y"] = pd.to_numeric(df_map["location_y"], errors="coerce")  # lon
        df_map = df_map.dropna(subset=["location_x", "location_y"]).reset_index(drop=True)

        for r in df_map.itertuples(index=False):
            _assets_for_map.append(
                {
                    "asset_id": str(getattr(r, "asset_id")),
                    "edge_id": str(getattr(r, "edge_id")) if hasattr(r, "edge_id") and getattr(r, "edge_id") is not None else None,
                    "asset_type": str(getattr(r, "asset_type")),
                    "lat": float(getattr(r, "location_x")),
                    "lon": float(getattr(r, "location_y")),
                    "condition_initial": getattr(r, "condition_initial", None),
                }
            )
except Exception:
    asset_edge_by_asset_id = {}
    _assets_for_map = []

# Deterministic color per edge_id for consistent map + schedule colors.
edge_color_by_edge_id: dict[str, str] = {}
if len(edges) > 0 and isinstance(edges[0], dict):
    for e in edges:
        eid = str(e.get("edge_id") or _edge_key(e.get("a", ""), e.get("b", "")))
        if eid not in edge_color_by_edge_id:
            edge_color_by_edge_id[eid] = _hex_color_from_string(eid)

# Optional: load train (Zug) polylines from data/edge_paths_train.csv
edge_paths_csv_path = data_dir / "edge_paths_train.csv"
train_paths: dict[str, list[tuple[float, float]]] = {}
_train_paths_load_error: str | None = None
try:
    if edge_paths_csv_path.exists():
        # NOTE: path_lon_lat contains commas. In CSV this MUST be quoted, e.g.
        # "[[8.53,47.04],[8.54,47.05]]"
        # We use the python engine + backslash escape to be more forgiving.
        try:
            df_paths = pd.read_csv(edge_paths_csv_path, engine="python", escapechar="\\")
        except Exception:
            # Fallback: allow a pipe-separated file if you prefer to avoid quoting JSON.
            df_paths = pd.read_csv(edge_paths_csv_path, sep="|", engine="python")

        required_path_cols = {"start_node", "end_node", "path_lon_lat"}
        if not required_path_cols.issubset(set(df_paths.columns)):
            raise ValueError(
                f"edge_paths_train.csv must contain columns {sorted(required_path_cols)}; got {sorted(df_paths.columns)}"
            )
        df_paths = df_paths.dropna(subset=["start_node", "end_node"]).copy()
        for r in df_paths.itertuples(index=False):
            a = str(getattr(r, "start_node"))
            b = str(getattr(r, "end_node"))
            path_val = getattr(r, "path_lon_lat", None)
            pts = _parse_lon_lat_path(path_val)
            if pts:
                train_paths[_edge_key(a, b)] = pts
except Exception as e:
    _train_paths_load_error = str(e)
    st.warning(f"Could not read train edge paths from {edge_paths_csv_path}: {e}. Falling back to straight lines.")

# with st.sidebar:
#     st.caption(
#         "Train routes (mode=Zug) can optionally be drawn from data/edge_paths_train.csv "
#         "(columns: start_node,end_node,path_lon_lat)."
#     )
#     st.caption(f"Assets loaded for map: {len(_assets_for_map)}")
#     st.caption(
#         "If you use comma-separated CSV, wrap path_lon_lat in quotes, e.g. "
#         "\"[[8.53,47.04],[8.54,47.05]]\". "
#         "Alternatively you can use a pipe-separated file (same name) with rows like: "
#         "start_node|end_node|path_lon_lat"
#     )
#     if _train_paths_load_error:
#         st.warning(f"edge_paths_train.csv parse error: {_train_paths_load_error}")

# Build coordinate maps
coord = {n["id"]: (n["lon"], n["lat"]) for n in nodes}

# Filter edges that reference unknown nodes; if nothing remains, build a simple chain.
if len(edges) > 0 and isinstance(edges[0], dict):
    edges = [e for e in edges if e["a"] in coord and e["b"] in coord]
    if len(edges) == 0:
        node_ids = [n["id"] for n in nodes]
        edges = [
            {"a": a, "b": b, "capacity_at_day": None}
            for a, b in (list(zip(node_ids, node_ids[1:])) if len(node_ids) > 1 else [])
        ]
else:
    # fallback_edges list of tuples
    edges = [(a, b) for (a, b) in edges if a in coord and b in coord]
    if len(edges) == 0:
        node_ids = [n["id"] for n in nodes]
        edges = list(zip(node_ids, node_ids[1:])) if len(node_ids) > 1 else []

# Create a Plotly figure: lines for edges and scatter for nodes
edge_traces = []

# Precompute capacity range for nicer scaling (typical values: ~1000–5000)
_caps = []
if len(edges) > 0 and isinstance(edges[0], dict):
    _caps = [e.get("capacity_at_day") for e in edges if e.get("capacity_at_day") is not None]
_cap_min = float(min(_caps)) if _caps else None
_cap_max = float(max(_caps)) if _caps else None

def _edge_width_from_capacity(capacity_at_day: float | None) -> float:
    # Map capacity to a visually reasonable width range.
    # For your current data (about 1000–5000), linear scaling gives clearer differences.
    if capacity_at_day is None or capacity_at_day <= 0:
        return 2.0

    # If we don't have a range (missing data or constant capacity), fall back to a default.
    if _cap_min is None or _cap_max is None or _cap_min == _cap_max:
        return 4.0

    # Normalize to [0, 1]
    t = (float(capacity_at_day) - _cap_min) / (_cap_max - _cap_min)
    t = max(0.0, min(1.0, t))

    # Map to a width range that is visually distinct but not too thick.
    min_w, max_w = 2.0, 10.0
    return float(min_w + t * (max_w - min_w))

if len(edges) > 0 and isinstance(edges[0], dict):
    for e in edges:
        a, b = e["a"], e["b"]
        if a not in coord or b not in coord:
            continue

        # Use polyline only for trains and only if we have a path.
        pts: list[tuple[float, float]] = []
        mode = (e.get("mode") or "").strip()
        if mode in ("Zug", "Schiff"):
            pts = train_paths.get(_edge_key(a, b), [])
        elif mode == "Bus":
            lon_a, lat_a = coord[a]
            lon_b, lat_b = coord[b]
            pts = _osrm_route_lon_lat(lon_a, lat_a, lon_b, lat_b)

        # If a path exists, ensure it connects the endpoints (prepend/append if needed).
        if pts:
            lon_a, lat_a = coord[a]
            lon_b, lat_b = coord[b]
            if (abs(pts[0][0] - lon_a) > 1e-6) or (abs(pts[0][1] - lat_a) > 1e-6):
                pts = [(lon_a, lat_a)] + pts
            if (abs(pts[-1][0] - lon_b) > 1e-6) or (abs(pts[-1][1] - lat_b) > 1e-6):
                pts = pts + [(lon_b, lat_b)]

        if pts:
            lon = [p[0] for p in pts]
            lat = [p[1] for p in pts]
        else:
            lon_a, lat_a = coord[a]
            lon_b, lat_b = coord[b]
            lon = [lon_a, lon_b]
            lat = [lat_a, lat_b]

        w = _edge_width_from_capacity(e.get("capacity_at_day"))
        edge_id = str(e.get("edge_id") or _edge_key(a, b))
        edge_color = edge_color_by_edge_id.get(edge_id) or _hex_color_from_string(edge_id)
        edge_traces.append(
            go.Scattermap(
                lon=lon,
                lat=lat,
                mode="lines",
                line=dict(width=w, color=edge_color),
                hovertemplate=(
                    f"<b>{a}  {b}</b><br>"
                    f"edge_id: {edge_id}<br>"
                    f"mode: {mode}<br>"
                    f"capacity_at_day: {e.get('capacity_at_day')}<br>"
                    f"path_points: {len(pts) if pts else 0}<extra></extra>"
                ),
            )
        )
else:
    for a, b in edges:
        lon_a, lat_a = coord[a]
        lon_b, lat_b = coord[b]
        edge_traces.append(
            go.Scattermap(
                lon=[lon_a, lon_b],
                lat=[lat_a, lat_b],
                mode="lines",
                line=dict(width=2, color="blue"),
                hoverinfo="none",
            )
        )

node_trace = go.Scattermap(
    lon=[n["lon"] for n in nodes],
    lat=[n["lat"] for n in nodes],
    mode="markers+text",
    marker=go.scattermap.Marker(size=12, color="black", symbol="circle"),
    text=[n["id"] for n in nodes],
    textposition="top center",
    textfont=dict(color="black")
)

node_trace_inner = go.Scattermap(
    lon=[n["lon"] for n in nodes],
    lat=[n["lat"] for n in nodes],
    marker=go.scattermap.Marker(size=6, color="white", symbol="circle"),
)

# Assets layer (emoji labels)
asset_traces = []
if _assets_for_map:
    _customdata = [
        [a.get("asset_id"), a.get("asset_type"), a.get("edge_id"), a.get("condition_initial")]
        for a in _assets_for_map
    ]

    # Single-letter label overlay + colored marker.
    asset_traces.append(
        go.Scattermap(
            lon=[a["lon"] for a in _assets_for_map],
            lat=[a["lat"] for a in _assets_for_map],
            mode="markers+text",
            marker=go.scattermap.Marker(
                size=15,
                #color=[_asset_color(a.get("asset_type")) for a in _assets_for_map],
                color="white",
                symbol="circle",
                opacity=0.7,
            ),
            text=[_asset_label(a.get("asset_type")) for a in _assets_for_map],
            textposition="middle center",
            textfont=dict(size=10, color="black"),
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                "type: %{customdata[1]}<br>"
                "edge_id: %{customdata[2]}<br>"
                "condition_initial: %{customdata[3]}<extra></extra>"
            ),
            customdata=_customdata,
        )
    )

# Compose and display map. Use an open-access map style that does not require a map token (MapLibre).
fig_map = go.Figure(data=edge_traces + [node_trace, node_trace_inner] + asset_traces)
if len(nodes) > 0:
    center_lat = sum(n["lat"] for n in nodes) / len(nodes)
    center_lon = sum(n["lon"] for n in nodes) / len(nodes)
else:
    center_lat, center_lon = 46.8, 8.0

fig_map.update_layout(
    map=dict(
        # Black/white basemap
        style="carto-positron",
        center=dict(lat=float(center_lat), lon=float(center_lon)),
        zoom=10,
    ),
    margin={"l": 0, "r": 0, "t": 0, "b": 0},
    height=450,
    showlegend=False,
)

with col_left:
    st.plotly_chart(fig_map, width="stretch")

    # Asset label legend (letters on the map)
    st.markdown("**Asset legend**")
    _legend_items = [
        ("K", "Pier"),
        ("W", "Switch"),
        ("B", "Bridge"),
        ("T", "Tunnel"),
        ("S", "Roap"),
        ("R", "Road"),
        ("G", "Gondola"),
        ("?", "Unknown / other"),
    ]
    st.markdown(
        "<div style='display:flex;gap:10px;flex-wrap:wrap;align-items:center'>"
        + "".join(
            f"<div style='display:flex;align-items:center;gap:6px'>"
            f"<span style='display:inline-flex;justify-content:center;align-items:center;"
            f"width:22px;height:22px;border-radius:50%;border:1px solid #999;background:#fff;"
            f"color:#000;"
            f"font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Arial;"
            f"font-size:12px;font-weight:700'>{lit}</span>"
            f"<span style='font-size:12px;'>{name}</span>"
            f"</div>"
            for lit, name in _legend_items
        )
        + "</div>",
        unsafe_allow_html=True,
    )

# Part 2: Calendar / Gantt view for assets
with col_right:
    st.subheader("Construction schedule timeline")

# Assets to show. We'll derive start day from the best_result values.
assets = list(best_result["best_schedule"].keys())
start_values = best_result["best_schedule"]
# Jobs that do not finish within the horizon are deferred (not executed in this plan)
deferred_assets = set(best_result.get("deferred_assets", []))

# Base date for the visualization
base_date = date(2026, 1, 1)

# Load per-asset maintenance durations (half-days) from assets_template.csv
# (assets_csv_path already defined above)
DEFAULT_DURATION_DAYS = 4.0

duration_days_by_asset: dict[str, float] = {}
try:
    df_assets = pd.read_csv(assets_csv_path)
    required_asset_cols = {"asset_id", "maintenance_duration_half_days"}
    if not required_asset_cols.issubset(set(df_assets.columns)):
        raise ValueError(
            f"assets_template.csv must contain columns {sorted(required_asset_cols)}; got {sorted(df_assets.columns)}"
        )

    df_assets = df_assets.dropna(subset=["asset_id"]).copy()
    df_assets["maintenance_duration_half_days"] = pd.to_numeric(
        df_assets["maintenance_duration_half_days"], errors="coerce"
    )
    # Convert half-days -> days
    df_assets["duration_days"] = df_assets["maintenance_duration_half_days"] / 2.0

    duration_days_by_asset = {
        str(r.asset_id): (
            float(r.duration_days)
            if r.duration_days is not None and not pd.isna(r.duration_days) and float(r.duration_days) > 0
            else DEFAULT_DURATION_DAYS
        )
        for r in df_assets.itertuples(index=False)
    }
except Exception as e:
    st.warning(
        f"Could not read asset durations from {assets_csv_path}: {e}. Falling back to {DEFAULT_DURATION_DAYS} days."
    )

rows = []
for asset in assets:
    raw_start = start_values[asset]
    # GA start values are hours since the start of the horizon (base_date 00:00)
    start_dt = pd.Timestamp(base_date) + pd.Timedelta(hours=float(raw_start))

    duration_days = float(duration_days_by_asset.get(asset, DEFAULT_DURATION_DAYS))
    end_dt = start_dt + pd.Timedelta(days=duration_days)

    rows.append(
        {
            "Asset": asset,
            "Start": start_dt,
            "End": end_dt,
            "raw_start": float(raw_start),
            "duration_days": float(duration_days),
            "Status": "deferred" if asset in deferred_assets else "planned",
        }
    )

df_schedule = pd.DataFrame(rows)

# Ensure correct dtypes
df_schedule["Start"] = pd.to_datetime(df_schedule["Start"], utc=False)
df_schedule["End"] = pd.to_datetime(df_schedule["End"], utc=False)
df_schedule["raw_start"] = df_schedule["raw_start"].astype(float)

# Plotly sometimes stumbles over non-primitive dtypes present in the dataframe.
# Pass a minimal dataframe to the chart to guarantee JSON-serializability.
df_schedule_plot = df_schedule[["Asset", "Start", "End"]].copy()
df_schedule_plot["Start"] = df_schedule_plot["Start"].dt.to_pydatetime()
df_schedule_plot["End"] = df_schedule_plot["End"].dt.to_pydatetime()

# Create a Gantt-like timeline (manual) to avoid datetime/timedelta JSON issues.
# We plot in "days since base_date" and format tick labels ourselves.
df_bar = df_schedule[df_schedule["Status"] == "planned"].copy()
df_bar["start_day"] = (df_bar["Start"] - pd.Timestamp(base_date)) / pd.Timedelta(days=1)
# Use the computed duration (may be fractional) rather than re-deriving it from timestamps.
df_bar["duration_days"] = df_bar.get("duration_days", DEFAULT_DURATION_DAYS).astype(float)

fig_timeline = go.Figure()
for _, r in df_bar.iterrows():
    asset_id = str(r["Asset"])
    edge_id_for_asset = asset_edge_by_asset_id.get(asset_id)
    bar_color = (
        edge_color_by_edge_id.get(edge_id_for_asset)
        if edge_id_for_asset is not None
        else _hex_color_from_string(asset_id)
    )
    start_day = float(r["start_day"])
    dur_days = float(r["duration_days"])
    end_day = float(start_day + dur_days)
    start_date = r["Start"].strftime("%Y-%m-%d %H:%M")
    end_date = r["End"].strftime("%Y-%m-%d %H:%M")

    fig_timeline.add_trace(
        go.Bar(
            y=[r["Asset"]],
            x=[dur_days],
            base=[start_day],
            marker=dict(color=bar_color),
            customdata=[
                [
                    start_day,
                    dur_days,
                    end_day,
                    start_date,
                    end_date,
                    edge_id_for_asset,
                ]
            ],
            orientation="h",
            name=r["Asset"],
            hovertemplate=(
                "<b>%{y}</b><br>"
                "Edge: %{customdata[5]}<br>"
                "Duration: %{customdata[1]:.1f} days<br>"
                "Start: %{customdata[3]}<br>"
                "End: %{customdata[4]}"
                "<extra></extra>"
            ),
        )
    )

def _tick_label(day_offset: int) -> str:
    d = base_date + timedelta(days=int(day_offset))
    return d.isoformat()

max_end = float((df_bar["start_day"] + df_bar["duration_days"]).max()) if not df_bar.empty else 0.0
tick_step = 7
max_tick = int(math.ceil(max_end / tick_step) * tick_step)
tickvals = list(range(0, max_tick + tick_step, tick_step))
ticktext = [_tick_label(v) for v in tickvals]

# Dynamically size the chart so all assets are visible.
# A fixed height clips rows when there are many assets.
row_height_px = 28
base_padding_px = 90
chart_height = int(base_padding_px + row_height_px * max(1, len(df_bar)))

fig_timeline.update_layout(
    barmode="stack",
    height=chart_height,
    margin={"l": 0, "r": 20, "t": 0, "b": 20},
    showlegend=False,
)
fig_timeline.update_yaxes(autorange="reversed")
fig_timeline.update_xaxes(title_text="Date", tickmode="array", tickvals=tickvals, ticktext=ticktext)

with col_right:
    st.plotly_chart(fig_timeline, width="stretch")
    if deferred_assets:
        st.caption(f"Deferred (not finished within horizon): {', '.join(sorted(deferred_assets))}")

# Objectives: show each objective as its own bar chart beneath the timeline
with col_right:
    st.subheader("Objectives")
    obj = best_result.get("objectives", {})
    if isinstance(obj, dict) and obj:
        # Hard-coded y-axis ranges for known objectives
        y_ranges = {
            "condition_penalty": (0.0, 1.0),
            "avg_condition": (0.0, 1.0),
            "travel_penalty": (0.0, 1.0),
            "avg_travel_time": (0.0, 0.35),
            "total_cost": (0.0, 38000000.0),
        }

        # Exclude some objectives from charting (e.g., cost_penalty per request)
        exclude_keys = {"cost_penalty"}
        plot_keys = [k for k in obj.keys() if k not in exclude_keys]

        if len(plot_keys) == 0:
            st.info("No objectives to display (all excluded).")
        else:
            # Map objective keys to descriptive titles
            title_map = {
                "condition_penalty": "Track Condition Penalty",
                "avg_condition": "Average Track Condition",
                "travel_penalty": "Travel Time Penalty",
                "avg_travel_time": "Average Travel Time",
                "total_cost": "Total Cost",
                "cost_penalty": "Cost Penalty",
            }

            cols = st.columns(len(plot_keys))
            for col, k in zip(cols, plot_keys):
                with col:
                    try:
                        val = float(obj.get(k, float('nan')))
                    except Exception:
                        val = float('nan')

                    ymin, ymax = y_ranges.get(k, (0.0, max(1.0, abs(val) * 2.0)))
                    if not math.isnan(val):
                        ymax = max(ymax, val * 1.1)  # never clip the bar

                    # Show value as text on the bar; format to two decimals when numeric
                    text_label = "" if (isinstance(val, float) and (math.isnan(val))) else f"{val:.2f}"
                    fig_o = go.Figure(
                        go.Bar(
                            x=[k],
                            y=[val],
                            marker_color="#28a745",
                            text=[text_label],
                            textposition="auto",
                            textfont=dict(size=16),
                            texttemplate="<b>%{text}</b>",
                        )
                    )
                    fig_o.update_layout(
                        height=220,
                        margin={"l": 20, "r": 20, "t": 30, "b": 20},
                        yaxis=dict(range=[ymin, ymax], title_text="Value"),
                        title_text=title_map.get(k, k.replace("_", " ").title()),
                    )
                    st.plotly_chart(fig_o, width="stretch")
    else:
        st.info("No objectives found in result to display as charts.")

# Show a table of schedule values (below the chart)
with col_left:
    st.markdown("<div style='height:30px'></div>", unsafe_allow_html=True)
    st.write("Schedule table")
    df_display = df_schedule.drop(columns=["raw_start"], errors="ignore").copy()
    df_display = df_display.assign(
        Start=df_display.Start.dt.strftime("%Y-%m-%d %H:%M"),
        End=df_display.End.dt.strftime("%Y-%m-%d %H:%M"),
    )
    # Limit the table height to avoid excessive page length
    st.dataframe(df_display, height=250)


# ---- prodsys details for the shown schedule: lines, route choice, transfers ----
details = best_result.get("details") if isinstance(best_result, dict) else None
if isinstance(details, dict) and details.get("backend") == "prodsys":
    st.markdown("## prodsys: lines and routes")
    st.caption(
        f"Simulated with prodsys along the construction timetable, passengers in groups of {details.get('group_size')}. "
        "Load factor = boarded passengers / offered seats over all departures. Left behind = passengers that did "
        "not fit into a departure (counted per departure)."
    )
    line_rows = []
    for line_id, line in details.get("lines", {}).items():
        trips = line.get("trips", {})
        line_rows.append(
            {
                "Line": line_id,
                "Mode": line.get("mode"),
                "Stations": " - ".join(line.get("stations", [])),
                "Vehicles": line.get("vehicles"),
                "Seats per vehicle": line.get("seats_per_vehicle"),
                "Load factor %": round(float(line.get("load_factor_pct", 0.0)), 1),
                "Passengers": line.get("passengers"),
                "Left behind": line.get("left_behind_passengers"),
                "Works (h)": round(float(line.get("maintenance_hours", 0.0)), 1),
                "Trips planned": trips.get("planned"),
                "Cancelled": trips.get("cancelled"),
                "Delayed": trips.get("delayed"),
                "Rerouted": trips.get("rerouted"),
                "Short-turned": trips.get("short_turned"),
                "Mean delay (min)": round(float(trips.get("mean_delay_minutes", 0.0)), 1),
            }
        )
    st.dataframe(pd.DataFrame(line_rows), hide_index=True, width="stretch")

    # Outcome of the disposition per line: status palette (good / warning / serious / critical),
    # each with a symbol and label so the state never depends on color alone.
    outcome_series = [
        ("\u2713 on time", "#0ca30c", lambda t: t.get("on_time", 0)),
        ("\u26a0 delayed", "#fab219", lambda t: t.get("delayed", 0)),
        ("\u21bb rerouted / short-turned", "#ec835a", lambda t: t.get("rerouted", 0) + t.get("short_turned", 0)),
        ("\u2715 cancelled", "#d03b3b", lambda t: t.get("cancelled", 0)),
    ]
    line_ids = list(details.get("lines", {}).keys())
    fig_trips = go.Figure()
    for name, color, value in outcome_series:
        counts = [value(details["lines"][line_id].get("trips", {})) for line_id in line_ids]
        fig_trips.add_trace(
            go.Bar(
                x=line_ids,
                y=counts,
                name=name,
                marker=dict(color=color, line=dict(color="#ffffff", width=2)),
                hovertemplate="%{x} \u00b7 " + name + ": %{y} trips<extra></extra>",
            )
        )
    fig_trips.update_layout(
        barmode="stack",
        height=300,
        margin={"l": 20, "r": 20, "t": 40, "b": 20},
        title_text="Trips per line in the construction timetable",
        yaxis=dict(title_text="Trips", gridcolor="rgba(137,135,129,0.2)"),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0),
    )
    st.plotly_chart(fig_trips, width="stretch")

    route_rows = []
    for relation, route in details.get("routes", {}).items():
        for variant in route.get("variants", []):
            avg = variant.get("avg_travel_min")
            route_rows.append(
                {
                    "Relation": relation,
                    "Route": variant.get("via"),
                    "Share %": round(100.0 * float(variant.get("share", 0.0)), 1),
                    "Passengers": variant.get("passengers"),
                    "Avg travel (min)": round(float(avg), 1) if avg is not None else None,
                }
            )
    st.write("Route choice per relation")
    st.dataframe(pd.DataFrame(route_rows), hide_index=True, width="stretch")
    transfers = {k: v for k, v in details.get("transfers_per_station", {}).items() if v}
    if transfers:
        st.caption("Transfers per station: " + ", ".join(f"{k}: {v}" for k, v in transfers.items()))

st.markdown("## Mutation example")
st.write("This demonstrates a random individual (start offsets) before and after applying the same mutation operator used in the GA.")

try:
    # horizon slider is in days in the frontend UI
    horizon_days = float(horizon) if 'horizon' in locals() else 60.0

    # assets list is derived above from best_result
    assets_for_demo = list(assets) if isinstance(assets, (list, tuple)) and len(assets) > 0 else [f"A{i+1}" for i in range(5)]

    # Create a random individual (one float per asset)
    ind_before = [random.uniform(0.0, horizon_days) for _ in assets_for_demo]

    # Copy and mutate using the same operator as in src/ga.py
    ind_after = list(ind_before)
    # mutPolynomialBounded mutates in-place and returns a tuple (individual,)
    try:
        deap_tools.mutPolynomialBounded(ind_after, low=0.0, up=horizon_days, eta=0.2, indpb=0.2)
    except Exception:
        # Fallback: if DEAP not available or mutation fails, perform a small gaussian perturbation
        print("not available")
        #ind_after = [max(0.0, min(horizon_days, v + random.gauss(0, horizon_days * 0.05))) for v in ind_after]

    # Build a small comparison table
    rows_mut = []
    for a, b, c in zip(assets_for_demo, ind_before, ind_after):
        rows_mut.append({
            "Asset": a,
            "Start before (days)": float(b),
            "Start after (days)": float(c),
            "Delta (days)": float(c) - float(b),
        })

    df_mut = pd.DataFrame(rows_mut)
    # Mini timelines: visualize before and after as horizontal bars
    fig_before_mut = go.Figure()
    fig_after_mut = go.Figure()
    # Colors: before=blue, after unchanged=orange, after mutated=red
    COLOR_BEFORE = "#1f77b4"
    COLOR_AFTER_UNCHANGED = "#62c43c"
    COLOR_AFTER_MUTATED = "#d62728"
    for _, r in df_mut.iterrows():
        asset_id = str(r["Asset"])
        dur = float(duration_days_by_asset.get(asset_id, DEFAULT_DURATION_DAYS)) if 'duration_days_by_asset' in locals() else DEFAULT_DURATION_DAYS
        start_b = float(r["Start before (days)"])
        start_a = float(r["Start after (days)"])
        mutated = abs(start_a - start_b) > 1e-9
        start_b_i = int(math.floor(start_b))
        start_a_i = int(math.floor(start_a))

        fig_before_mut.add_trace(
            go.Bar(y=[asset_id], x=[dur], base=[start_b_i], orientation="h", marker=dict(color=COLOR_BEFORE), name="before")
        )
        after_color = COLOR_AFTER_MUTATED if mutated else COLOR_AFTER_UNCHANGED
        fig_after_mut.add_trace(
            go.Bar(y=[asset_id], x=[dur], base=[start_a_i], orientation="h", marker=dict(color=after_color), name="after")
        )

    fig_before_mut.update_layout(barmode="stack", height=200 + 28 * max(1, len(df_mut)), showlegend=False, margin={"l": 0, "r": 0, "t": 0, "b": 0})
    fig_before_mut.update_yaxes(autorange="reversed")
    fig_after_mut.update_layout(barmode="stack", height=200 + 28 * max(1, len(df_mut)), showlegend=False, margin={"l": 0, "r": 0, "t": 0, "b": 0})
    fig_after_mut.update_yaxes(autorange="reversed")

    col_b, col_a = st.columns([1, 1])
    with col_b:
        st.caption("Before mutation")
        st.plotly_chart(fig_before_mut, width="stretch")
    with col_a:
        st.caption("After mutation")
        st.plotly_chart(fig_after_mut, width="stretch")

except Exception as _e:
    st.warning(f"Could not render mutation demo: {_e}")

# --- Educational: show mating (crossover) example ---
st.markdown("## Mating example")
st.write("This shows two parent schedules and a child produced by the GA mating operator.")

try:
    assets_for_demo = list(assets) if isinstance(assets, (list, tuple)) and len(assets) > 0 else [f"A{i+1}" for i in range(5)]
    horizon_days = float(horizon) if 'horizon' in locals() else 60.0

    parent1 = [random.uniform(0.0, horizon_days) for _ in assets_for_demo]
    parent2 = [random.uniform(0.0, horizon_days) for _ in assets_for_demo]

    child1 = list(parent1)
    child2 = list(parent2)
    try:
        deap_tools.cxTwoPoint(child1, child2)
        child = child1
    except Exception:
        # Fallback: simple one-point crossover
        mid = len(parent1) // 2
        child = parent1[:mid] + parent2[mid:]

    rows_mate = []
    for a, p1, p2, ch in zip(assets_for_demo, parent1, parent2, child):
        rows_mate.append({
            "Asset": a,
            "Parent 1 (days)": float(p1),
            "Parent 2 (days)": float(p2),
            "Child (days)": float(ch),
        })

    df_mate = pd.DataFrame(rows_mate)
    # st.write("Parents and child start offsets:")
    # st.dataframe(df_mate)

    # Timelines: use real durations per asset
    fig_p1 = go.Figure()
    fig_p2 = go.Figure()
    fig_child = go.Figure()
    for _, r in df_mate.iterrows():
        asset_id = str(r["Asset"])
        dur = float(duration_days_by_asset.get(asset_id, DEFAULT_DURATION_DAYS)) if 'duration_days_by_asset' in locals() else DEFAULT_DURATION_DAYS
        s1 = int(math.floor(r["Parent 1 (days)"]))
        s2 = int(math.floor(r["Parent 2 (days)"]))
        sc = int(math.floor(r["Child (days)"]))

        fig_p1.add_trace(go.Bar(y=[asset_id], x=[dur], base=[s1], orientation="h", marker=dict(color="#2ca02c")))
        fig_p2.add_trace(go.Bar(y=[asset_id], x=[dur], base=[s2], orientation="h", marker=dict(color="#9467bd")))
        fig_child.add_trace(go.Bar(y=[asset_id], x=[dur], base=[sc], orientation="h", marker=dict(color="#d62728")))

    for fig in (fig_p1, fig_p2, fig_child):
        fig.update_layout(barmode="stack", height=200 + 28 * max(1, len(df_mate)), showlegend=False, margin={"l": 0, "r": 0, "t": 0, "b": 0})
        fig.update_yaxes(autorange="reversed")

    c1, o1, c2, o2, c3 = st.columns([1, 0.1, 1, 0.1, 1])
    with c1:
        st.caption("Parent 1")
        st.plotly_chart(fig_p1, width="stretch")
    with o1:
        st.title("+", text_alignment="center")
    with c2:
        st.caption("Parent 2")
        st.plotly_chart(fig_p2, width="stretch")
    with o2:
        st.title("→", text_alignment="center")
    with c3:
        st.caption("Child (after cxTwoPoint)")
        st.plotly_chart(fig_child, width="stretch")

except Exception as e:
    st.warning(f"Could not render mating demo: {e}")

# Weighted objectives (mocked)
# st.subheader("Weighted objectives (mocked)")
# # Some test fixtures of `best_result` may use the key `objectives` instead of
# # `weighted_objectives`. Fall back safely and show sensible defaults.
# if "weighted_objectives" in best_result and isinstance(best_result["weighted_objectives"], dict):
#     wo = best_result["weighted_objectives"]
# else:
#     obj = best_result.get("objectives", {})
#     wo = {
#         "condition_penalty": obj.get("avg_condition", "N/A"),
#         "travel_penalty": obj.get("avg_travel_time", "N/A"),
#         "cost_penalty": obj.get("total_cost", "N/A"),
#     }
# 
# st.write(f"Condition penalty: {wo.get('condition_penalty')}")
# st.write(f"Travel penalty: {wo.get('travel_penalty')}")
# st.write(f"Cost penalty: {wo.get('cost_penalty')}")

# Raw result at the bottom
st.divider()
with st.expander("Raw result JSON", expanded=False):
    st.json(best_result)

# If user clicked Run, execute src/main.py with provided arguments and show output
if 'run_now' in globals() and run_now:
    project_root = Path(__file__).resolve().parents[1]
    main_script = project_root / "src" / "main.py"
    cmd = [sys.executable, str(main_script),
           "--weights", f"{weights[0]}", f"{weights[1]}", f"{weights[2]}",
           "--horizon", str(float(horizon) * 24.0),  # slider is in days, backend expects hours
           "--population", str(int(population)),
           "--cx", str(float(cx)),
           "--mut", str(float(mut)),
           "--generations", str(int(generations)),
           "--backend", backend,
           "--group-size", str(int(group_size))]

    # Show command for debugging
    st.sidebar.write("Running command:")
    st.sidebar.code(" ".join(cmd))
    print(f"[DEBUG frontend] running subprocess cmd: {' '.join(cmd)}", flush=True)

    # Update status area and run subprocess
    status_area.info("Queued — starting subprocess...")
    with st.spinner("Running (this may take a while)..."):
        try:
            status_area.info("Running...")
            proc = subprocess.run(cmd, cwd=str(project_root), capture_output=True, text=True)
            rc = proc.returncode
            stdout = proc.stdout
            stderr = proc.stderr
        except Exception as e:
            rc = None
            stdout = ""
            stderr = str(e)
            status_area.error(f"Failed to start subprocess: {e}")
    print(f"[DEBUG frontend] subprocess finished rc={rc}", flush=True)
    print(f"[DEBUG frontend] subprocess stdout sample:\n{stdout[:800]}", flush=True)
    print(f"[DEBUG frontend] subprocess stderr sample:\n{stderr[:800]}", flush=True)

    # Finalize status
    if rc == 0:
        status_area.success(f"Finished successfully (rc={rc}) ✅")
    else:
        status_area.error(f"Finished with return code={rc}")

    with st.expander("Stdout", expanded=True):
        st.text(stdout)
    with st.expander("Stderr", expanded=False):
        st.text(stderr)
