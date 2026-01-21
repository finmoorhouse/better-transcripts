from fastapi import APIRouter, Request, File, UploadFile, Form, HTTPException, BackgroundTasks, Depends
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select
from auth import current_active_user, User
import tempfile
import os
import logging
import asyncio
import json

# Initialize router
router = APIRouter(tags=["jobs"])

# Initialize templates
templates = Jinja2Templates(directory="templates")

# Set up logging
logger = logging.getLogger(__name__)

# Dependencies imported locally within functions to avoid circular import

@router.post("/jobs/add")
async def add_job(background_tasks: BackgroundTasks, file: UploadFile = File(...), keyterms: str = Form(""), custom_instructions: str = Form(""), llm_model: str = Form("gemini-2.5-flash"), user: User = Depends(current_active_user)):
    # Local imports to avoid circular import
    from main import engine, validate_audio_file, save_uploaded_file, process_transcription
    from models import Job, JobStatus

    # Validate file
    error_msg = validate_audio_file(file)
    if error_msg:
        return HTMLResponse(f"<div class='text-red-600 font-semibold'>❌ {error_msg}</div>")

    with Session(engine) as session:
        # Create job first to get ID
        keyterms_cleaned = keyterms.strip() if keyterms else None
        custom_instructions_cleaned = custom_instructions.strip() if custom_instructions else None
        llm_model_cleaned = llm_model.strip() if llm_model else "gemini-2.5-flash"
        db_job = Job(filename=file.filename, status=JobStatus.processing, user_id=user.id, keyterms=keyterms_cleaned, custom_instructions=custom_instructions_cleaned, llm_model=llm_model_cleaned)
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
            
            # Start background transcription task (non-streaming)
            background_tasks.add_task(process_transcription, db_job.id)

            # Return success response with trigger to refresh job list
            return HTMLResponse(
                f"<div class='text-green-600 font-semibold'>✅ {file.filename} uploaded successfully. Transcription started in background.</div>",
                headers={
                    "HX-Trigger": "refreshJobs"
                }
            )
            
        except Exception as e:
            # If file save fails, delete the job
            session.delete(db_job)
            session.commit()
            return HTMLResponse(f"<div class='text-red-600 font-semibold'>❌ Failed to save file: {str(e)}</div>")


@router.post("/jobs/add-transcript")
async def add_transcript_job(background_tasks: BackgroundTasks, file: UploadFile = File(...), custom_instructions: str = Form(""), llm_model: str = Form("gemini-2.5-flash"), user: User = Depends(current_active_user)):
    """Upload a raw transcript file and process it with LLM (skips AssemblyAI transcription)."""
    from main import engine, process_raw_transcript, UPLOAD_DIR
    from models import Job, JobStatus

    # Validate file extension
    if not file.filename:
        return HTMLResponse("<div class='text-red-600 font-semibold'>❌ No file selected</div>")

    file_ext = os.path.splitext(file.filename)[1].lower()
    if file_ext not in {".md", ".txt"}:
        return HTMLResponse("<div class='text-red-600 font-semibold'>❌ Invalid file type. Only .md and .txt files are allowed for raw transcripts.</div>")

    with Session(engine) as session:
        custom_instructions_cleaned = custom_instructions.strip() if custom_instructions else None
        llm_model_cleaned = llm_model.strip() if llm_model else "gemini-2.5-flash"
        db_job = Job(
            filename=file.filename,
            status=JobStatus.processing,
            user_id=user.id,
            custom_instructions=custom_instructions_cleaned,
            llm_model=llm_model_cleaned
        )
        session.add(db_job)
        session.commit()
        session.refresh(db_job)

        try:
            # Save the raw transcript file
            safe_filename = f"job_{db_job.id}_{file.filename}"
            file_path = os.path.join(UPLOAD_DIR, safe_filename)

            with open(file_path, "wb") as buffer:
                import shutil
                shutil.copyfileobj(file.file, buffer)

            file_size = os.path.getsize(file_path)

            db_job.file_path = file_path
            db_job.file_size = file_size
            session.commit()

            # Start background processing task (skips AssemblyAI)
            background_tasks.add_task(process_raw_transcript, db_job.id)

            return HTMLResponse(
                f"<div class='text-green-600 font-semibold'>✅ {file.filename} uploaded successfully. LLM processing started (skipping transcription).</div>",
                headers={"HX-Trigger": "refreshJobs"}
            )

        except Exception as e:
            session.delete(db_job)
            session.commit()
            return HTMLResponse(f"<div class='text-red-600 font-semibold'>❌ Failed to save file: {str(e)}</div>")


