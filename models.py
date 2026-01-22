from sqlmodel import SQLModel, Field
from typing import Optional
from datetime import datetime
from enum import Enum
import uuid

# Job status enumeration
class JobStatus(str, Enum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"

# SQLModel Job model
class Job(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    filename: str = Field(index=True)
    file_path: Optional[str] = Field(default=None)  # Path to uploaded audio file
    file_size: Optional[int] = Field(default=None)  # File size in bytes
    status: JobStatus = Field(default=JobStatus.pending)
    transcript: Optional[str] = Field(default=None)  # Deprecated - use result instead
    result: Optional[str] = Field(default=None)  # Deprecated - use transcript_file_path instead
    transcript_file_path: Optional[str] = Field(default=None)  # Path to transcript .md file
    keyterms: Optional[str] = Field(default=None)  # Comma-separated keyterms for transcription
    custom_instructions: Optional[str] = Field(default=None)  # Custom instructions for GPT-5 processing
    llm_model: Optional[str] = Field(default="gemini-2.5-flash")  # LLM model for transcript processing
    progress_message: Optional[str] = Field(default=None)  # Current progress status message
    chunks_total: Optional[int] = Field(default=None)  # Total number of chunks to process
    chunks_completed: Optional[int] = Field(default=None)  # Number of chunks completed
    user_id: Optional[uuid.UUID] = Field(default=None, foreign_key="user.id", index=True)
    api_cost: Optional[float] = Field(default=None)  # Cost of this job in dollars
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = Field(default=None)
