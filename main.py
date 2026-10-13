from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from datetime import datetime
from pydantic import Field
from enum import Enum
import zipfile
import uuid
import io
import os

from src.main import run_pipeline

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
    version="0.2",
    contact={
        "name": "Nefeli Kousta",
        "email": "nefelikousta@gmail.com",
    },
    license_info={
        "name": "BSD License",
    }
)


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
                jobs_db[job_id]["error"] = "No optimal solution found"
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
    
    if job["status"] == "completed":
        return {
            "job_id": job["id"],
            "status": job["status"],
            "created_at": job["created_at"],
            "selected_deposits_gdf": job["selected_deposits_gdf"],
            "roads_with_clusters": job["roads_with_clusters"],
            "completed_at": job["completed_at"],
        }
    else:
        return {
            "job_id": job["id"],
            "status": job["status"],
            "created_at": job["created_at"]
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