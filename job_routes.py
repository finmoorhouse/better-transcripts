from fastapi import APIRouter, Request, File, UploadFile, Form, HTTPException, BackgroundTasks, Depends
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from sqlmodel import Session, select
from auth import current_active_user, User
import tempfile
import os
import logging
import asyncio
import json

# Initialize router
router = APIRouter(tags=["jobs"])

# Set up logging
logger = logging.getLogger(__name__)

# Dependencies imported locally within functions to avoid circular import

@router.post("/jobs/add")
async def add_job(background_tasks: BackgroundTasks, file: UploadFile = File(...), keyterms: str = Form(""), user: User = Depends(current_active_user)):
    # Local imports to avoid circular import
    from main import engine, Job, JobStatus, validate_audio_file, save_uploaded_file, process_transcription
    
    # Validate file
    error_msg = validate_audio_file(file)
    if error_msg:
        return HTMLResponse(f"<div class='text-red-600 font-semibold'>❌ {error_msg}</div>")
    
    with Session(engine) as session:
        # Create job first to get ID
        keyterms_cleaned = keyterms.strip() if keyterms else None
        db_job = Job(filename=file.filename, status=JobStatus.processing, user_id=user.id, keyterms=keyterms_cleaned)
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
            
            # Return simple success response
            return HTMLResponse(f"<div class='text-green-600 font-semibold'>✅ {file.filename} uploaded successfully. Transcription started in background.</div>")
            
        except Exception as e:
            # If file save fails, delete the job
            session.delete(db_job)
            session.commit()
            return HTMLResponse(f"<div class='text-red-600 font-semibold'>❌ Failed to save file: {str(e)}</div>")

# Streaming endpoint removed - using non-streaming background processing now

@router.get("/jobs/list")
async def list_jobs(user: User = Depends(current_active_user)):
    from main import engine, Job, JobStatus, format_local_datetime
    with Session(engine) as session:
        jobs = session.exec(select(Job).where(Job.user_id == user.id).order_by(Job.created_at.desc())).all()
        if not jobs:
            return HTMLResponse("<div class='text-center py-8 text-flexoki-tx-3'>No transcription jobs found. Upload an audio file to get started!</div>")
        
        job_items = []
        for job in jobs:
            status_color = {
                "pending": "bg-flexoki-ye bg-opacity-20 text-flexoki-ye border-flexoki-ye",
                "processing": "bg-flexoki-bl bg-opacity-20 text-flexoki-bl border-flexoki-bl", 
                "completed": "bg-flexoki-gr bg-opacity-20 text-flexoki-gr border-flexoki-gr",
                "failed": "bg-flexoki-re bg-opacity-20 text-flexoki-re border-flexoki-re"
            }.get(job.status, "bg-flexoki-ui-2 text-flexoki-tx-3 border-flexoki-ui-3")
            
            file_info = f"{job.file_size // 1024:,} KB" if job.file_size else "Unknown size"
            cost_info = f"${job.api_cost:.2f}" if job.api_cost else "—"
            
            job_items.append(f"""
                <li class='p-4 hover:bg-flexoki-ui bg-flexoki-paper-light cursor-pointer transition-colors duration-200 border-b border-flexoki-ui-3 last:border-b-0' 
                    hx-get='/jobs/{job.id}' 
                    hx-target='#main-content' 
                    hx-swap='innerHTML'>
                    <div class='flex justify-between items-start'>
                        <div class='flex-1 min-w-0'>
                            <div class='font-medium text-flexoki-tx truncate mb-1'>{job.filename}</div>
                            <div class='text-sm text-flexoki-tx-3 space-y-1'>
                                <div>Created: {format_local_datetime(job.created_at)}</div>
                                <div class='flex items-center space-x-4'>
                                    <span>Size: {file_info}</span>
                                    <span>Cost: {cost_info}</span>
                                </div>
                            </div>
                        </div>
                        <div class='ml-4 flex-shrink-0'>
                            <span class='inline-flex items-center px-2.5 py-1 text-xs font-medium rounded-full border {status_color}'>
                                {job.status.title()}
                            </span>
                        </div>
                    </div>
                </li>
            """)
        
        return HTMLResponse(f"<ul class='bg-flexoki-paper rounded-lg border border-flexoki-ui-3 overflow-hidden'>{''.join(job_items)}</ul>")

