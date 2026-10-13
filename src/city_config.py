"""
city_config.py
===============
Per-city configuration for the OptimalSnow pipeline (prepare_data.py,
main.py, and the experiment/visualization scripts).

Adding a new city means adding one CityConfig entry below — no other
file should need city-specific hardcoding after that.
"""

import os
from dataclasses import dataclass

# Repo root (one level above src/), so paths work regardless of the cwd
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT_DIR, "data")
PREPARED_DIR = os.path.join(ROOT_DIR, "data_prepared")


@dataclass
class CityConfig:
    name: str                  # slug used in paths, e.g. "tallinn"
    display_name: str          # human-readable, e.g. "Tallinn"
    roads_path: str            # road segments, must yield an area_sqm column
    deposit_paths: list        # one or more deposit candidate files (concatenated)
    osm_place: str             # place string passed to osmnx.graph_from_place
    metric_crs: int            # projected EPSG code for centroid/clustering distances
    n_clusters: int = 300      # spatial clusters for KMeans street aggregation

    @property
    def prepared_dir(self) -> str:
        """Where prepare_data.py writes and run_pipeline reads this city's data."""
        return os.path.join(PREPARED_DIR, self.name)


CITY_CONFIGS = {
    "tallinn": CityConfig(
        name="tallinn",
        display_name="Tallinn",
        roads_path=os.path.join(DATA_DIR, "tallinn", "tallinn_roads.geojson"),
        deposit_paths=[
            os.path.join(DATA_DIR, "tallinn", "snow_capacity_municipality.geojson"),
            os.path.join(DATA_DIR, "tallinn", "snow_capacity_government.geojson"),
        ],
        osm_place="Tallinn, Estonia",
        metric_crs=3301,   # Estonian National Grid
        n_clusters=300,
    ),
    "kajaani": CityConfig(
        name="kajaani",
        display_name="Kajaani",
        # width/area-enriched network — see README for how this was derived
        # from the raw city-provided street network
        roads_path=os.path.join(DATA_DIR, "kajaani", "kajaani_roads_width_hybrid.geojson"),
        deposit_paths=[
            os.path.join(DATA_DIR, "kajaani", "kajaani_deposits.geojson"),
        ],
        osm_place="Kajaani, Finland",
        metric_crs=3067,   # ETRS89 / TM35FIN
        n_clusters=200,    # ~67% of Tallinn's road segment count, scaled accordingly
    ),
}


def get_city_config(city: str) -> CityConfig:
    try:
        return CITY_CONFIGS[city]
    except KeyError:
        raise ValueError(
            f"Unknown city '{city}'. Available cities: {sorted(CITY_CONFIGS)}"
        )