# Streaming endpoint removed - using non-streaming background processing now

@router.get("/jobs/list")
async def list_jobs(request: Request, user: User = Depends(current_active_user)):
    from main import engine, strip_file_extension
    from models import Job, JobStatus
    from datetime import timezone

    with Session(engine) as session:
        jobs_raw = session.exec(select(Job).where(Job.user_id == user.id).order_by(Job.created_at.desc())).all()

        # Prepare job data for template
        jobs_data = []
        for job in jobs_raw:
            status_color = {
                "pending": "bg-flexoki-ye bg-opacity-20 text-flexoki-ye border-flexoki-ye",
                "processing": "bg-flexoki-bl bg-opacity-20 text-flexoki-bl border-flexoki-bl",
                "completed": "bg-flexoki-gr bg-opacity-20 text-flexoki-gr border-flexoki-gr",
                "failed": "bg-flexoki-re bg-opacity-20 text-flexoki-re border-flexoki-re"
            }.get(job.status, "bg-flexoki-ui-2 text-flexoki-tx-3 border-flexoki-ui-3")

            display_name = strip_file_extension(job.filename)

            # Format created date in natural way
            created_dt = job.created_at.replace(tzinfo=timezone.utc) if job.created_at.tzinfo is None else job.created_at
            created_local = created_dt.astimezone()
            created_natural = created_local.strftime('%B %d, %Y at %I:%M %p')

            jobs_data.append({
                "job": job,
                "display_name": display_name,
                "status_color": status_color,
                "created_natural": created_natural
            })

        return templates.TemplateResponse(
            "partials/job_list.html",
            {
                "request": request,
                "jobs": jobs_data
            }
        )

@router.get("/jobs/{job_id}")
async def get_job_detail(job_id: int, request: Request, user: User = Depends(current_active_user)):
    # Local import to avoid circular import
    from main import engine, load_transcript_from_file, format_transcript_for_display, extract_speakers_from_transcript, strip_file_extension
    from models import Job, JobStatus
    from datetime import timezone

    # Check if this is an HTMX request or a direct browser visit
    is_htmx_request = request.headers.get("HX-Request") == "true"

    with Session(engine) as session:
        job = session.get(Job, job_id)
        if not job or job.user_id != user.id:
            return HTMLResponse("<div class='text-red-500'>Job not found.</div>")

        display_name = strip_file_extension(job.filename)

        status_color = {
            "pending": "bg-flexoki-ye bg-opacity-20 text-flexoki-ye border-flexoki-ye",
            "processing": "bg-flexoki-bl bg-opacity-20 text-flexoki-bl border-flexoki-bl",
            "completed": "bg-flexoki-gr bg-opacity-20 text-flexoki-gr border-flexoki-gr",
            "failed": "bg-flexoki-re bg-opacity-20 text-flexoki-re border-flexoki-re"
        }.get(job.status, "bg-flexoki-ui-2 text-flexoki-tx-3 border-flexoki-ui-3")

        transcript_content = ""
        formatted_transcript = ""
        speakers = []

        if job.transcript_file_path:
            transcript_content = load_transcript_from_file(job.transcript_file_path)
        elif job.result:  # Fallback for old records
            transcript_content = job.result

        if transcript_content:
            # Format transcript for display (handles both JSON and plain text)
            formatted_transcript = format_transcript_for_display(transcript_content)
            # Extract speakers for rename form
            speakers = extract_speakers_from_transcript(transcript_content)

        # Format created date in a more natural way
        created_dt = job.created_at.replace(tzinfo=timezone.utc) if job.created_at.tzinfo is None else job.created_at
        created_local = created_dt.astimezone()
        created_natural = created_local.strftime('%B %d, %Y at %I:%M %p')

        completed_time = None
        if job.completed_at:
            # Format completion time as HH:MM only
            completed_time = job.completed_at.astimezone().strftime('%H:%M') if job.completed_at.tzinfo else job.completed_at.replace(tzinfo=timezone.utc).astimezone().strftime('%H:%M')

        context = {
            "request": request,
            "job": job,
            "display_name": display_name,
            "status_color": status_color,
            "transcript_content": transcript_content,
            "formatted_transcript": formatted_transcript,
            "speakers": speakers,
            "created_natural": created_natural,
            "completed_time": completed_time,
        }

        # If direct browser visit, wrap in full page layout
        if not is_htmx_request:
            return templates.TemplateResponse("job_detail_page.html", context)

        # If HTMX request, return just the partial
        return templates.TemplateResponse("partials/job_detail.html", context)