@router.get("/jobs/{job_id}")
async def get_job_detail(job_id: int, user: User = Depends(current_active_user)):
    # Local import to avoid circular import
    from main import engine, Job, JobStatus, load_transcript_from_file, format_local_datetime, format_transcript_for_display
    
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
            # Format transcript for display (handles both JSON and plain text)
            formatted_transcript = format_transcript_for_display(transcript_content)
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
                    <div class='w-full'>
                        <div class='text-base leading-relaxed overflow-y-auto prose prose-base max-w-none font-serif'>{formatted_transcript}</div>
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
            completed_info = f"<div class='text-sm text-gray-500'>Completed: {format_local_datetime(job.completed_at)}</div>"
        
        detail_html = f"""
            <div class='max-w-2xl mx-auto bg-white border border-flexoki-ui-3 p-6 rounded-lg'>
                <div class='flex items-center justify-between mb-6'>
                    <button 
                        hx-get="/jobs/list/view" 
                        hx-target="#main-content" 
                        hx-swap="innerHTML"
                        class='text-blue-500 hover:text-blue-600 flex items-center space-x-2'>
                        <span>←</span><span>Back to Jobs</span>
                    </button>
                    <div class='flex items-center space-x-3'>
                        <span class='px-3 py-1 text-sm rounded-full {status_color}'>{job.status}</span>
                        <button 
                            id="delete-btn-{job.id}"
                            onclick="toggleDeleteButton({job.id})"
                            class='bg-red-500 hover:bg-red-600 text-white text-sm px-3 py-1 rounded transition duration-300 flex items-center space-x-1'>
                            <svg class='w-4 h-4' fill='none' stroke='currentColor' viewBox='0 0 24 24'>
                                <path stroke-linecap='round' stroke-linejoin='round' stroke-width='2' d='M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16'></path>
                            </svg>
                            <span id="delete-text-{job.id}">Delete</span>
                        </button>
                    </div>
                </div>
                
                <div class='mb-6'>
                    <h1 class='text-2xl font-bold text-gray-800 mb-2'>{job.filename}</h1>
                    <div class='text-sm text-gray-500 space-y-1'>
                        <div>Created: {format_local_datetime(job.created_at)}</div>
                        {completed_info}
                        {f"<div>User ID: {job.user_id}</div>" if job.user_id else ""}
                    </div>
                </div>
                
                <div class='border-t pt-6 mb-6'>
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
                        {"<div><span class='font-medium text-gray-600'>API Cost:</span><span class='ml-2 font-semibold text-green-600'>$" + f"{job.api_cost:.2f}" + "</span></div>" if job.api_cost else ""}
                        {"<div><span class='font-medium text-gray-600'>Audio File:</span><span class='ml-2 text-xs text-gray-500'>" + job.file_path + "</span></div>" if job.file_path else ""}
                        {"<div><span class='font-medium text-gray-600'>Transcript File:</span><span class='ml-2 text-xs text-gray-500'>" + job.transcript_file_path + "</span></div>" if job.transcript_file_path else ""}
                    </div>
                </div>
                <hr/>
                {transcript_section}
            </div>
            
            <script>
                function toggleDeleteButton(jobId) {{
                    const btn = document.getElementById(`delete-btn-${{jobId}}`);
                    const text = document.getElementById(`delete-text-${{jobId}}`);
                    
                    if (text.textContent === 'Delete') {{
                        // First click - change to "Sure?"
                        text.textContent = 'Sure?';
                        btn.classList.remove('bg-red-500', 'hover:bg-red-600');
                        btn.classList.add('bg-red-600', 'hover:bg-red-700');
                        
                        // Reset after 3 seconds if not clicked again
                        setTimeout(() => {{
                            if (text.textContent === 'Sure?') {{
                                text.textContent = 'Delete';
                                btn.classList.remove('bg-red-600', 'hover:bg-red-700');
                                btn.classList.add('bg-red-500', 'hover:bg-red-600');
                            }}
                        }}, 3000);
                    }} else {{
                        // Second click - actually delete
                        htmx.ajax('DELETE', `/jobs/${{jobId}}`, {{
                            target: '#main-content',
                            swap: 'innerHTML'
                        }});
                    }}
                }}
            </script>
        """
        
        return HTMLResponse(detail_html)

