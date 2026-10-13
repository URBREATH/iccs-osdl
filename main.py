from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import StreamingResponse, FileResponse, Response
from pydantic import BaseModel
from datetime import datetime
from pydantic import Field
from functools import lru_cache
from enum import Enum
import geopandas as gpd
import numpy as np
import shapely
import zipfile
import uuid
import io
import os

from src.main import run_pipeline
from src.city_config import get_city_config

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

class SolverType(str, Enum):
    cpsat = "cpsat"
    cbc = "cbc"


class CityName(str, Enum):
    tallinn = "tallinn"
    kajaani = "kajaani"


class Job(BaseModel):

    tsv: float
    p: int
    solver: SolverType
    city_name: CityName
    

app = FastAPI(
    title="UrBreath - OptimalSnow - API",
    description="",
    version="0.32",
    contact={
        "name": "Nefeli Kousta",
        "email": "nefelikousta@gmail.com",
    },
    license_info={
        "name": "BSD License",
    }
)

# The result GeoJSON for Tallinn is several MB, so compress responses
app.add_middleware(GZipMiddleware, minimum_size=1000)


jobs_db = {}


def run_solver_task(job_id: str):
    try:
            
        if job_id in jobs_db:
            res = run_pipeline(
                job_id,
                jobs_db[job_id]["tsv"],
                jobs_db[job_id]["p"],
                jobs_db[job_id]["solver"],
                jobs_db[job_id]["city_name"]
            )

            if res == None:
                jobs_db[job_id]["status"] = "failed"
                jobs_db[job_id]["error"] = (
                    "No feasible solution found: try a lower snow volume or more sites"
                )
            else:
                print(res)
                deposits, roads = res
                jobs_db[job_id]["status"] = "completed"
                jobs_db[job_id]["selected_deposits_gdf"] = deposits
                jobs_db[job_id]["roads_with_clusters"] = roads
            jobs_db[job_id]["completed_at"] = datetime.now()
    except Exception as e:
        if job_id in jobs_db:
            jobs_db[job_id]["status"] = "failed"
            jobs_db[job_id]["error"] = str(e)
            jobs_db[job_id]["completed_at"] = datetime.now()


@app.post("/jobs/", status_code=201)
async def create_job(job_data: Job, background_tasks: BackgroundTasks):

    # id: uuid.UUID = Field(default_factory=uuid.uuid4)
    
    job_dict = job_data.model_dump()
    job_dict["id"] = uuid.uuid4()

    job_id_str = str(job_dict["id"])
    
    job_dict["created_at"] = datetime.now()
    job_dict["status"] = "pending"
    
    jobs_db[job_id_str] = job_dict
    
    background_tasks.add_task(
        run_solver_task, 
        job_id_str
    )

    return job_dict


@app.get("/jobs/{job_id}/status")
async def get_job_status(job_id: uuid.UUID):
    
    str_id = str(job_id)
    
    if str_id not in jobs_db:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = jobs_db[str_id]
    
    params = {k: job[k] for k in ("city_name", "tsv", "p", "solver")}

    if job["status"] == "completed":
        return {
            "job_id": job["id"],
            "status": job["status"],
            "created_at": job["created_at"],
            "selected_deposits_gdf": job["selected_deposits_gdf"],
            "roads_with_clusters": job["roads_with_clusters"],
            "completed_at": job["completed_at"],
            **params,
        }
    elif job["status"] == "failed":
        return {
            "job_id": job["id"],
            "status": job["status"],
            "created_at": job["created_at"],
            "error": job.get("error"),
            "completed_at": job.get("completed_at"),
            **params,
        }
    else:
        return {
            "job_id": job["id"],
            "status": job["status"],
            "created_at": job["created_at"],
            **params,
        }



@app.get("/jobs/{job_id}/download-results")
async def download_all_results(job_id: str):
    if job_id not in jobs_db:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = jobs_db[job_id]
    path1 = job.get("selected_deposits_gdf")
    path2 = job.get("roads_with_clusters")

    if not path1 or not path2 or not (os.path.exists(path1) and os.path.exists(path2)):
        raise HTTPException(status_code=404, detail="One or more result files are missing")


    zip_buffer = io.BytesIO()
    
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        
        zip_file.write(path1, arcname="selected_deposits.geojson")
        zip_file.write(path2, arcname="roads_with_clusters.geojson")

    zip_buffer.seek(0)

    return StreamingResponse(
        zip_buffer, 
        media_type="application/x-zip-compressed",
        headers={"Content-Disposition": f"attachment; filename=results_{job_id}.zip"}
    )


