from fastapi import FastAPI, Request, Form, File, UploadFile, HTTPException, BackgroundTasks, Depends
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from sqlmodel import SQLModel, Field, create_engine, Session, select, Relationship
from typing import Optional
from datetime import datetime
from enum import Enum
import uvicorn
import os
import shutil
import asyncio
import time
import tempfile
import uuid
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Job status enumeration
class JobStatus(str, Enum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"

# SQLModel Job model
class Job(SQLModel, table=True):
    __table_args__ = {'extend_existing': True}
    id: Optional[int] = Field(default=None, primary_key=True)
    filename: str = Field(index=True)
    file_path: Optional[str] = Field(default=None)  # Path to uploaded audio file
    file_size: Optional[int] = Field(default=None)  # File size in bytes
    status: JobStatus = Field(default=JobStatus.pending)
    transcript: Optional[str] = Field(default=None)  # Deprecated - use result instead
    result: Optional[str] = Field(default=None)  # Deprecated - use transcript_file_path instead
    transcript_file_path: Optional[str] = Field(default=None)  # Path to transcript .md file
    user_id: Optional[uuid.UUID] = Field(default=None, foreign_key="user.id", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = Field(default=None)

# File upload constants
UPLOAD_DIR = "./uploads"
TRANSCRIPT_DIR = "./transcripts"
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB in bytes
ALLOWED_EXTENSIONS = {".wav", ".mp3"}

# Ensure directories exist
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(TRANSCRIPT_DIR, exist_ok=True)

# Database setup
DATABASE_URL = "sqlite:///./test.db"
engine = create_engine(DATABASE_URL)

def create_db_and_tables():
    try:
        SQLModel.metadata.create_all(engine)
    except Exception as e:
        # If there are issues with existing tables/indexes, drop and recreate
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)

def get_session():
    with Session(engine) as session:
        yield session

def validate_audio_file(file: UploadFile) -> str:
    """Validate uploaded audio file and return error message if invalid."""
    if not file.filename:
        return "No file selected"
    
    # Check file extension
    file_ext = os.path.splitext(file.filename)[1].lower()
    if file_ext not in ALLOWED_EXTENSIONS:
        return f"Invalid file type. Only {', '.join(ALLOWED_EXTENSIONS)} files are allowed"
    
    # Check file size (we need to read the file to check size)
    file.file.seek(0, 2)  # Seek to end of file
    file_size = file.file.tell()
    file.file.seek(0)  # Reset to beginning
    
    if file_size > MAX_FILE_SIZE:
        return f"File too large. Maximum size is {MAX_FILE_SIZE // (1024*1024)}MB"
    
    return ""  # No error

def save_uploaded_file(file: UploadFile, job_id: int) -> tuple[str, int]:
    """Save uploaded file and return (file_path, file_size)."""
    file_ext = os.path.splitext(file.filename)[1].lower()
    safe_filename = f"job_{job_id}_{file.filename}"
    file_path = os.path.join(UPLOAD_DIR, safe_filename)
    
    # Save file
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    # Get file size
    file_size = os.path.getsize(file_path)
    
    return file_path, file_size

def save_transcript_to_file(job_id: int, transcript_text: str) -> str:
    """Save transcript text to .md file and return file path."""
    filename = f"transcript_job_{job_id}.md"
    file_path = os.path.join(TRANSCRIPT_DIR, filename)
    
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(transcript_text)
    
    return file_path

def load_transcript_from_file(file_path: str) -> str:
    """Load transcript text from .md file."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""

async def process_transcription(job_id: int):
    """Background task to simulate transcription API processing."""
    # Wait 5 seconds to simulate API processing time
    await asyncio.sleep(5)
    
    # Simulate transcription result
    dummy_transcript = """
    Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat. Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla pariatur. Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia deserunt mollit anim id est laborum.
    """.strip()
    
    # Save transcript to file and update job status in database
    with Session(engine) as session:
        job = session.get(Job, job_id)
        if job:
            transcript_file_path = save_transcript_to_file(job_id, dummy_transcript)
            job.status = JobStatus.completed
            job.transcript_file_path = transcript_file_path
            job.completed_at = datetime.utcnow()
            session.commit()

app = FastAPI(title="Better Transcripts", description="High-quality formatted transcripts")

# Import authentication after app creation to avoid circular imports
from auth import auth_backend, fastapi_users, current_active_user, User, UserRead, UserCreate

@app.on_event("startup")
def on_startup():
    create_db_and_tables()

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Include authentication routes
app.include_router(
    fastapi_users.get_auth_router(auth_backend), prefix="/auth/jwt", tags=["auth"]
)
app.include_router(
    fastapi_users.get_register_router(UserRead, UserCreate), prefix="/auth", tags=["auth"]
)
app.include_router(
    fastapi_users.get_reset_password_router(), prefix="/auth", tags=["auth"]
)
app.include_router(
    fastapi_users.get_verify_router(UserRead), prefix="/auth", tags=["auth"]
)

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.get("/register", response_class=HTMLResponse) 
async def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})

@app.get("/check-auth")
async def check_auth(request: Request):
    logger.info("Checking authentication status")
    logger.info(f"Cookies: {request.cookies}")
    
    # Check if user has auth cookie
    auth_cookie = request.cookies.get("fastapiusersauth")
    logger.info(f"Auth cookie present: {auth_cookie is not None}")
    
    if auth_cookie:
        try:
            # Try to get user using the dependency
            # We'll create a simple route that tries to access a protected resource
            return HTMLResponse(f"""
                <div hx-get="/auth-test" hx-trigger="load"></div>
            """)
        except Exception as e:
            logger.error(f"Error with auth cookie: {e}")
    
    logger.info("User is not authenticated, showing login prompt")
    # User is not authenticated, show login prompt
    return HTMLResponse("""
        <div class="max-w-md mx-auto bg-white rounded-lg shadow-md p-6 text-center">
            <h2 class="text-2xl font-semibold mb-4 text-gray-700">Welcome to Better Transcripts</h2>
            <p class="text-gray-600 mb-6">Please log in to access your transcription jobs.</p>
            <div class="space-y-3">
                <a href="/login" class="block w-full bg-blue-500 hover:bg-blue-600 text-white font-bold py-2 px-4 rounded transition duration-300">
                    Login
                </a>
                <a href="/register" class="block w-full bg-green-500 hover:bg-green-600 text-white font-bold py-2 px-4 rounded transition duration-300">
                    Register
                </a>
            </div>
        </div>
    """)

@app.get("/auth-test")
async def auth_test(user: User = Depends(current_active_user)):
    logger.info(f"Auth test successful for user: {user.email}")
    return HTMLResponse(f"""
        <div hx-get="/jobs/list/view" hx-trigger="load"></div>
        <script>
            // Load auth status
            htmx.ajax('GET', '/auth-status', {{target: '#auth-status'}});
        </script>
    """)

@app.get("/auth-status")
async def auth_status(user: User = Depends(current_active_user)):
    return HTMLResponse(f"""
        <div class="flex items-center gap-3">
            <span class="text-sm text-gray-600">Welcome, {user.name}</span>
            <button onclick="logout()" class="bg-red-500 hover:bg-red-600 text-white text-sm px-3 py-1 rounded transition duration-300">
                Logout
            </button>
        </div>
        <script>
            async function logout() {{
                try {{
                    await fetch('/auth/jwt/logout', {{method: 'POST'}});
                    window.location.reload();
                }} catch (error) {{
                    console.error('Logout failed:', error);
                }}
            }}
        </script>
    """)

@app.post("/jobs/add")
async def add_job(file: UploadFile = File(...), background_tasks: BackgroundTasks = BackgroundTasks(), user: User = Depends(current_active_user)):
    # Validate file
    error_msg = validate_audio_file(file)
    if error_msg:
        return HTMLResponse(f"<div class='text-red-600 font-semibold'>❌ {error_msg}</div>")
    
    with Session(engine) as session:
        # Create job first to get ID
        db_job = Job(filename=file.filename, status=JobStatus.processing, user_id=user.id)
        session.add(db_job)
        session.commit()
        session.refresh(db_job)
        
        try:
            # Save file with job ID
            file_path, file_size = save_uploaded_file(file, db_job.id)
            
            # Update job with file info
            db_job.file_path = file_path
            db_job.file_size = file_size
            session.commit()
            
            # Start background transcription processing
            background_tasks.add_task(process_transcription, db_job.id)
            
            return HTMLResponse(f"<div class='text-blue-600 font-semibold'>🔄 Processing job: {file.filename} ({file_size // 1024} KB)</div>")
            
        except Exception as e:
            # If file save fails, delete the job
            session.delete(db_job)
            session.commit()
            return HTMLResponse(f"<div class='text-red-600 font-semibold'>❌ Failed to save file: {str(e)}</div>")

@app.get("/jobs/list")
async def list_jobs(user: User = Depends(current_active_user)):
    with Session(engine) as session:
        jobs = session.exec(select(Job).where(Job.user_id == user.id).order_by(Job.created_at.desc())).all()
        if not jobs:
            return HTMLResponse("<div class='text-gray-500'>No jobs found.</div>")
        
        job_items = []
        for job in jobs:
            status_color = {
                "pending": "text-yellow-600",
                "processing": "text-blue-600", 
                "completed": "text-green-600",
                "failed": "text-red-600"
            }.get(job.status, "text-gray-600")
            
            file_info = f" • {job.file_size // 1024:,} KB" if job.file_size else ""
            job_items.append(f"<li class='py-2 border-b border-gray-200 last:border-b-0 hover:bg-gray-50 cursor-pointer' hx-get='/jobs/{job.id}' hx-target='#main-content' hx-swap='innerHTML'><div class='flex justify-between items-start'><div><div class='font-medium'>{job.filename}</div><div class='text-sm text-gray-500'>Created: {job.created_at.strftime('%Y-%m-%d %H:%M')}{file_info}</div></div><span class='px-2 py-1 text-xs rounded-full bg-gray-100 {status_color}'>{job.status}</span></div></li>")
        
        return HTMLResponse(f"<ul class='divide-y divide-gray-200'>{''.join(job_items)}</ul>")

@app.get("/jobs/{job_id}")
async def get_job_detail(job_id: int, user: User = Depends(current_active_user)):
    with Session(engine) as session:
        job = session.get(Job, job_id)
        if not job or job.user_id != user.id:
            return HTMLResponse("<div class='text-red-500'>Job not found.</div>")
        
        status_color = {
            "pending": "bg-yellow-100 text-yellow-800",
            "processing": "bg-blue-100 text-blue-800", 
            "completed": "bg-green-100 text-green-800",
            "failed": "bg-red-100 text-red-800"
        }.get(job.status, "bg-gray-100 text-gray-800")
        
        transcript_section = ""
        transcript_content = ""
        if job.transcript_file_path:
            transcript_content = load_transcript_from_file(job.transcript_file_path)
        elif job.result:  # Fallback for old records
            transcript_content = job.result
            
        if transcript_content:
            download_button = f"""
                <div class='mb-3'>
                    <a href='/jobs/{job_id}/download' 
                       class='inline-flex items-center px-4 py-2 bg-blue-500 hover:bg-blue-600 text-white text-sm font-medium rounded-md transition duration-300'>
                        <svg class='w-4 h-4 mr-2' fill='none' stroke='currentColor' viewBox='0 0 24 24'>
                            <path stroke-linecap='round' stroke-linejoin='round' stroke-width='2' d='M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z'></path>
                        </svg>
                        Download Transcript
                    </a>
                </div>
            """
            transcript_section = f"""
                <div class='mt-6'>
                    <h3 class='text-lg font-semibold mb-3'>Transcription Result</h3>
                    {download_button}
                    <div class='bg-gray-50 p-4 rounded-lg border'>
                        <pre class='whitespace-pre-wrap text-sm leading-relaxed'>{transcript_content}</pre>
                    </div>
                </div>
            """
        elif job.status == "completed":
            transcript_section = """
                <div class='mt-6'>
                    <h3 class='text-lg font-semibold mb-3'>Transcription Result</h3>
                    <div class='bg-gray-50 p-4 rounded-lg border text-gray-500'>
                        Transcription completed but result not available.
                    </div>
                </div>
            """
        elif job.status == "processing":
            transcript_section = """
                <div class='mt-6'>
                    <h3 class='text-lg font-semibold mb-3'>Transcription Result</h3>
                    <div class='bg-blue-50 p-4 rounded-lg border text-blue-700'>
                        🔄 Transcription in progress... This may take a few minutes.
                    </div>
                </div>
            """
        elif job.status == "failed":
            transcript_section = """
                <div class='mt-6'>
                    <h3 class='text-lg font-semibold mb-3'>Transcription Result</h3>
                    <div class='bg-red-50 p-4 rounded-lg border text-red-700'>
                        ❌ Transcription failed. Please try uploading the file again.
                    </div>
                </div>
            """
        
        completed_info = ""
        if job.completed_at:
            completed_info = f"<div class='text-sm text-gray-500'>Completed: {job.completed_at.strftime('%Y-%m-%d %H:%M')}</div>"
        
        # Add auto-refresh for processing jobs
        auto_refresh = ""
        if job.status == "processing":
            auto_refresh = f'hx-get="/jobs/{job.id}" hx-target="#main-content" hx-swap="innerHTML" hx-trigger="every 3s"'
        
        detail_html = f"""
            <div class='max-w-4xl mx-auto bg-white rounded-lg shadow-md p-6' {auto_refresh}>
                <div class='flex items-center justify-between mb-6'>
                    <button 
                        hx-get="/jobs/list/view" 
                        hx-target="#main-content" 
                        hx-swap="innerHTML"
                        class='text-blue-500 hover:text-blue-600 flex items-center space-x-2'>
                        <span>←</span><span>Back to Jobs</span>
                    </button>
                    <span class='px-3 py-1 text-sm rounded-full {status_color}'>{job.status}</span>
                </div>
                
                <div class='mb-6'>
                    <h1 class='text-2xl font-bold text-gray-800 mb-2'>{job.filename}</h1>
                    <div class='text-sm text-gray-500 space-y-1'>
                        <div>Created: {job.created_at.strftime('%Y-%m-%d %H:%M')}</div>
                        {completed_info}
                        {f"<div>User ID: {job.user_id}</div>" if job.user_id else ""}
                    </div>
                </div>
                
                <div class='border-t pt-6'>
                    <h3 class='text-lg font-semibold mb-3'>Job Details</h3>
                    <div class='grid grid-cols-2 gap-4 text-sm'>
                        <div>
                            <span class='font-medium text-gray-600'>Status:</span>
                            <span class='ml-2'>{job.status}</span>
                        </div>
                        <div>
                            <span class='font-medium text-gray-600'>Job ID:</span>
                            <span class='ml-2'>#{job.id}</span>
                        </div>
                        {"<div><span class='font-medium text-gray-600'>File Size:</span><span class='ml-2'>" + f"{job.file_size // 1024:,} KB" + "</span></div>" if job.file_size else ""}
                        {"<div><span class='font-medium text-gray-600'>Audio File:</span><span class='ml-2 text-xs text-gray-500'>" + job.file_path + "</span></div>" if job.file_path else ""}
                        {"<div><span class='font-medium text-gray-600'>Transcript File:</span><span class='ml-2 text-xs text-gray-500'>" + job.transcript_file_path + "</span></div>" if job.transcript_file_path else ""}
                    </div>
                </div>
                
                {transcript_section}
            </div>
        """
        
        return HTMLResponse(detail_html)

@app.get("/jobs/list/view")
async def get_job_list_view(user: User = Depends(current_active_user)):
    with Session(engine) as session:
        jobs = session.exec(select(Job).where(Job.user_id == user.id).order_by(Job.created_at.desc())).all()
        if not jobs:
            jobs_content = "<div class='text-gray-500'>No jobs found.</div>"
        else:
            job_items = []
            for job in jobs:
                status_color = {
                    "pending": "text-yellow-600",
                    "processing": "text-blue-600", 
                    "completed": "text-green-600",
                    "failed": "text-red-600"
                }.get(job.status, "text-gray-600")
                
                file_info = f" • {job.file_size // 1024:,} KB" if job.file_size else ""
                job_items.append(f"<li class='py-2 border-b border-gray-200 last:border-b-0 hover:bg-gray-50 cursor-pointer' hx-get='/jobs/{job.id}' hx-target='#main-content' hx-swap='innerHTML'><div class='flex justify-between items-start'><div><div class='font-medium'>{job.filename}</div><div class='text-sm text-gray-500'>Created: {job.created_at.strftime('%Y-%m-%d %H:%M')}{file_info}</div></div><span class='px-2 py-1 text-xs rounded-full bg-gray-100 {status_color}'>{job.status}</span></div></li>")
            
            jobs_content = f"<ul class='divide-y divide-gray-200'>{''.join(job_items)}</ul>"
    
    list_view_html = f"""
        <div class='max-w-2xl mx-auto bg-white rounded-lg shadow-md p-6'>
            <h2 class='text-2xl font-semibold mb-4 text-gray-700'>Transcription Jobs</h2>
            
            <div class='space-y-4'>
                <form hx-post='/jobs/add' hx-target='#job-result' hx-swap='innerHTML' hx-encoding='multipart/form-data'>
                    <div class='space-y-3'>
                        <div>
                            <label class='block text-sm font-medium text-gray-700 mb-2'>Select Audio File</label>
                            <input 
                                type='file' 
                                name='file' 
                                accept='.wav,.mp3,audio/wav,audio/mpeg'
                                required
                                class='block w-full text-sm text-gray-500 file:mr-4 file:py-2 file:px-4 file:rounded-full file:border-0 file:text-sm file:font-semibold file:bg-blue-50 file:text-blue-700 hover:file:bg-blue-100'>
                            <p class='mt-1 text-xs text-gray-500'>Upload .wav or .mp3 files up to 100MB</p>
                        </div>
                        <button 
                            type='submit'
                            class='w-full bg-blue-500 hover:bg-blue-600 text-white font-bold py-2 px-4 rounded transition duration-300'>
                            Upload & Create Job
                        </button>
                    </div>
                </form>
                
                <div 
                    id='job-result' 
                    class='p-2 min-h-[30px]'>
                    <!-- Add job result will appear here -->
                </div>
                
                <div class='border-t pt-4'>
                    <div class='flex justify-between items-center mb-4'>
                        <h3 class='font-semibold'>Transcription Jobs:</h3>
                        <button 
                            hx-get='/jobs/list/view' 
                            hx-target='#main-content' 
                            hx-swap='innerHTML'
                            class='text-blue-500 hover:text-blue-600 text-sm'>
                            Refresh
                        </button>
                    </div>
                    <div 
                        id='job-list' 
                        class='bg-gray-50 rounded border min-h-[120px] p-4'>
                        {jobs_content}
                    </div>
                </div>
            </div>
        </div>
    """
    
    return HTMLResponse(list_view_html)

@app.get("/jobs/{job_id}/download")
async def download_transcript(job_id: int, user: User = Depends(current_active_user)):
    with Session(engine) as session:
        job = session.get(Job, job_id)
        if not job or job.user_id != user.id:
            raise HTTPException(status_code=404, detail="Job not found")
        
        if job.status != "completed":
            raise HTTPException(status_code=400, detail="Transcript not ready for download")
        
        transcript_content = ""
        if job.transcript_file_path:
            transcript_content = load_transcript_from_file(job.transcript_file_path)
        elif job.result:  # Fallback for old records
            transcript_content = job.result
            
        if not transcript_content:
            raise HTTPException(status_code=404, detail="Transcript content not found")
        
        # If we have a file path, serve the file directly
        if job.transcript_file_path and os.path.exists(job.transcript_file_path):
            filename = f"transcript_{job.filename.rsplit('.', 1)[0]}.md"
            return FileResponse(
                path=job.transcript_file_path,
                filename=filename,
                media_type='text/markdown'
            )
        else:
            # Create a temporary file for legacy records
            temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False, encoding='utf-8')
            temp_file.write(transcript_content)
            temp_file.close()
            
            filename = f"transcript_{job.filename.rsplit('.', 1)[0]}.md"
            return FileResponse(
                path=temp_file.name,
                filename=filename,
                media_type='text/markdown'
            )

def main():
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=True)

if __name__ == "__main__":
    main()