@router.get("/jobs/list/view")
async def get_job_list_view(user: User = Depends(current_active_user)):
    from main import engine, Job, JobStatus, format_local_datetime
    with Session(engine) as session:
        jobs = session.exec(select(Job).where(Job.user_id == user.id).order_by(Job.created_at.desc())).all()
        if not jobs:
            jobs_content = "<div class='text-center py-8 text-flexoki-tx-3'>No transcription jobs found. Upload an audio file to get started!</div>"
        else:
            job_items = []
            for job in jobs:
                status_color = {
                    "pending": "bg-flexoki-ye bg-opacity-20 text-flexoki-ye border-flexoki-ye",
                    "processing": "bg-flexoki-bl bg-opacity-20 text-flexoki-bl border-flexoki-bl", 
                    "completed": "bg-flexoki-gr bg-opacity-20 text-flexoki-gr border-flexoki-gr",
                    "failed": "bg-flexoki-re bg-opacity-20 text-flexoki-re border-flexoki-re"
                }.get(job.status, "bg-flexoki-ui-2 text-flexoki-tx-3 border-flexoki-ui-3")
                
                file_info = f"{job.file_size // 1024:,} KB" if job.file_size else "Unknown size"
                cost_info = f"${job.api_cost:.2f}" if job.api_cost else "—"
                
                job_items.append(f"""
                    <li class='p-4 hover:bg-flexoki-ui bg-flexoki-paper-light cursor-pointer transition-colors duration-200 border-b border-flexoki-ui-3 last:border-b-0' 
                        hx-get='/jobs/{job.id}' 
                        hx-target='#main-content' 
                        hx-swap='innerHTML'>
                        <div class='flex justify-between items-start'>
                            <div class='flex-1  min-w-0'>
                                <div class='font-medium text-flexoki-tx truncate mb-1'>{job.filename}</div>
                                <div class='text-sm text-flexoki-tx-3 space-y-1'>
                                    <div>Created: {format_local_datetime(job.created_at)}</div>
                                    <div class='flex items-center space-x-4'>
                                        <span>Size: {file_info}</span>
                                        <span>Cost: {cost_info}</span>
                                    </div>
                                </div>
                            </div>
                            <div class='ml-4 flex-shrink-0'>
                                <span class='inline-flex items-center px-2.5 py-1 text-xs font-medium rounded-full border {status_color}'>
                                    {job.status.title()}
                                </span>
                            </div>
                        </div>
                    </li>
                """)
            
            jobs_content = f"<ul class='bg-flexoki-paper rounded-lg border border-flexoki-ui-3 overflow-hidden'>{''.join(job_items)}</ul>"
    
    list_view_html = f"""
        <div class='max-w-2xl mx-auto bg-white rounded-lg border border-flexoki-ui-3 p-6 shadow-sm'>
            <div class='flex justify-between items-center mb-4'>
                <h2 class='text-2xl font-semibold text-flexoki-tx'>Transcriptions</h2>
                <div class='text-right'>
                    <div class='text-sm text-flexoki-tx-3'>Total API Usage</div>
                    <div class='text-lg font-semibold text-flexoki-gr'>${user.total_api_cost:.2f}</div>
                </div>
            </div>
            
            <div class='space-y-4'>
                <form hx-post='/jobs/add' hx-target='#job-result' hx-swap='innerHTML' hx-encoding='multipart/form-data'
                      hx-on::before-request="showUploadProgress(event)"
                      hx-on::after-request="hideUploadProgress(event)">
                    <div class='space-y-4'>
                        <div class='w-full p-6 border border border-flexoki-ui-3 rounded-lg bg-flexoki-paper-light transition-colors duration-200'>
                            <div class='flex flex-col items-center justify-center space-y-4'>
                                <div class="text-4xl">🎧</div>
                                <label class='text-lg font-medium text-flexoki-tx'>Upload a new audio file →</label>
                                <input 
                                    type='file' 
                                    name='file' 
                                    id='file-input'
                                    accept='.wav,.mp3,audio/wav,audio/mpeg'
                                    required
                                    class='hover:shadow-lg transition-shadow duration-200 cursor-pointer border bg-flexoki-paper p-2 rounded-md text-sm text-flexoki-tx file:mr-4 file:py-3 file:px-4 file:rounded-lg file:border-0
                                    file:text-sm file:border-flexoki-ui-3 file:font-semibold file:bg-flexoki-bl file:text-white
                                    hover:file:bg-flexoki-bl-2 file:cursor-pointer'>
                                <p class='text-sm text-flexoki-tx-3/70'>Support for .wav and .mp3 files up to 100MB</p>

                                <!-- Key Terms Input -->
                                <div class='w-full pt-4'>
                                    <label class='block text-sm font-medium text-flexoki-tx mb-2'>Key Terms (optional)</label>
                                    <textarea
                                        name='keyterms'
                                        id='keyterms-input'
                                        placeholder='Enter key terms separated by commas (e.g., AI, machine learning, API)'
                                        rows='2'
                                        oninput='updateKeyTermsPills()'
                                        class='w-full p-3 border border-flexoki-ui-3 rounded-md text-sm text-flexoki-tx bg-flexoki-paper focus:outline-none focus:ring-2 focus:ring-flexoki-bl focus:border-transparent resize-none'></textarea>
                                    <!-- Pills container -->
                                    <div id='keyterms-pills' class='mt-2 flex flex-wrap gap-2 min-h-[24px]'></div>
                                    <p class='text-xs text-flexoki-tx-3/70 mt-1'>Help improve transcription accuracy for domain-specific terms</p>
                                </div>

                                <!-- Simple Upload Indicator -->
                                <div id='upload-indicator' class='hidden'>
                                    <div class='flex items-center justify-center space-x-2 text-flexoki-bl'>
                                        <div class='animate-spin rounded-full h-4 w-4 border-b-2 border-flexoki-bl'></div>
                                        <span class='text-sm font-medium'>Uploading file...</span>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <button 
                            type='submit'
                            id='upload-button'
                            class='w-full bg-flexoki-bl hover:bg-flexoki-bl-2 text-white font-semibold py-3 px-6 rounded-lg transition-colors duration-200 focus:outline-none focus:ring-2 focus:ring-flexoki-bl focus:ring-opacity-50'>
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
                    <div id='job-list'>
                        {jobs_content}
                    </div>
                </div>
            </div>
        </div>
        
        <script>
            function showUploadProgress(event) {{
                const indicator = document.getElementById('upload-indicator');
                const button = document.getElementById('upload-button');
                
                // Show upload indicator
                indicator.classList.remove('hidden');
                
                // Disable button and change text
                button.disabled = true;
                button.textContent = 'Uploading...';
                button.classList.add('opacity-75', 'cursor-not-allowed');
            }}
            
            function hideUploadProgress(event) {{
                const indicator = document.getElementById('upload-indicator');
                const button = document.getElementById('upload-button');

                // Hide upload indicator
                indicator.classList.add('hidden');

                // Re-enable button
                button.disabled = false;
                button.textContent = 'Upload & Create Job';
                button.classList.remove('opacity-75', 'cursor-not-allowed');
            }}

            function updateKeyTermsPills() {{
                const input = document.getElementById('keyterms-input');
                const pillsContainer = document.getElementById('keyterms-pills');

                if (!input || !pillsContainer) return;

                const text = input.value.trim();
                pillsContainer.innerHTML = '';

                if (text) {{
                    const terms = text.split(',').map(term => term.trim()).filter(term => term.length > 0);

                    terms.forEach(term => {{
                        const pill = document.createElement('span');
                        pill.className = 'inline-flex items-center px-2.5 py-1 text-xs font-medium rounded-full bg-flexoki-bl bg-opacity-10 text-flexoki-bl border border-flexoki-bl border-opacity-20';
                        pill.textContent = term;
                        pillsContainer.appendChild(pill);
                    }});
                }}
            }}
        </script>
    """
    
    return HTMLResponse(list_view_html)

