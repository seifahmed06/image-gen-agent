# agent.py
import os
import uuid
import asyncio
from typing import Optional, List, Dict, Any
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException, Header, BackgroundTasks
from datetime import datetime
import httpx
import sqlite3
import logging
from enum import Enum

from dotenv import load_dotenv
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# CONFIG - set to your MCP server endpoint
MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://localhost:8080/generate_image")
ESTIMATED_COST_PER_IMAGE_USD = float(os.environ.get("COST_PER_IMAGE", "0.05"))
APPROVER_SECRET = os.environ.get("APPROVER_SECRET", "secret-token")
MAX_IMAGES_PER_REQUEST = int(os.environ.get("MAX_IMAGES_PER_REQUEST", "10"))
REQUEST_TIMEOUT = float(os.environ.get("REQUEST_TIMEOUT", "60.0"))

app = FastAPI(
    title="Image-Gen Agent with Cost Approval",
    description="AI Agent for image generation with cost control and approval workflow",
    version="2.0.0"
)

DB = "jobs.db"

# Database models
class JobStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"

# Pydantic models
class GenerateRequest(BaseModel):
    prompt: str
    n_images: int = 1
    user_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

class JobResponse(BaseModel):
    job_id: str
    status: str
    message: Optional[str] = None
    estimated_cost: Optional[float] = None
    approval_required: bool = False

class ApprovalRequest(BaseModel):
    approved: bool = True
    reason: Optional[str] = None

class JobDetailResponse(BaseModel):
    job_id: str
    prompt: str
    n_images: int
    status: str
    created_at: str
    completed_at: Optional[str] = None
    result: Optional[str] = None
    estimated_cost: float
    user_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

# Database helper functions
def init_db():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY,
        prompt TEXT NOT NULL,
        n_images INTEGER NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        completed_at TEXT,
        result TEXT,
        user_id TEXT,
        metadata TEXT,
        estimated_cost REAL,
        approved_by TEXT,
        approval_reason TEXT
    )
    """)
    conn.commit()
    conn.close()

init_db()

def store_job(job_id: str, prompt: str, n_images: int, status: JobStatus, 
              user_id: Optional[str] = None, metadata: Optional[Dict] = None):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    estimated_cost = n_images * ESTIMATED_COST_PER_IMAGE_USD
    metadata_str = str(metadata) if metadata else None
    
    c.execute("""
        INSERT INTO jobs (id, prompt, n_images, status, created_at, user_id, metadata, estimated_cost)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (job_id, prompt, n_images, status.value, datetime.utcnow().isoformat(), 
          user_id, metadata_str, estimated_cost))
    conn.commit()
    conn.close()

def update_job_status(job_id: str, status: JobStatus, result: Optional[str] = None,
                     approved_by: Optional[str] = None, approval_reason: Optional[str] = None):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    
    if status == JobStatus.COMPLETED or status == JobStatus.FAILED:
        completed_at = datetime.utcnow().isoformat()
        if result:
            c.execute("""
                UPDATE jobs SET status=?, completed_at=?, result=?, approved_by=?, approval_reason=?
                WHERE id=?
            """, (status.value, completed_at, result, approved_by, approval_reason, job_id))
        else:
            c.execute("""
                UPDATE jobs SET status=?, completed_at=?, approved_by=?, approval_reason=?
                WHERE id=?
            """, (status.value, completed_at, approved_by, approval_reason, job_id))
    else:
        if approved_by or approval_reason:
            c.execute("""
                UPDATE jobs SET status=?, approved_by=?, approval_reason=?
                WHERE id=?
            """, (status.value, approved_by, approval_reason, job_id))
        else:
            c.execute("UPDATE jobs SET status=? WHERE id=?", (status.value, job_id))
    
    conn.commit()
    conn.close()