# ---------------------------------------------------------------------------
# Web map: page + map-ready (EPSG:4326) GeoJSON for the frontend in static/
# ---------------------------------------------------------------------------

def _deposit_labels(deposits):
    for col in ("name", "site_id"):
        if col in deposits.columns:
            return deposits[col].fillna("").astype(str)
    return deposits.index.map(lambda i: f"Deposit {i}").to_series(index=deposits.index)


def _to_web_geojson(gdf, columns, metric_crs=None, as_points=False):
    gdf = gdf[[c for c in columns if c in gdf.columns] + ["geometry"]].copy()
    if as_points:
        # centroids are taken in the city's metric CRS for accuracy
        gdf["geometry"] = gdf.geometry.to_crs(metric_crs).centroid
    gdf = gdf.to_crs(4326)
    # ~10 cm precision is plenty for display and keeps the payload small
    gdf["geometry"] = shapely.transform(gdf.geometry.values, lambda xy: xy.round(6))
    return gdf.to_json(drop_id=True)


@lru_cache(maxsize=None)
def _city_summary(city_name: str):
    city = get_city_config(city_name)
    try:
        roads = gpd.read_parquet(os.path.join(city.prepared_dir, "roads.parquet"))
        capacities = np.load(os.path.join(city.prepared_dir, "capacities.npy"))
    except FileNotFoundError:
        return {"name": city.name, "display_name": city.display_name, "available": False}

    west, south, east, north = roads.to_crs(4326).total_bounds
    largest_first = np.sort(capacities)[::-1]
    return {
        "name": city.name,
        "display_name": city.display_name,
        "available": True,
        "bounds": [[south, west], [north, east]],
        "n_deposits": int(len(capacities)),
        # max_capacity_by_p[p-1] = combined capacity of the p largest deposits,
        # an upper bound on the snow volume a job with that p can handle
        "max_capacity_by_p": np.round(np.cumsum(largest_first), 1).tolist(),
    }


@lru_cache(maxsize=None)
def _city_deposits_geojson(city_name: str):
    city = get_city_config(city_name)
    deposits = gpd.read_parquet(os.path.join(city.prepared_dir, "deposits.parquet"))
    deposits["deposit_index"] = np.arange(len(deposits))
    deposits["label"] = _deposit_labels(deposits)
    return _to_web_geojson(
        deposits, ["deposit_index", "label", "snow_capacity_m3"],
        metric_crs=city.metric_crs, as_points=True,
    )


@lru_cache(maxsize=32)
def _job_deposits_geojson(path: str, city_name: str):
    deposits = gpd.read_file(path)
    deposits["label"] = _deposit_labels(deposits)
    return _to_web_geojson(
        deposits,
        ["deposit_index", "label", "snow_capacity_m3", "assigned_snow_m3", "n_streets"],
        metric_crs=get_city_config(city_name).metric_crs, as_points=True,
    )


@lru_cache(maxsize=32)
def _job_streets_geojson(path: str):
    streets = gpd.read_file(path)
    return _to_web_geojson(streets, ["assigned_deposit_index", "snow_m3", "name"])


def _completed_job(job_id: str):
    job = jobs_db.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "completed":
        raise HTTPException(status_code=409, detail=f"Job is {job['status']}")
    return job


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/cities")
def list_cities():
    return [_city_summary(c.value) for c in CityName]


@app.get("/cities/{city_name}/deposits")
def get_city_deposits(city_name: CityName):
    if not _city_summary(city_name.value)["available"]:
        raise HTTPException(status_code=404, detail="No prepared data for this city")
    return Response(_city_deposits_geojson(city_name.value), media_type="application/geo+json")


@app.get("/jobs/{job_id}/deposits")
def get_job_deposits(job_id: str):
    job = _completed_job(job_id)
    geojson = _job_deposits_geojson(job["selected_deposits_gdf"], job["city_name"].value)
    return Response(geojson, media_type="application/geo+json")


@app.get("/jobs/{job_id}/streets")
def get_job_streets(job_id: str):
    job = _completed_job(job_id)
    return Response(_job_streets_geojson(job["roads_with_clusters"]), media_type="application/geo+json")