@router.get("/jobs")
async def get_job_list_view(request: Request, user: User = Depends(current_active_user)):
    from main import engine, strip_file_extension
    from models import Job, JobStatus
    from datetime import timezone

    # Check if this is an HTMX request or a direct browser visit
    is_htmx_request = request.headers.get("HX-Request") == "true"

    with Session(engine) as session:
        jobs_raw = session.exec(select(Job).where(Job.user_id == user.id).order_by(Job.created_at.desc())).all()

        # Prepare job data for template
        jobs_data = []
        for job in jobs_raw:
            status_color = {
                "pending": "bg-flexoki-ye bg-opacity-20 text-flexoki-ye border-flexoki-ye",
                "processing": "bg-flexoki-bl bg-opacity-20 text-flexoki-bl border-flexoki-bl",
                "completed": "bg-flexoki-gr bg-opacity-20 text-flexoki-gr border-flexoki-gr",
                "failed": "bg-flexoki-re bg-opacity-20 text-flexoki-re border-flexoki-re"
            }.get(job.status, "bg-flexoki-ui-2 text-flexoki-tx-3 border-flexoki-ui-3")

            display_name = strip_file_extension(job.filename)

            # Format created date in natural way
            created_dt = job.created_at.replace(tzinfo=timezone.utc) if job.created_at.tzinfo is None else job.created_at
            created_local = created_dt.astimezone()
            created_natural = created_local.strftime('%B %d, %Y at %I:%M %p')

            jobs_data.append({
                "job": job,
                "display_name": display_name,
                "status_color": status_color,
                "created_natural": created_natural
            })

        context = {
            "request": request,
            "jobs": jobs_data
        }

        # If direct browser visit, wrap in full page layout
        if not is_htmx_request:
            return templates.TemplateResponse("job_list_page.html", context)

        # If HTMX request, return just the partial
        return templates.TemplateResponse("partials/job_list_view.html", context)

@router.get("/jobs/{job_id}/download")
async def download_transcript(job_id: int, user: User = Depends(current_active_user)):
    # Local import to avoid circular import
    from main import engine, load_transcript_from_file
    from models import Job, JobStatus
    
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
            filename = f"{job.filename.rsplit('.', 1)[0]}.md"
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
            
            filename = f"{job.filename.rsplit('.', 1)[0]}.md"
            return FileResponse(
                path=temp_file.name,
                filename=filename,
                media_type='text/markdown'
            )