def get_job(job_id: str) -> Optional[Dict]:
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""
        SELECT id, prompt, n_images, status, created_at, completed_at, 
               result, user_id, metadata, estimated_cost
        FROM jobs WHERE id=?
    """, (job_id,))
    row = c.fetchone()
    conn.close()
    
    if not row:
        return None
    
    keys = ["id", "prompt", "n_images", "status", "created_at", "completed_at", 
            "result", "user_id", "metadata", "estimated_cost"]
    result = dict(zip(keys, row))
    
    # Parse metadata back to dict
    if result["metadata"]:
        try:
            result["metadata"] = eval(result["metadata"])
        except:
            result["metadata"] = {"raw": result["metadata"]}
    
    return result

# MCP Server communication
async def call_mcp_generate(prompt: str, n_images: int = 1) -> List[Any]:
    """Call MCP server for image generation"""
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            # Different MCP servers might have different request formats
            # Try common formats for popular image generation services
            
            # Format 1: Simple prompt with count
            payload = {"prompt": prompt, "n_images": n_images}
            
            # Format 2: DALL-E style
            # payload = {"prompt": prompt, "n": n_images, "size": "1024x1024"}
            
            # Format 3: Stable Diffusion style
            # payload = {"prompt": prompt, "num_images": n_images, "steps": 20}
            
            logger.info(f"Calling MCP server at {MCP_SERVER_URL} with payload: {payload}")
            
            response = await client.post(MCP_SERVER_URL, json=payload)
            response.raise_for_status()
            
            data = response.json()
            
            # Handle different response formats
            if isinstance(data, list):
                return data
            elif isinstance(data, dict) and "images" in data:
                return data["images"]
            elif isinstance(data, dict) and "data" in data:
                return data["data"]
            else:
                return [data]
                
    except httpx.TimeoutException:
        logger.error(f"MCP server timeout after {REQUEST_TIMEOUT} seconds")
        raise HTTPException(status_code=504, detail="MCP server timeout")
    except httpx.HTTPError as e:
        logger.error(f"MCP server error: {str(e)}")
        raise HTTPException(status_code=502, detail=f"MCP server error: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error calling MCP server: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

# Background task for async image generation
async def generate_images_async(job_id: str, prompt: str, n_images: int):
    """Generate images asynchronously in background"""
    try:
        logger.info(f"Starting async generation for job {job_id}, {n_images} images")
        
        # Generate images sequentially to avoid overloading
        results = []
        for i in range(n_images):
            logger.info(f"Generating image {i+1}/{n_images} for job {job_id}")
            try:
                image_result = await call_mcp_generate(prompt, 1)
                results.extend(image_result)
            except Exception as e:
                logger.error(f"Failed to generate image {i+1} for job {job_id}: {str(e)}")
                # Continue with remaining images even if one fails
                results.append(f"Error: {str(e)}")
        
        # Update job status
        update_job_status(job_id, JobStatus.COMPLETED, result=str(results))
        logger.info(f"Completed async generation for job {job_id}")
        
    except Exception as e:
        logger.error(f"Async generation failed for job {job_id}: {str(e)}")
        update_job_status(job_id, JobStatus.FAILED, result=str(e))

# API Endpoints
@app.post("/generate", response_model=JobResponse)
async def generate_image(
    req: GenerateRequest, 
    background_tasks: BackgroundTasks,
    x_user_id: Optional[str] = Header(None)
):
    """Generate images with automatic approval for single images"""
    if req.n_images < 1:
        raise HTTPException(status_code=400, detail="n_images must be >= 1")
    
    if req.n_images > MAX_IMAGES_PER_REQUEST:
        raise HTTPException(
            status_code=400, 
            detail=f"Maximum {MAX_IMAGES_PER_REQUEST} images per request"
        )
    
    job_id = str(uuid.uuid4())
    user_id = req.user_id or x_user_id or "anonymous"
    estimated_cost = req.n_images * ESTIMATED_COST_PER_IMAGE_USD
    
    # Single image: auto-approve and generate immediately
    if req.n_images == 1:
        try:
            logger.info(f"Auto-approving single image generation for job {job_id}")
            store_job(job_id, req.prompt, req.n_images, JobStatus.APPROVED, user_id, req.metadata)
            
            # Generate immediately
            results = await call_mcp_generate(req.prompt, req.n_images)
            update_job_status(job_id, JobStatus.COMPLETED, result=str(results))
            
            return JobResponse(
                job_id=job_id,
                status="COMPLETED",
                message="Image generated successfully",
                estimated_cost=estimated_cost,
                approval_required=False
            )
            
        except Exception as e:
            store_job(job_id, req.prompt, req.n_images, JobStatus.FAILED, user_id, req.metadata)
            raise HTTPException(status_code=500, detail=f"Generation failed: {str(e)}")
    
    else:
        # Bulk generation: require approval
        store_job(job_id, req.prompt, req.n_images, JobStatus.PENDING, user_id, req.metadata)
        
        approval_msg = (
            f"Bulk generation job {job_id} requires approval. "
            f"Prompt: {req.prompt[:100]}{'...' if len(req.prompt) > 100 else ''}. "
            f"Images: {req.n_images}. Estimated cost: ${estimated_cost:.2f}. "
            f"Use the approval endpoint to approve or reject this job."
        )
        
        logger.info(f"Created pending job {job_id} for {req.n_images} images")
        
        return JobResponse(
            job_id=job_id,
            status="PENDING",
            message=approval_msg,
            estimated_cost=estimated_cost,
            approval_required=True
        )

@app.post("/approve/{job_id}")
async def approve_job(
    job_id: str,
    approval_req: ApprovalRequest,
    x_approver: Optional[str] = Header(None)
):
    """Approve or reject a pending job"""
    if x_approver != APPROVER_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized - invalid approver token")
    
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if job["status"] != JobStatus.PENDING:
        raise HTTPException(status_code=400, detail=f"Job is not pending (current status: {job['status']})")
    
    if approval_req.approved:
        # Approve the job
        update_job_status(
            job_id, 
            JobStatus.APPROVED, 
            approved_by="approver_api",
            approval_reason=approval_req.reason or "Approved via API"
        )
        
        # Start async generation
        from fastapi import BackgroundTasks
        background_tasks = BackgroundTasks()
        background_tasks.add_task(
            generate_images_async, 
            job_id, 
            job["prompt"], 
            job["n_images"]
        )
        
        logger.info(f"Approved job {job_id}, starting async generation")
        
        return {
            "job_id": job_id,
            "status": "APPROVED",
            "message": "Job approved and generation started",
            "estimated_cost": job["estimated_cost"]
        }
    else:
        # Reject the job
        update_job_status(
            job_id,
            JobStatus.REJECTED,
            approved_by="approver_api", 
            approval_reason=approval_req.reason or "Rejected via API"
        )
        
        logger.info(f"Rejected job {job_id}")
        
        return {
            "job_id": job_id,
            "status": "REJECTED",
            "message": "Job rejected",
            "reason": approval_req.reason
        }

@app.get("/jobs/{job_id}", response_model=JobDetailResponse)
def get_job_status(job_id: str):
    """Get job status and details"""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    return JobDetailResponse(**job)

@app.get("/jobs")
def list_jobs(
    status: Optional[JobStatus] = None,
    limit: int = 100,
    offset: int = 0
):
    """List jobs with optional filtering"""
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    
    query = "SELECT id, prompt, n_images, status, created_at, estimated_cost FROM jobs"
    params = []
    
    if status:
        query += " WHERE status = ?"
        params.append(status.value)
    
    query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    
    c.execute(query, params)
    rows = c.fetchall()
    conn.close()
    
    keys = ["id", "prompt", "n_images", "status", "created_at", "estimated_cost"]
    return [dict(zip(keys, row)) for row in rows]

@app.get("/stats")
def get_stats():
    """Get system statistics"""
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    
    # Total jobs
    c.execute("SELECT COUNT(*) FROM jobs")
    total_jobs = c.fetchone()[0]
    
    # Jobs by status
    c.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status")
    status_counts = dict(c.fetchall())
    
    # Total cost
    c.execute("SELECT SUM(estimated_cost) FROM jobs WHERE status = 'COMPLETED'")
    total_cost = c.fetchone()[0] or 0
    
    # Recent activity
    c.execute("""
        SELECT COUNT(*) FROM jobs 
        WHERE created_at > datetime('now', '-1 day')
    """)
    last_24h = c.fetchone()[0]
    
    conn.close()
    
    return {
        "total_jobs": total_jobs,
        "jobs_by_status": status_counts,
        "total_cost_estimated": round(total_cost, 2),
        "jobs_last_24h": last_24h,
        "cost_per_image": ESTIMATED_COST_PER_IMAGE_USD
    }

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    # Test MCP server connectivity
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Simple health check - adjust based on your MCP server's health endpoint
            response = await client.get(MCP_SERVER_URL.replace("/generate_image", "/health"))
            mcp_healthy = response.status_code == 200
    except:
        mcp_healthy = False
    
    # Test database connectivity
    try:
        conn = sqlite3.connect(DB)
        conn.close()
        db_healthy = True
    except:
        db_healthy = False
    
    return {
        "status": "healthy" if (mcp_healthy and db_healthy) else "degraded",
        "mcp_server": "connected" if mcp_healthy else "disconnected",
        "database": "connected" if db_healthy else "disconnected",
        "timestamp": datetime.utcnow().isoformat()
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=8000,
        log_level="info"
    )