@router.get("/jobs/{job_id}/download")
async def download_transcript(job_id: int, user: User = Depends(current_active_user)):
    # Local import to avoid circular import
    from main import engine, Job, JobStatus, load_transcript_from_file
    
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

@router.delete("/jobs/{job_id}")
async def delete_job(job_id: int, user: User = Depends(current_active_user)):
    """Delete a job and clean up associated files."""
    from main import engine, Job, JobStatus
    
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
        
        # Return success message that redirects to job list
        return HTMLResponse(f"""
            <div class='max-w-2xl mx-auto bg-white border border-green-200 p-6 rounded-lg text-center'>
                <div class='mb-4'>
                    <div class='text-green-600 font-semibold text-lg mb-2'>✅ Job Deleted Successfully</div>
                    <div class='text-sm text-gray-600'>
                        <div>Job: {job.filename}</div>
                        {f"<div>Cleaned up: {', '.join(files_deleted)}</div>" if files_deleted else ""}
                    </div>
                </div>
                <button 
                    hx-get="/jobs/list/view" 
                    hx-target="#main-content" 
                    hx-swap="innerHTML"
                    class='bg-blue-500 hover:bg-blue-600 text-white font-semibold py-2 px-4 rounded transition duration-300'>
                    Back to Jobs
                </button>
            </div>
        """)