@router.post("/jobs/{job_id}/rename-speakers")
async def rename_speakers(job_id: int, request: Request, user: User = Depends(current_active_user)):
    """Rename speakers in the transcript."""
    from main import engine, load_transcript_from_file, extract_speakers_from_transcript
    from models import Job
    import re

    # Get form data
    form_data = await request.form()
    speaker_mappings = {}

    # Extract speaker mappings from form data
    for key, value in form_data.items():
        if key.startswith('speaker_'):
            original_speaker = key.replace('speaker_', '')
            new_name = value.strip()
            if new_name:  # Only add if new name is not empty
                speaker_mappings[original_speaker] = new_name

    # Validation: Check for duplicate new names
    new_names = list(speaker_mappings.values())
    if len(new_names) != len(set(new_names)):
        return HTMLResponse("""
            <div class='bg-red-50 border border-red-200 p-4 rounded-lg text-red-700'>
                ⚠️ Error: Speaker names must be unique. You have duplicate names in your input.
            </div>
        """, status_code=400)

    with Session(engine) as session:
        job = session.get(Job, job_id)
        if not job or job.user_id != user.id:
            raise HTTPException(status_code=404, detail="Job not found")

        if not job.transcript_file_path or not os.path.exists(job.transcript_file_path):
            raise HTTPException(status_code=404, detail="Transcript file not found")

        # Load the transcript
        transcript_content = load_transcript_from_file(job.transcript_file_path)

        # Perform speaker replacements
        updated_transcript = transcript_content
        for old_speaker, new_speaker in speaker_mappings.items():
            # Replace **OldSpeaker**: with **NewSpeaker**:
            pattern = re.escape(f'**{old_speaker}**:')
            replacement = f'**{new_speaker}**:'
            updated_transcript = re.sub(pattern, replacement, updated_transcript)

        # Save the updated transcript back to the file
        try:
            with open(job.transcript_file_path, 'w', encoding='utf-8') as f:
                f.write(updated_transcript)
            logger.info(f"Successfully renamed speakers for job {job_id}")
        except Exception as e:
            logger.error(f"Failed to save updated transcript: {str(e)}")
            return HTMLResponse("""
                <div class='bg-red-50 border border-red-200 p-4 rounded-lg text-red-700'>
                    ❌ Error: Failed to save updated transcript.
                </div>
            """, status_code=500)

        # Return success message with updated transcript preview
        from main import format_transcript_for_display
        formatted_transcript = format_transcript_for_display(updated_transcript)

        return HTMLResponse(f"""
            <div class='bg-green-50 border border-green-200 p-4 rounded-lg text-green-700 mb-4'>
                ✅ Speakers renamed successfully!
            </div>
            <div class='text-base leading-relaxed overflow-y-auto prose prose-base max-w-none font-serif'>{formatted_transcript}</div>
        """)

@router.delete("/jobs/{job_id}")
async def delete_job(job_id: int, user: User = Depends(current_active_user)):
    """Delete a job and clean up associated files."""
    from main import engine
    from models import Job, JobStatus
    
    with Session(engine) as session:
        job = session.get(Job, job_id)
        if not job or job.user_id != user.id:
            raise HTTPException(status_code=404, detail="Job not found")
        
        # Clean up files
        files_deleted = []
        
        # Delete audio file if it still exists
        if job.file_path and os.path.exists(job.file_path):
            try:
                os.remove(job.file_path)
                files_deleted.append(f"audio file: {job.file_path}")
                logger.info(f"Deleted audio file: {job.file_path}")
            except OSError as e:
                logger.warning(f"Failed to delete audio file {job.file_path}: {str(e)}")
        
        # Delete transcript file if it exists
        if job.transcript_file_path and os.path.exists(job.transcript_file_path):
            try:
                os.remove(job.transcript_file_path)
                files_deleted.append(f"transcript file: {job.transcript_file_path}")
                logger.info(f"Deleted transcript file: {job.transcript_file_path}")
            except OSError as e:
                logger.warning(f"Failed to delete transcript file {job.transcript_file_path}: {str(e)}")
        
        # Delete the job from database
        session.delete(job)
        session.commit()
        
        logger.info(f"Deleted job {job_id} ({job.filename}) and associated files")

        # Use HTMX redirect header to go back to jobs list
        return HTMLResponse(
            content="",
            headers={
                "HX-Redirect": "/jobs"
            }
        )