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
import google.genai as genai
import httpx
from pydub import AudioSegment
from pydub.silence import detect_nonsilent

# Set up logging with HTTP debugging
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s:%(name)s:%(message)s'
)
logger = logging.getLogger(__name__)

# Enable detailed HTTP logging for debugging API issues
logging.getLogger("httpx").setLevel(logging.INFO)
logging.getLogger("google.genai").setLevel(logging.INFO)

# Enable debug-level logging for HTTP requests (shows full request/response details)
# Uncomment the lines below for even more detailed HTTP debugging:
# logging.getLogger("httpx").setLevel(logging.DEBUG)
# logging.getLogger("httpcore").setLevel(logging.DEBUG)

# Set to True to enable verbose HTTP debugging (can be toggled easily)
VERBOSE_HTTP_DEBUG = os.getenv("VERBOSE_HTTP_DEBUG", "false").lower() == "true"
if VERBOSE_HTTP_DEBUG:
    logging.getLogger("httpx").setLevel(logging.DEBUG)
    logging.getLogger("httpcore").setLevel(logging.DEBUG)
    logger.info("Verbose HTTP debugging enabled")

# Custom exception for transcription timeouts
class TranscriptionTimeoutError(Exception):
    """Custom exception for transcription timeout errors"""
    pass

# Load environment variables
load_dotenv()

# Configure Google Gemini API
from google.genai import types
client = genai.Client(
    api_key=os.getenv("GEMINI_KEY"),
    http_options=types.HttpOptions(
        timeout=900_000  # 15 minutes in milliseconds
    )
)

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

def format_transcript_for_display(transcript_text: str) -> str:
    """Format transcript for display - handle both JSON and plain text."""
    try:
        # Try to parse as JSON and format nicely
        parsed_json = json.loads(transcript_text)
        return json.dumps(parsed_json, indent=2, ensure_ascii=False)
    except json.JSONDecodeError:
        # If not JSON, return as-is (backwards compatibility)
        return transcript_text

def chunk_audio_file(file_path: str, chunk_minutes: int = 20, overlap_minutes: int = 2) -> list[str]:
    """
    Split audio file into chunks with overlap for reliable processing.
    
    Args:
        file_path: Path to the audio file
        chunk_minutes: Target chunk length in minutes (default: 20)
        overlap_minutes: Overlap between chunks in minutes (default: 2)
    
    Returns:
        List of paths to chunk files
    """
    try:
        logger.info(f"Loading audio file for chunking: {file_path}")
        
        # Load audio file
        audio = AudioSegment.from_file(file_path)
        total_duration_ms = len(audio)
        total_minutes = total_duration_ms / (1000 * 60)
        
        logger.info(f"Audio duration: {total_minutes:.1f} minutes")
        
        # If file is shorter than chunk size, no need to split
        if total_minutes <= chunk_minutes:
            logger.info(f"File is {total_minutes:.1f} minutes, shorter than chunk size ({chunk_minutes} min). No splitting needed.")
            return [file_path]
        
        # Convert times to milliseconds
        chunk_length_ms = chunk_minutes * 60 * 1000
        overlap_ms = overlap_minutes * 60 * 1000
        step_size_ms = chunk_length_ms - overlap_ms
        
        chunk_paths = []
        chunk_num = 0
        start_ms = 0
        
        while start_ms < total_duration_ms:
            chunk_num += 1
            end_ms = min(start_ms + chunk_length_ms, total_duration_ms)
            
            logger.info(f"Creating chunk {chunk_num}: {start_ms/60000:.1f}m - {end_ms/60000:.1f}m")
            
            # Extract chunk
            chunk = audio[start_ms:end_ms]
            
            # Generate chunk filename
            base_name = os.path.splitext(file_path)[0]
            chunk_path = f"{base_name}_chunk_{chunk_num:03d}.wav"
            
            # Export chunk as WAV for consistent format
            chunk.export(chunk_path, format="wav")
            chunk_paths.append(chunk_path)
            
            logger.info(f"Saved chunk {chunk_num}: {chunk_path}")
            
            # Move to next chunk
            start_ms += step_size_ms
            
            # Break if we've reached the end
            if end_ms >= total_duration_ms:
                break
        
        logger.info(f"Created {len(chunk_paths)} audio chunks")
        return chunk_paths
        
    except Exception as e:
        logger.error(f"Error chunking audio file {file_path}: {str(e)}")
        # Return original file if chunking fails
        return [file_path]

