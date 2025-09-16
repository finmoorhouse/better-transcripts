from fastapi import FastAPI, Request, Form, File, UploadFile, HTTPException, BackgroundTasks, Depends
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from sqlmodel import SQLModel, Field, create_engine, Session, select, Relationship
from typing import Optional
from datetime import datetime, timezone
from enum import Enum
import uvicorn
import os
import shutil
import asyncio
import time
import tempfile
import uuid
import logging
import random
import json
from dotenv import load_dotenv
import assemblyai as aai
import markdown

# Set up logging with HTTP debugging
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s:%(name)s:%(message)s'
)
logger = logging.getLogger(__name__)

# Enable detailed HTTP logging for debugging API issues
logging.getLogger("httpx").setLevel(logging.INFO)


# Load environment variables
load_dotenv()

# Configure AssemblyAI API
aai.settings.api_key = os.getenv("ASSEMBLY_KEY")
transcriber = aai.Transcriber()

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
    keyterms: Optional[str] = Field(default=None)  # Comma-separated keyterms for transcription
    user_id: Optional[uuid.UUID] = Field(default=None, foreign_key="user.id", index=True)
    api_cost: Optional[float] = Field(default=None)  # Cost of this job in dollars
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
        logger.warning(f"Database creation failed: {e}. Dropping and recreating tables...")
        try:
            # If there are issues with existing tables/indexes, drop and recreate
            SQLModel.metadata.drop_all(engine)
            SQLModel.metadata.create_all(engine)
            logger.info("Database tables recreated successfully")
        except Exception as drop_error:
            logger.error(f"Failed to recreate database tables: {drop_error}")
            raise

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

def format_transcript_for_display(transcript_text: str) -> str:
    """Format transcript for display - convert markdown to HTML."""
    try:
        # Try to parse as JSON (legacy format) and convert to markdown first
        parsed_json = json.loads(transcript_text)
        # Convert JSON to markdown format
        markdown_lines = []
        for segment in parsed_json:
            if isinstance(segment, dict) and 'speaker' in segment and 'text' in segment:
                speaker_text = f"**{segment['speaker']}**: {segment['text'].strip()}"
                markdown_lines.append(speaker_text)
        markdown_text = "\n\n".join(markdown_lines)
        # Convert markdown to HTML
        return markdown.markdown(markdown_text)
    except json.JSONDecodeError:
        # If not JSON, assume it's already markdown and convert to HTML
        return markdown.markdown(transcript_text)



def format_local_datetime(utc_dt: datetime) -> str:
    """Convert UTC datetime to local time and format for display."""
    if utc_dt is None:
        return ""
    
    # Ensure the datetime is timezone-aware (UTC)
    if utc_dt.tzinfo is None:
        utc_dt = utc_dt.replace(tzinfo=timezone.utc)
    
    # Convert to local time
    local_dt = utc_dt.astimezone()
    
    # Format for display
    return local_dt.strftime('%Y-%m-%d %H:%M')

def create_assemblyai_config(keyterms: Optional[list] = None) -> aai.TranscriptionConfig:
    """Create AssemblyAI transcription configuration with speaker diarization."""
    config_params = {
        'speech_model': 'slam-1',  # Use SLAM-1 speech model
        'speaker_labels': True,  # Enable speaker diarization
        'auto_highlights': False,
        'iab_categories': False,
        'content_safety': False,
        'summarization': False,
        'punctuate': True,
        'format_text': True
    }

    # Only add keyterms_prompt if keyterms are provided
    if keyterms:
        config_params['keyterms_prompt'] = keyterms

    config = aai.TranscriptionConfig(**config_params)
    return config

def format_assemblyai_transcript(transcript: aai.Transcript) -> str:
    """Format AssemblyAI transcript with speaker diarization into markdown."""
    try:
        markdown_lines = []
        
        if transcript.utterances:
            # Use speaker-diarized utterances if available
            for utterance in transcript.utterances:
                speaker_text = f"**{utterance.speaker}**: {utterance.text.strip()}"
                markdown_lines.append(speaker_text)
        else:
            # Fallback to full transcript without speaker info
            speaker_text = f"**Speaker A**: {transcript.text}"
            markdown_lines.append(speaker_text)
        
        # Join with double newlines (blank line between speakers)
        return "\n\n".join(markdown_lines)
        
    except Exception as e:
        logger.error(f"Error formatting transcript: {e}")
        # Fallback to plain text
        return transcript.text if transcript.text else ""

