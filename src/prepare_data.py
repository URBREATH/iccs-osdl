import argparse
import os

import geopandas as gpd
import pandas as pd
import numpy as np
import osmnx as ox
import networkx as nx

from city_config import get_city_config

parser = argparse.ArgumentParser(description="Preprocess road/deposit data for a city.")
parser.add_argument("--city", default="tallinn", help="City slug, e.g. 'tallinn' or 'kajaani'")
args = parser.parse_args()

city = get_city_config(args.city)

OUTPUT_DIR = city.prepared_dir
os.makedirs(OUTPUT_DIR, exist_ok=True)

print(f"Loading data for {city.display_name}...")

roads = gpd.read_file(city.roads_path)
deposits = pd.concat(
    [gpd.read_file(p) for p in city.deposit_paths], ignore_index=True
)

# Compute per-street area fractions (V-independent); multiply by V at runtime
total_area = roads["area_sqm"].sum()
road_area_fractions = (roads["area_sqm"] / total_area).to_numpy()

capacities = deposits["snow_capacity_m3"].to_numpy()

print(f"Projecting for centroid extraction... (EPSG:{city.metric_crs})")
# Centroids are computed in a metric CRS, then converted back to lon/lat
# because the OSM graph from graph_from_place is unprojected (EPSG:4326)
road_centroids = roads.to_crs(city.metric_crs).geometry.centroid.to_crs(4326)
depot_centroids = deposits.to_crs(city.metric_crs).geometry.centroid.to_crs(4326)

road_lons = road_centroids.x.values
road_lats = road_centroids.y.values

depot_lons = depot_centroids.x.values
depot_lats = depot_centroids.y.values

print("Loading OSM graph...")
G = ox.graph_from_place(city.osm_place, network_type="drive")
ox.distance.add_edge_lengths(G)

print("Finding nearest nodes...")
road_nodes = ox.nearest_nodes(G, road_lons, road_lats)
depot_nodes = ox.nearest_nodes(G, depot_lons, depot_lats)

print("Computing distance matrix...")
n_streets = len(roads)
n_depots = len(deposits)

dist_matrix = np.zeros((n_streets, n_depots))

for j, depot_node in enumerate(depot_nodes):
    dists = nx.shortest_path_length(G, depot_node, weight="length")
    for i, road_node in enumerate(road_nodes):
        dist_matrix[i, j] = dists.get(road_node, 1e9)

print("Saving outputs...")
roads.to_parquet(os.path.join(OUTPUT_DIR, "roads.parquet"))
deposits.to_parquet(os.path.join(OUTPUT_DIR, "deposits.parquet"))

np.save(os.path.join(OUTPUT_DIR, "road_area_fractions.npy"), road_area_fractions)
np.save(os.path.join(OUTPUT_DIR, "capacities.npy"), capacities)
np.save(os.path.join(OUTPUT_DIR, "dist_matrix.npy"), dist_matrix)
np.save(os.path.join(OUTPUT_DIR, "road_nodes.npy"), road_nodes)
np.save(os.path.join(OUTPUT_DIR, "depot_nodes.npy"), depot_nodes)

print(f"Done! Outputs written to {OUTPUT_DIR}")