def cleanup_chunk_files(chunk_paths: list[str], original_path: str):
    """Clean up temporary chunk files, keeping the original."""
    for chunk_path in chunk_paths:
        if chunk_path != original_path and os.path.exists(chunk_path):
            try:
                os.remove(chunk_path)
                logger.info(f"Cleaned up chunk: {chunk_path}")
            except OSError as e:
                logger.warning(f"Failed to cleanup chunk {chunk_path}: {str(e)}")

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

def prepare_content(uploaded_file, previous_transcript: str = None, chunk_number: int = 1, total_chunks: int = 1) -> list:
    """Prepare audio file and prompt for transcription with structured output."""
    audio = types.Part.from_uri(
        file_uri=uploaded_file.uri,
        mime_type=uploaded_file.mime_type,
    )
    
    # Base prompt
    prompt = f"""Generate a detailed diarized transcript for this audio file (chunk {chunk_number} of {total_chunks}). 
    
IMPORTANT: Group ALL consecutive speech from the same speaker into a SINGLE JSON entry. Only create a new JSON entry when the speaker changes.

For example:
- If Speaker 1 talks for 30 seconds continuously, put all their text in ONE entry
- Only create a new entry when Speaker 2 starts talking
- Then group all of Speaker 2's consecutive speech into one entry
- And so on

Remove filler words, repetition, and other non-essential content — the result should be a slightly tidied up and fluent version of the actual speech."""

    # Add context from previous chunks if available
    if previous_transcript and chunk_number > 1:
        prompt += f"""

CONTEXT FROM PREVIOUS CHUNKS:
The speakers from earlier parts of this audio are:
{previous_transcript}

IMPORTANT: Continue using the SAME speaker numbering as the previous chunks. If "Speaker 1" and "Speaker 2" were speaking in previous chunks, continue using those exact labels for the same people in this chunk. Do NOT restart numbering.

This helps maintain speaker consistency across the entire transcript."""

    text = types.Part.from_text(text=prompt)
    return [
        types.Content(
            role="user",
            parts=[audio, text]
        ),
    ]

def configure_generation() -> types.GenerateContentConfig:
    """Configure generation with JSON schema for structured response."""
    # JSON schema for structured response
    schema = {
        "type": "ARRAY",
        "description": "A diarized transcript with speaker changes. Each entry represents ALL consecutive speech from one speaker before switching to another speaker.",
        "items": {
            "type": "OBJECT",
            "properties": {
                "timestamp": {"type": "STRING", "description": "Start time when this speaker begins (mm:ss format)"},
                "speaker": {"type": "STRING", "description": "Speaker identifier (e.g., Speaker 1, Speaker 2)"},
                "text": {"type": "STRING", "description": "All consecutive transcribed text from this speaker until the next speaker begins"}
            },
            "required": ["speaker", "text"]
        }
    }

    # Config params
    return types.GenerateContentConfig(
        temperature=1.0,
        top_p=0.95,
        seed=0,
        max_output_tokens=32768,
        response_modalities=["TEXT"],
        response_mime_type="application/json",
        response_schema=schema,
    )