def process_audio_with_assemblyai(file_path: str, keyterms: Optional[list] = None) -> str:
    """Process audio file with AssemblyAI and return formatted transcript."""
    try:
        logger.info(f"Starting AssemblyAI transcription for: {file_path}")

        # Create transcription config with speaker diarization and keyterms
        config = create_assemblyai_config(keyterms=keyterms)

        # Transcribe the audio file
        transcript = transcriber.transcribe(file_path, config=config)
        
        # Check for errors
        if transcript.error:
            logger.error(f"AssemblyAI transcription error: {transcript.error}")
            raise Exception(f"Transcription failed: {transcript.error}")
        
        logger.info(f"AssemblyAI transcription completed for: {file_path}")
        
        # Format the transcript with speaker diarization
        formatted_transcript = format_assemblyai_transcript(transcript)
        
        return formatted_transcript
        
    except Exception as e:
        logger.error(f"Error processing audio with AssemblyAI: {str(e)}")
        raise


def process_transcription(job_id: int):
    """Background task to transcribe audio using AssemblyAI API."""
    # Get job details and file path first
    with Session(engine) as session:
        job = session.get(Job, job_id)
        if not job:
            logger.error(f"Job {job_id} not found")
            return
        
        if not job.file_path:
            logger.error(f"Job {job_id} missing file path - file may have been deleted or job already processed")
            return
        
        file_path = job.file_path
        filename = job.filename
        user_id = job.user_id
        keyterms_str = job.keyterms

    try:
        logger.info(f"Starting AssemblyAI transcription for job {job_id}: {filename}")

        # Parse keyterms from comma-separated string to list
        keyterms = None
        if keyterms_str and keyterms_str.strip():
            keyterms = [term.strip() for term in keyterms_str.split(',') if term.strip()]

        # Process the entire audio file with AssemblyAI (no chunking needed)
        transcript_text = process_audio_with_assemblyai(file_path, keyterms=keyterms)
        logger.info(f"AssemblyAI transcription completed for job {job_id}")
        
        # Save transcript to file
        transcript_file_path = save_transcript_to_file(job_id, transcript_text)
        
        # Delete the original audio file after successful processing
        try:
            os.remove(file_path)
            logger.info(f"Deleted original audio file: {file_path}")
        except OSError as e:
            logger.warning(f"Failed to delete original audio file {file_path}: {str(e)}")
        
        # For now, use a small fixed cost - you can implement actual cost calculation later
        api_cost = 0.25  # Reduced cost for AssemblyAI
        
        # Single database operation: update job completion and clear file path
        with Session(engine) as session:
            job = session.get(Job, job_id)
            if job:
                job.status = JobStatus.completed
                job.transcript_file_path = transcript_file_path
                job.completed_at = datetime.utcnow()
                job.api_cost = api_cost
                job.file_path = None  # Clear file path since we deleted the file
                
                # Update user's total API cost in the same transaction
                if user_id:
                    user = session.get(User, user_id)
                    if user:
                        user.total_api_cost += api_cost
                
                session.commit()
                logger.info(f"Job {job_id} completed successfully")
        
    except Exception as e:
        logger.error(f"Transcription failed for job {job_id}: {str(e)}")
        # Handle general failure with a fresh session
        try:
            with Session(engine) as session:
                job = session.get(Job, job_id)
                if job:
                    job.status = JobStatus.failed
                    job.completed_at = datetime.utcnow()
                    session.commit()
        except Exception as cleanup_error:
            logger.error(f"Failed to update job status for job {job_id}: {str(cleanup_error)}")

# Removed old Gemini streaming code - now using AssemblyAI

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

# Include page routes
from page_routes import router as page_router
app.include_router(page_router)

# Include auth status routes
from auth_routes import router as auth_router
app.include_router(auth_router)

# Include job routes  
from job_routes import router as job_router
app.include_router(job_router)

# Routes are now handled by job_routes.py

def main():
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=True)

if __name__ == "__main__":
    main()