def process_single_chunk(uploaded_file, chunk_number: int, total_chunks: int, previous_transcript: str = None) -> str:
    """Process a single audio chunk and return the transcript with robust retry logic."""
    max_retries = 5  # Increased from 3 to 5 for connection issues
    base_delay = 3  # Start with 3 second delay
    
    for attempt in range(max_retries):
        try:
            # Prepare structured transcription request with context
            contents = prepare_content(uploaded_file, previous_transcript, chunk_number, total_chunks)
            config = configure_generation()
            
            logger.info(f"Starting transcription for chunk {chunk_number}/{total_chunks} (attempt {attempt + 1}/{max_retries})")
            
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=contents,
                config=config
            )
            transcript_text = response.text.strip()
            
            # Validate JSON response
            try:
                json.loads(transcript_text)
                logger.info(f"Valid JSON response received for chunk {chunk_number}")
            except json.JSONDecodeError as json_err:
                logger.warning(f"Response is not valid JSON for chunk {chunk_number}: {json_err}")
            
            return transcript_text
            
        except Exception as api_e:
            error_message = str(api_e)
            error_type = type(api_e).__name__
            
            # Log full exception details for debugging
            logger.error(f"Exception details for chunk {chunk_number}: {error_type}: {error_message}")
            if hasattr(api_e, 'response') and api_e.response is not None:
                logger.error(f"HTTP Response status: {api_e.response.status_code}")
                logger.error(f"HTTP Response headers: {dict(api_e.response.headers)}")
                if hasattr(api_e.response, 'text'):
                    logger.error(f"HTTP Response body: {api_e.response.text[:500]}")  # First 500 chars
            
            # Check if this is a retryable error (connection issues, 503, rate limits, etc.)
            is_retryable = (
                isinstance(api_e, (httpx.RemoteProtocolError, httpx.ReadTimeout, httpx.ConnectTimeout, httpx.TimeoutException)) or
                "503" in error_message or 
                "overloaded" in error_message.lower() or
                "rate limit" in error_message.lower() or
                "too many requests" in error_message.lower() or
                "unavailable" in error_message.lower() or
                "server disconnected" in error_message.lower() or
                "connection" in error_message.lower()
            )
            
            if is_retryable and attempt < max_retries - 1:
                # Exponential backoff with jitter for connection issues
                if isinstance(api_e, (httpx.RemoteProtocolError, httpx.ReadTimeout, httpx.ConnectTimeout, httpx.TimeoutException)):
                    delay = base_delay * (2 ** attempt) + random.uniform(2, 5)  # Longer delay for connection issues
                    logger.warning(f"Connection error for chunk {chunk_number} (attempt {attempt + 1}/{max_retries}): {error_type}: {error_message}")
                else:
                    delay = base_delay * (2 ** attempt) + random.uniform(1, 3)
                    logger.warning(f"Retryable error for chunk {chunk_number} (attempt {attempt + 1}/{max_retries}): {error_type}: {error_message}")
                
                logger.info(f"Retrying chunk {chunk_number} in {delay:.1f} seconds...")
                time.sleep(delay)
                continue
            else:
                # Only raise timeout error if we've exhausted all retries on connection issues
                if isinstance(api_e, (httpx.RemoteProtocolError, httpx.ReadTimeout, httpx.ConnectTimeout, httpx.TimeoutException)):
                    logger.error(f"HTTP timeout/disconnect during transcription for chunk {chunk_number} after {max_retries} attempts: {error_type}: {api_e}")
                    raise TranscriptionTimeoutError(
                        f"Transcription timed out for chunk {chunk_number} after {max_retries} attempts. Consider using smaller chunks."
                    ) from api_e
                else:
                    logger.error(f"API error during transcription for chunk {chunk_number} after {max_retries} attempts: {error_type}: {api_e}")
                    raise
    
    # Should never reach here due to the raise in the except block
    raise Exception(f"Failed to process chunk {chunk_number} after {max_retries} attempts")

def merge_chunk_transcripts(chunk_transcripts: list[str], overlap_minutes: int = 2) -> str:
    """Merge multiple chunk transcripts, removing overlaps and aligning speakers."""
    if not chunk_transcripts:
        return ""
    
    if len(chunk_transcripts) == 1:
        return chunk_transcripts[0]
    
    try:
        merged_segments = []
        
        for i, chunk_transcript in enumerate(chunk_transcripts):
            try:
                chunk_json = json.loads(chunk_transcript)
                logger.info(f"Processing chunk {i+1} with {len(chunk_json)} segments")
                
                if i == 0:
                    # First chunk - add all segments
                    merged_segments.extend(chunk_json)
                else:
                    # Subsequent chunks - skip overlapping content
                    # For now, add all segments (overlap removal can be enhanced later)
                    for segment in chunk_json:
                        # Simple duplicate removal - skip if text matches last few segments
                        duplicate = False
                        for recent_segment in merged_segments[-3:]:  # Check last 3 segments
                            if segment.get('text', '').strip() == recent_segment.get('text', '').strip():
                                duplicate = True
                                logger.info(f"Skipping duplicate segment: {segment.get('text', '')[:50]}...")
                                break
                        
                        if not duplicate:
                            merged_segments.append(segment)
                            
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse chunk {i+1} as JSON: {e}")
                # If JSON parsing fails, treat as plain text
                if i == 0:
                    merged_segments.append({"speaker": "Speaker 1", "text": chunk_transcript})
                else:
                    merged_segments.append({"speaker": "Unknown", "text": chunk_transcript})
        
        logger.info(f"Merged {len(chunk_transcripts)} chunks into {len(merged_segments)} total segments")
        return json.dumps(merged_segments, indent=2, ensure_ascii=False)
        
    except Exception as e:
        logger.error(f"Error merging chunk transcripts: {e}")
        # Fallback - concatenate all transcripts
        return "\n\n=== CHUNK SEPARATOR ===\n\n".join(chunk_transcripts)

def process_transcription(job_id: int):
    """Background task to transcribe audio using Google Gemini API."""
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
    
    # Process the transcription without holding database connections
    chunk_paths = []
    try:
        logger.info(f"Starting chunked transcription for job {job_id}: {filename}")
        
        # Step 1: Split audio into chunks
        chunk_paths = chunk_audio_file(file_path, chunk_minutes=15, overlap_minutes=2)
        logger.info(f"Created {len(chunk_paths)} chunks for processing")
        
        # Step 2: Process chunks sequentially with context
        chunk_transcripts = []
        combined_previous_transcript = ""
        
        for i, chunk_path in enumerate(chunk_paths):
            chunk_number = i + 1
            total_chunks = len(chunk_paths)
            
            logger.info(f"Processing chunk {chunk_number}/{total_chunks}: {os.path.basename(chunk_path)}")
            
            try:
                # Upload chunk to Gemini
                uploaded_file = client.files.upload(file=chunk_path)
                logger.info(f"Chunk {chunk_number} uploaded to Gemini: {uploaded_file.name}")
                
                # Process chunk with context from previous chunks
                chunk_transcript = process_single_chunk(
                    uploaded_file, 
                    chunk_number, 
                    total_chunks, 
                    combined_previous_transcript if chunk_number > 1 else None
                )
                
                chunk_transcripts.append(chunk_transcript)
                logger.info(f"Chunk {chunk_number} transcription completed")
                
                # Update combined context for next chunk (keep it concise)
                try:
                    chunk_json = json.loads(chunk_transcript)
                    # Keep last few segments as context
                    recent_segments = chunk_json[-2:] if len(chunk_json) > 2 else chunk_json
                    combined_previous_transcript = json.dumps(recent_segments, indent=2)
                except json.JSONDecodeError:
                    # If not JSON, use text directly but keep it short
                    combined_previous_transcript = chunk_transcript[-500:] if len(chunk_transcript) > 500 else chunk_transcript
                
                # Clean up uploaded file from Gemini
                try:
                    client.files.delete(name=uploaded_file.name)
                    logger.info(f"Deleted chunk {chunk_number} from Gemini: {uploaded_file.name}")
                except Exception as cleanup_e:
                    logger.warning(f"Failed to cleanup chunk {chunk_number} from Gemini: {cleanup_e}")
                
                # Add delay between chunks to avoid overwhelming the API and reduce connection issues
                if chunk_number < total_chunks:  # Don't delay after the last chunk
                    delay = 3 + random.uniform(1.0, 2.0)  # 4-5 second delay (increased)
                    logger.info(f"Waiting {delay:.1f}s before processing next chunk...")
                    time.sleep(delay)
                
            except TranscriptionTimeoutError:
                logger.error(f"Chunk {chunk_number} timed out - aborting remaining chunks")
                raise
            except Exception as chunk_e:
                logger.error(f"Error processing chunk {chunk_number}: {str(chunk_e)}")
                raise
        
        # Step 3: Merge all chunk transcripts
        logger.info("Merging chunk transcripts...")
        transcript_text = merge_chunk_transcripts(chunk_transcripts, overlap_minutes=2)
        logger.info(f"Chunked transcription completed for job {job_id}")
        
        # Clean up chunk files
        cleanup_chunk_files(chunk_paths, file_path)
        
        # Save transcript to file
        transcript_file_path = save_transcript_to_file(job_id, transcript_text)
        
        # Delete the original audio file after successful processing
        try:
            os.remove(file_path)
            logger.info(f"Deleted original audio file: {file_path}")
        except OSError as e:
            logger.warning(f"Failed to delete original audio file {file_path}: {str(e)}")
        
        # For now, use a small fixed cost - you can implement actual cost calculation later
        api_cost = 0.50  # Placeholder cost
        
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
        
    except TranscriptionTimeoutError as timeout_e:
        logger.error(f"Transcription timeout for job {job_id}: {str(timeout_e)}")
        # Clean up chunk files on timeout
        if chunk_paths:
            cleanup_chunk_files(chunk_paths, file_path)
        # Handle timeout failure with a fresh session - mark as failed with timeout message
        try:
            with Session(engine) as session:
                job = session.get(Job, job_id)
                if job:
                    job.status = JobStatus.failed
                    job.completed_at = datetime.utcnow()
                    # Could add a timeout-specific error field here in future
                    session.commit()
                    logger.info(f"Job {job_id} marked as failed due to timeout")
        except Exception as cleanup_error:
            logger.error(f"Failed to update job status for job {job_id}: {str(cleanup_error)}")
    except Exception as e:
        logger.error(f"Transcription failed for job {job_id}: {str(e)}")
        # Clean up chunk files on general error
        if chunk_paths:
            cleanup_chunk_files(chunk_paths, file_path)
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

# Removed old streaming code - now using chunked non-streaming approach

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
