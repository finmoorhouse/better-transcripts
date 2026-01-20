from fastapi import FastAPI, Request, Form, File, UploadFile, HTTPException, BackgroundTasks, Depends
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from sqlmodel import SQLModel, create_engine, Session, select
from typing import Optional
from datetime import datetime, timezone
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
import openai
from google import genai
import markdown

# Import models
from models import Job, JobStatus

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s:%(name)s:%(message)s'
)
logger = logging.getLogger(__name__)

# Reduce HTTP logging verbosity to WARNING to reduce log clutter
# (AssemblyAI polls every 10 seconds, which creates a lot of INFO logs)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


# Load environment variables
load_dotenv()

# Configure AssemblyAI API
aai.settings.api_key = os.getenv("ASSEMBLY_KEY")
# Set polling interval to 10 seconds (default is 3 seconds) to reduce API calls and log clutter
aai.settings.polling_interval = 10.0
transcriber = aai.Transcriber()

# Configure OpenAI API
openai.api_key = os.getenv("OPENAI_API_KEY")
openai_client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Configure Gemini API
gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# Model processing speed tracking (seconds per input word)
# Priors based on experimentation - will be refined during processing
MODEL_SPEED_PRIORS = {
    "gpt-5-mini": 0.008,      # ~125 words/sec
    "gemini-2.5-flash": 0.005  # ~200 words/sec (faster)
}

# Runtime speed estimates (updated as chunks complete)
# Format: {job_id: {"samples": [(words, seconds), ...], "estimate": float}}
job_speed_estimates = {}

def get_time_estimate(job_id: int, word_count: int, model: str) -> float:
    """Get estimated processing time for a chunk based on prior + learned data."""
    prior = MODEL_SPEED_PRIORS.get(model, 0.007)

    if job_id in job_speed_estimates and job_speed_estimates[job_id]["samples"]:
        # Use weighted average of prior and observed data
        samples = job_speed_estimates[job_id]["samples"]
        total_words = sum(w for w, _ in samples)
        total_time = sum(t for _, t in samples)
        observed_rate = total_time / total_words if total_words > 0 else prior

        # Weight observed data more as we get more samples
        weight = min(len(samples) / 3, 1.0)  # Full weight after 3 chunks
        estimate = (weight * observed_rate) + ((1 - weight) * prior)
    else:
        estimate = prior

    return word_count * estimate

def record_chunk_timing(job_id: int, word_count: int, elapsed_time: float):
    """Record timing data from a completed chunk to improve estimates."""
    if job_id not in job_speed_estimates:
        job_speed_estimates[job_id] = {"samples": [], "estimate": 0}

    job_speed_estimates[job_id]["samples"].append((word_count, elapsed_time))

    # Log the observed rate
    rate = elapsed_time / word_count if word_count > 0 else 0
    logger.info(f"Job {job_id} chunk timing: {word_count} words in {elapsed_time:.2f}s ({rate:.4f}s/word)")

def cleanup_job_estimates(job_id: int):
    """Clean up speed estimates when job completes."""
    if job_id in job_speed_estimates:
        del job_speed_estimates[job_id]

# File upload constants
UPLOAD_DIR = "./uploads"
TRANSCRIPT_DIR = "./transcripts"
RAW_TRANSCRIPT_DIR = "./raw_transcripts"
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB in bytes
ALLOWED_EXTENSIONS = {".wav", ".mp3"}

# Ensure directories exist
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(TRANSCRIPT_DIR, exist_ok=True)
os.makedirs(RAW_TRANSCRIPT_DIR, exist_ok=True)

# Database setup
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./test.db")
engine = create_engine(DATABASE_URL)

def create_db_and_tables():
    try:
        SQLModel.metadata.create_all(engine, checkfirst=True)
        logger.info("Database tables created/verified successfully")
    except Exception as e:
        logger.error(f"Database creation failed: {e}")
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

def update_job_progress(job_id: int, message: str):
    """Update the progress message for a job."""
    with Session(engine) as session:
        job = session.get(Job, job_id)
        if job:
            job.progress_message = message
            session.commit()
            logger.info(f"Job {job_id} progress: {message}")

def load_transcript_from_file(file_path: str) -> str:
    """Load transcript text from .md file."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""

def extract_speakers_from_transcript(transcript_text: str) -> list[str]:
    """Extract unique speaker labels from transcript using regex."""
    import re
    # Match speaker labels in format **SpeakerName**:
    pattern = r'\*\*([^*]+)\*\*:'
    matches = re.findall(pattern, transcript_text)
    # Return unique speakers in order of first appearance
    seen = set()
    unique_speakers = []
    for speaker in matches:
        if speaker not in seen:
            seen.add(speaker)
            unique_speakers.append(speaker)
    return unique_speakers

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
        # Convert markdown to HTML with extensions
        return markdown.markdown(markdown_text, extensions=['extra', 'toc'])
    except json.JSONDecodeError:
        # If not JSON, assume it's already markdown and convert to HTML with extensions
        return markdown.markdown(transcript_text, extensions=['extra', 'toc'])



def strip_file_extension(filename: str) -> str:
    """Remove file extension from filename for display."""
    if '.' in filename:
        return filename.rsplit('.', 1)[0]
    return filename

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
                # Convert milliseconds to HH:MM:SS format
                total_seconds = utterance.start // 1000
                hours = total_seconds // 3600
                minutes = (total_seconds % 3600) // 60
                seconds = total_seconds % 60
                timestamp = f"[{hours:02d}:{minutes:02d}:{seconds:02d}]"

                speaker_text = f"{timestamp} **{utterance.speaker}**: {utterance.text.strip()}"
                markdown_lines.append(speaker_text)
        else:
            # Fallback to full transcript without speaker info
            speaker_text = f"[00:00:00] **Speaker A**: {transcript.text}"
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

def chunk_transcript(transcript_text: str, max_words: int = 800) -> list[dict]:
    """
    Split transcript into chunks, prioritizing speaker boundaries and sentence boundaries.

    Args:
        transcript_text: The full transcript text
        max_words: Maximum words per chunk

    Returns:
        List of dicts with 'text' and 'new_speaker' keys
    """
    words = transcript_text.split()
    total_words = len(words)
    logger.info(f"Chunking transcript: {total_words} total words, max_words={max_words}")

    if total_words <= max_words:
        logger.info(f"Transcript fits in single chunk ({total_words} <= {max_words})")
        return [{"text": transcript_text, "new_speaker": True}]

    chunks = []
    current_chunk = []
    current_word_count = 0

    # Split into lines (each line should be a speaker segment)
    lines = transcript_text.strip().split('\n')
    logger.info(f"Split into {len(lines)} lines")
    for i, line in enumerate(lines[:3]):  # Log first 3 lines for debugging
        logger.info(f"Line {i+1}: '{line}' ({len(line.split())} words)")

    for line in lines:
        line = line.strip()
        if not line:
            continue

        line_words = line.split()
        line_word_count = len(line_words)

        # If adding this line would exceed max_words and we have content, save current chunk
        if current_word_count + line_word_count > max_words and current_chunk:
            # Save current chunk (new speaker since it's a line boundary)
            chunks.append({"text": '\n'.join(current_chunk), "new_speaker": True})
            current_chunk = []
            current_word_count = 0

        # If a single line is longer than max_words, split it at sentence boundaries
        if line_word_count > max_words:
            logger.info(f"Line too long ({line_word_count} words), splitting at sentences")
            # Split long speaker segments at sentence boundaries
            sentences = line.split('. ')

            first_sentence_in_line = True
            for i, sentence in enumerate(sentences):
                sentence = sentence.strip()
                if not sentence:
                    continue

                # Add period back except for last sentence
                if i < len(sentences) - 1 and not sentence.endswith('.'):
                    sentence += '.'

                sentence_words = sentence.split()

                # If current chunk + this sentence would exceed max_words, save current chunk
                if current_word_count + len(sentence_words) > max_words and current_chunk:
                    # First sentence in line starts new speaker, others are continuations
                    chunks.append({"text": '\n'.join(current_chunk), "new_speaker": first_sentence_in_line})
                    current_chunk = []
                    current_word_count = 0
                    first_sentence_in_line = False

                # Add this sentence to current chunk
                current_chunk.append(sentence)
                current_word_count += len(sentence_words)
        else:
            # Add the whole line
            current_chunk.append(line)
            current_word_count += line_word_count

    # Add final chunk if it has content
    if current_chunk:
        chunks.append({"text": '\n'.join(current_chunk), "new_speaker": True})

    logger.info(f"Chunking complete: created {len(chunks)} chunks")
    for i, chunk in enumerate(chunks):
        chunk_word_count = len(chunk["text"].split())
        new_speaker_flag = chunk["new_speaker"]
        logger.info(f"Chunk {i+1}: {chunk_word_count} words, new_speaker={new_speaker_flag}")

    return chunks

def generate_chapters_with_gpt5(transcript_text: str) -> str:
    """Generate chapter markers from the full transcript using GPT-5."""
    try:
        logger.info("Generating chapters with GPT-5")
        start_time = time.time()

        system_prompt = """You are an expert at analyzing transcripts and creating chapter markers.
Your task is to analyze the entire transcript and generate a list of chapters that help readers navigate the content.

Requirements:
- Create chapter markers at natural topic boundaries
- Aim for approximately one chapter per 10-20 minutes of speech
- Each chapter should have a timestamp (based on the timestamps in the transcript) and a short descriptive phrase
- Format each chapter as: - (HH:MM:SS) A short phrase summarising this section of text
- The phrase should be concise (5-8 words maximum) and descriptive
- Chapters should reflect major topic changes or segments

Return ONLY the chapter list in the specified markdown format, with no additional text or explanation."""

        response = openai_client.chat.completions.create(
            model="gpt-5-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": transcript_text}
            ],
        )

        chapters = response.choices[0].message.content
        elapsed = time.time() - start_time
        logger.info(f"Chapters generated in {elapsed:.2f}s")
        logger.info("Chapters generated successfully")
        return chapters

    except Exception as e:
        logger.error(f"Error generating chapters with GPT-5: {str(e)}")
        return ""

def process_transcript_with_gpt5(transcript_text: str, custom_instructions: Optional[str] = None, job_id: Optional[int] = None) -> str:
    """Process the transcript with GPT-5 for language processing and enhancement."""
    try:
        logger.info("Starting GPT-5 processing of transcript")

        # Determine if we should generate chapters
        # Only generate if there's more than one speaker and transcript is long enough
        word_count = len(transcript_text.split())
        speaker_count = len(set([line.split('**')[1].split('**')[0] for line in transcript_text.split('\n') if '**' in line]))
        should_generate_chapters = speaker_count > 1 and word_count > 500

        logger.info(f"Word count: {word_count}, Speaker count: {speaker_count}, Generate chapters: {should_generate_chapters}")

        # First, generate chapters from the full transcript if conditions are met
        chapters = ""
        if should_generate_chapters:
            if job_id:
                update_job_progress(job_id, "Generating chapter markers...")
            chapters = generate_chapters_with_gpt5(transcript_text)

        # Check if transcript needs chunking
        chunks = chunk_transcript(transcript_text, max_words=1000) #Max words is best set at around 800 to keep the chunks small enough for GPT-5
        logger.info(f"Split transcript into {len(chunks)} chunks")

        # Build system prompt with optional chapter context
        system_prompt_parts = ["You are an expert transcript editor. Your task is to process the transcript according to the following instructions. Please:"]

        if chapters:
            system_prompt_parts.append(f"\n## Context\nThe following is a summary of chapters from the overall transcript:\n\n{chapters}\n")
            system_prompt_parts.append("You are processing this transcript in chunks. Each chunk will indicate which chunk number it is.")
            system_prompt_parts.append("If you notice that a timestamp in the chunk exactly matches a chapter timestamp from the list above, add a markdown H3 header (###) with the chapter title just before that speaker's line.\n")

        # Shared instructions for all transcripts
        system_prompt_parts.append("""
- If the below doesn't apply, do not change the original wording. You are to be a subtle editor. Most text should remain identical — do not change things for the sake of it.
- If there are obvious transcription errors given the overall context, fix them. Similarly, improve punctuation and capitalization where appropriate.
- Clean up filler words (um, uh, like) and false starts, but preserve natural speech patterns.
- Be careful about accidentally including punctuation that will get interpreted by the markdown parser. For example, a numeral followed by a period will be interpreted as a list item. You may italicise words with asterisks.
- In some cases, a speaker will mention a concept or noun where it would be useful to add a link. In this case, add a markdown-formatted link (Example: [Concept](https://www.example.com)). Only when you are confident of the correct link. Favour links to credible sources, such as SEP, Wikipedia, Epoch AI, Our World in Data, etc.
- In general, you should feel more comfortable cutting obvious mistakes or filler words or fragments of speech which trail off, and slightly less comfortable overtly adding or changing words, even if the meaning is preserved. Exceptions include where the speaker obviously meant to use a different word, or some simple connective words were skipped over, but properly speaking would have been used.
""")

        # Speaker-specific instructions
        if speaker_count == 1:
            system_prompt_parts.append("""
## Single Speaker Format
- The very first line of the first chunk you receive will begin with a timestamp, followed by a speaker label. Please delete BOTH the timestamp AND the speaker label (since there is only one speaker).
- Break the text into natural paragraphs based on topic changes. Add a blank line between paragraphs.
- Don't add bolded text anywhere (this may break important regex parsing).

Return only the improved transcript as plain paragraphs with no timestamps or speaker labels.""")
        else:
            system_prompt_parts.append("""
## Multiple Speaker Format
- In some cases, the transcription will incorrectly assume the speaker has changed when it clearly hasn't, creating many lines of few words. In these cases, you can simply delete the new speaker indication altogether when it seems out of place. For example, "**A**: What do — [new line] **B**: You think? [new line] **A**: About this?" should become "**A**: What do you think about this?". Only do this if it makes obvious sense to do so.
- You will receive one (potentially long) line of text per speaker. If a new topic begins, add a full line break (leaving a blank line) to start a new paragraph.
- Each new line begins with a timestamp, followed by a speaker label in bold, followed by a colon, followed by the text. Please always delete the timestamp, so the line begins with the speaker label.
- Speaker labels are letters given in bold, like **A**:. Maintain all speaker labels *exactly* as provided, even if you can infer the true name of the speaker. Don't add in any brackets or extra whitespace. The colon should always remain outside the speaker name, i.e. **A**: not **A:**.
- Each new speaker must always begin on a new line, separated by a blank line (but remember to always delete the timestamp, so the line begins with the speaker label).
- Don't add bolded text anywhere outside of the speaker labels (this may break important regex parsing).

Return only the improved transcript, maintaining the same format with **Speaker**: text structure (no timestamps).""")

        if custom_instructions:
            system_prompt_parts.append(f"\n## Custom Instructions\nThe user also provided the following custom instructions:\n\n{custom_instructions}\n")

        system_prompt = "".join(system_prompt_parts)

        processed_chunks = []

        for i, chunk_data in enumerate(chunks):
            # Check if job was deleted before processing each chunk
            if job_id is not None:
                with Session(engine) as session:
                    job = session.get(Job, job_id)
                    if not job:
                        logger.info(f"Job {job_id} was deleted during GPT processing. Stopping at chunk {i + 1}/{len(chunks)}.")
                        raise Exception("Job was deleted by user")
                # Update progress for each chunk
                update_job_progress(job_id, f"Processing with GPT-5-mini: chunk {i + 1} of {len(chunks)}...")

            chunk_text = chunk_data["text"]
            is_new_speaker = chunk_data["new_speaker"]
            chunk_word_count = len(chunk_text.split())
            logger.info(f"Processing chunk {i + 1}/{len(chunks)} ({chunk_word_count} words, new_speaker={is_new_speaker})")

            # Prepare user message with chunk context
            user_message = chunk_text
            if len(chunks) > 1:
                user_message = f"[Chunk {i + 1} of {len(chunks)}]\n\n{chunk_text}"

            try:
                chunk_start_time = time.time()
                estimated_time = get_time_estimate(job_id, chunk_word_count, "gpt-5-mini") if job_id else chunk_word_count * 0.008

                # Use streaming to show progress during processing
                stream = openai_client.chat.completions.create(
                    model="gpt-5-mini",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message}
                    ],
                    stream=True,
                )

                # Collect streamed response and update progress periodically
                collected_content = []
                last_progress_update = time.time()
                for stream_chunk in stream:
                    if stream_chunk.choices[0].delta.content:
                        collected_content.append(stream_chunk.choices[0].delta.content)

                    # Update progress every 1 second
                    now = time.time()
                    if job_id and (now - last_progress_update) >= 1.0:
                        elapsed = now - chunk_start_time
                        if estimated_time > 0:
                            progress_pct = min(int((elapsed / estimated_time) * 100), 99)
                            update_job_progress(job_id, f"Chunk {i + 1}/{len(chunks)}: {elapsed:.0f}s elapsed (~{estimated_time:.0f}s expected, {progress_pct}%)")
                        else:
                            update_job_progress(job_id, f"Chunk {i + 1}/{len(chunks)}: {elapsed:.0f}s elapsed...")
                        last_progress_update = now

                chunk_elapsed = time.time() - chunk_start_time
                processed_chunk = "".join(collected_content)
                processed_chunks.append({"text": processed_chunk, "new_speaker": is_new_speaker})

                # Record timing for future estimates
                if job_id:
                    record_chunk_timing(job_id, chunk_word_count, chunk_elapsed)

                logger.info(f"Chunk {i + 1}/{len(chunks)} processed in {chunk_elapsed:.2f}s ({chunk_word_count} words)")

            except Exception as chunk_error:
                logger.error(f"Error processing chunk {i + 1}: {str(chunk_error)}")
                # Fall back to original chunk if processing fails
                processed_chunks.append({"text": chunk_text, "new_speaker": is_new_speaker})
                logger.warning(f"Using original content for chunk {i + 1}")

        # Combine all processed chunks with appropriate spacing
        final_parts = []
        for i, chunk_data in enumerate(processed_chunks):
            if i == 0:
                # First chunk always gets added
                final_parts.append(chunk_data["text"])
            elif chunk_data["new_speaker"]:
                # New speaker gets double newline
                final_parts.append("\n\n" + chunk_data["text"])
            else:
                # Same speaker continuation gets single space
                final_parts.append(" " + chunk_data["text"])

        final_transcript = "".join(final_parts)

        # Add chapters at the beginning if they were generated
        if chapters:
            final_transcript = f"## Chapters\n\n{chapters}\n\n---\n\n{final_transcript}"

        logger.info("GPT-5 processing completed successfully")

        return final_transcript

    except Exception as e:
        logger.error(f"Error processing transcript with GPT-5: {str(e)}")
        # Return original transcript if GPT processing fails
        logger.warning("Falling back to original transcript due to GPT processing error")
        return transcript_text


def generate_chapters_with_gemini(transcript_text: str) -> str:
    """Generate chapter markers from the full transcript using Gemini."""
    try:
        logger.info("Generating chapters with Gemini")
        start_time = time.time()

        system_prompt = """You are an expert at analyzing transcripts and creating chapter markers.
Your task is to analyze the entire transcript and generate a list of chapters that help readers navigate the content.

Requirements:
- Create chapter markers at natural topic boundaries
- Aim for approximately one chapter per 10-20 minutes of speech
- Each chapter should have a timestamp (based on the timestamps in the transcript) and a short descriptive phrase
- Format each chapter as: - (HH:MM:SS) A short phrase summarising this section of text
- The phrase should be concise (5-8 words maximum) and descriptive
- Chapters should reflect major topic changes or segments

Return ONLY the chapter list in the specified markdown format, with no additional text or explanation."""

        response = gemini_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[system_prompt, transcript_text]
        )
        elapsed = time.time() - start_time
        logger.info(f"Chapters generated in {elapsed:.2f}s")
        chapters = response.text
        logger.info("Chapters generated successfully with Gemini")
        return chapters

    except Exception as e:
        logger.error(f"Error generating chapters with Gemini: {str(e)}")
        return ""


def process_transcript_with_gemini(transcript_text: str, custom_instructions: Optional[str] = None, job_id: Optional[int] = None) -> str:
    """Process the transcript with Gemini for language processing and enhancement."""
    try:
        logger.info("Starting Gemini processing of transcript")

        # Determine if we should generate chapters
        word_count = len(transcript_text.split())
        speaker_count = len(set([line.split('**')[1].split('**')[0] for line in transcript_text.split('\n') if '**' in line]))
        should_generate_chapters = speaker_count > 1 and word_count > 500

        logger.info(f"Word count: {word_count}, Speaker count: {speaker_count}, Generate chapters: {should_generate_chapters}")

        # First, generate chapters from the full transcript if conditions are met
        chapters = ""
        if should_generate_chapters:
            if job_id:
                update_job_progress(job_id, "Generating chapter markers...")
            chapters = generate_chapters_with_gemini(transcript_text)

        # Check if transcript needs chunking
        chunks = chunk_transcript(transcript_text, max_words=1000)
        logger.info(f"Split transcript into {len(chunks)} chunks")

        # Build system prompt with optional chapter context
        system_prompt_parts = ["You are an expert transcript editor. Your task is to process the transcript according to the following instructions. Please:"]

        if chapters:
            system_prompt_parts.append(f"\n## Context\nThe following is a summary of chapters from the overall transcript:\n\n{chapters}\n")
            system_prompt_parts.append("You are processing this transcript in chunks. Each chunk will indicate which chunk number it is.")
            system_prompt_parts.append("If you notice that a timestamp in the chunk exactly matches a chapter timestamp from the list above, add a markdown H3 header (###) with the chapter title just before that speaker's line.\n")

        # Shared instructions for all transcripts
        system_prompt_parts.append("""
- If the below doesn't apply, do not change the original wording. You are to be a subtle editor. Most text should remain identical — do not change things for the sake of it.
- If there are obvious transcription errors given the overall context, fix them. Similarly, improve punctuation and capitalization where appropriate.
- Clean up filler words (um, uh, like) and false starts, but preserve natural speech patterns.
- Be careful about accidentally including punctuation that will get interpreted by the markdown parser. For example, a numeral followed by a period will be interpreted as a list item. You may italicise words with asterisks.
- In some cases, a speaker will mention a concept or noun where it would be useful to add a link. In this case, add a markdown-formatted link (Example: [Concept](https://www.example.com)). Only when you are confident of the correct link. Favour links to credible sources, such as SEP, Wikipedia, Epoch AI, Our World in Data, etc.
- In general, you should feel more comfortable cutting obvious mistakes or filler words or fragments of speech which trail off, and slightly less comfortable overtly adding or changing words, even if the meaning is preserved. Exceptions include where the speaker obviously meant to use a different word, or some simple connective words were skipped over, but properly speaking would have been used.
""")

        # Speaker-specific instructions
        if speaker_count == 1:
            system_prompt_parts.append("""
## Single Speaker Format
- The very first line of the first chunk you receive will begin with a timestamp, followed by a speaker label. Please delete BOTH the timestamp AND the speaker label (since there is only one speaker).
- Break the text into natural paragraphs based on topic changes. Add a blank line between paragraphs.
- Don't add bolded text anywhere (this may break important regex parsing).

Return only the improved transcript as plain paragraphs with no timestamps or speaker labels.""")
        else:
            system_prompt_parts.append("""
## Multiple Speaker Format
- In some cases, the transcription will incorrectly assume the speaker has changed when it clearly hasn't, creating many lines of few words. In these cases, you can simply delete the new speaker indication altogether when it seems out of place. For example, "**A**: What do — [new line] **B**: You think? [new line] **A**: About this?" should become "**A**: What do you think about this?". Only do this if it makes obvious sense to do so.
- You will receive one (potentially long) line of text per speaker. If a new topic begins, add a full line break (leaving a blank line) to start a new paragraph.
- Each new line begins with a timestamp, followed by a speaker label in bold, followed by a colon, followed by the text. Please always delete the timestamp, so the line begins with the speaker label.
- Speaker labels are letters given in bold, like **A**:. Maintain all speaker labels *exactly* as provided, even if you can infer the true name of the speaker. Don't add in any brackets or extra whitespace. The colon should always remain outside the speaker name, i.e. **A**: not **A:**.
- Each new speaker must always begin on a new line, separated by a blank line (but remember to always delete the timestamp, so the line begins with the speaker label).
- Don't add bolded text anywhere outside of the speaker labels (this may break important regex parsing).

Return only the improved transcript, maintaining the same format with **Speaker**: text structure (no timestamps).""")

        if custom_instructions:
            system_prompt_parts.append(f"\n## Custom Instructions\nThe user also provided the following custom instructions:\n\n{custom_instructions}\n")

        system_prompt = "".join(system_prompt_parts)

        processed_chunks = []

        for i, chunk_data in enumerate(chunks):
            # Check if job was deleted before processing each chunk
            if job_id is not None:
                with Session(engine) as session:
                    job = session.get(Job, job_id)
                    if not job:
                        logger.info(f"Job {job_id} was deleted during Gemini processing. Stopping at chunk {i + 1}/{len(chunks)}.")
                        raise Exception("Job was deleted by user")
                # Update progress for each chunk
                update_job_progress(job_id, f"Processing with Gemini: chunk {i + 1} of {len(chunks)}...")

            chunk_text = chunk_data["text"]
            is_new_speaker = chunk_data["new_speaker"]
            chunk_word_count = len(chunk_text.split())
            logger.info(f"Processing chunk {i + 1}/{len(chunks)} with Gemini ({chunk_word_count} words, new_speaker={is_new_speaker})")

            # Prepare user message with chunk context
            user_message = chunk_text
            if len(chunks) > 1:
                user_message = f"[Chunk {i + 1} of {len(chunks)}]\n\n{chunk_text}"

            try:
                chunk_start_time = time.time()
                estimated_time = get_time_estimate(job_id, chunk_word_count, "gemini-2.5-flash") if job_id else chunk_word_count * 0.005

                # Use streaming to show progress during processing
                stream = gemini_client.models.generate_content_stream(
                    model="gemini-2.5-flash",
                    contents=[system_prompt, user_message]
                )

                # Collect streamed response and update progress periodically
                collected_content = []
                last_progress_update = time.time()
                for stream_chunk in stream:
                    if stream_chunk.text:
                        collected_content.append(stream_chunk.text)

                    # Update progress every 1 second
                    now = time.time()
                    if job_id and (now - last_progress_update) >= 1.0:
                        elapsed = now - chunk_start_time
                        if estimated_time > 0:
                            progress_pct = min(int((elapsed / estimated_time) * 100), 99)
                            update_job_progress(job_id, f"Chunk {i + 1}/{len(chunks)}: {elapsed:.0f}s elapsed (~{estimated_time:.0f}s expected, {progress_pct}%)")
                        else:
                            update_job_progress(job_id, f"Chunk {i + 1}/{len(chunks)}: {elapsed:.0f}s elapsed...")
                        last_progress_update = now

                chunk_elapsed = time.time() - chunk_start_time
                processed_chunk = "".join(collected_content)
                processed_chunks.append({"text": processed_chunk, "new_speaker": is_new_speaker})

                # Record timing for future estimates
                if job_id:
                    record_chunk_timing(job_id, chunk_word_count, chunk_elapsed)

                logger.info(f"Chunk {i + 1}/{len(chunks)} processed in {chunk_elapsed:.2f}s ({chunk_word_count} words)")

            except Exception as chunk_error:
                logger.error(f"Error processing chunk {i + 1} with Gemini: {str(chunk_error)}")
                processed_chunks.append({"text": chunk_text, "new_speaker": is_new_speaker})
                logger.warning(f"Using original content for chunk {i + 1}")

        # Combine all processed chunks with appropriate spacing
        final_parts = []
        for i, chunk_data in enumerate(processed_chunks):
            if i == 0:
                final_parts.append(chunk_data["text"])
            elif chunk_data["new_speaker"]:
                final_parts.append("\n\n" + chunk_data["text"])
            else:
                final_parts.append(" " + chunk_data["text"])

        final_transcript = "".join(final_parts)

        # Add chapters at the beginning if they were generated
        if chapters:
            final_transcript = f"## Chapters\n\n{chapters}\n\n---\n\n{final_transcript}"

        logger.info("Gemini processing completed successfully")

        return final_transcript

    except Exception as e:
        logger.error(f"Error processing transcript with Gemini: {str(e)}")
        logger.warning("Falling back to original transcript due to Gemini processing error")
        return transcript_text


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
        custom_instructions = job.custom_instructions
        llm_model = job.llm_model or "gemini-2.5-flash"

    try:
        logger.info(f"Starting AssemblyAI transcription for job {job_id}: {filename}")
        update_job_progress(job_id, "Transcribing audio with AssemblyAI...")

        # Parse keyterms from comma-separated string to list
        keyterms = None
        if keyterms_str and keyterms_str.strip():
            keyterms = [term.strip() for term in keyterms_str.split(',') if term.strip()]

        # Process the entire audio file with AssemblyAI (no chunking needed)
        raw_transcript_text = process_audio_with_assemblyai(file_path, keyterms=keyterms)
        word_count = len(raw_transcript_text.split())
        logger.info(f"AssemblyAI transcription completed for job {job_id}. Total word count: {word_count}")
        update_job_progress(job_id, f"Transcription complete ({word_count} words). Starting LLM processing...")

        # Check if job was deleted after AssemblyAI transcription
        with Session(engine) as session:
            job = session.get(Job, job_id)
            if not job:
                logger.info(f"Job {job_id} was deleted during processing. Stopping.")
                return

        # In development mode, save the raw transcript for debugging
        environment = os.getenv("ENVIRONMENT", "production")
        if environment == "development":
            raw_filename = f"raw_transcript_job_{job_id}.md"
            raw_file_path = os.path.join(RAW_TRANSCRIPT_DIR, raw_filename)
            with open(raw_file_path, "w", encoding="utf-8") as f:
                f.write(raw_transcript_text)
            logger.info(f"Saved raw transcript for debugging: {raw_file_path}")

        # Process transcript with selected LLM for language enhancement
        logger.info(f"Job {job_id} llm_model value: '{llm_model}' (type: {type(llm_model).__name__})")
        if llm_model in ("gemini-2.5-flash", "gemini-3.0-flash"):  # Support both old and new values
            logger.info(f"Using Gemini 2.5 Flash for job {job_id}")
            update_job_progress(job_id, "Processing transcript with Gemini 2.5 Flash...")
            final_transcript_text = process_transcript_with_gemini(raw_transcript_text, custom_instructions=custom_instructions, job_id=job_id)
        else:
            logger.info(f"Using GPT-5-mini for job {job_id}")
            update_job_progress(job_id, "Processing transcript with GPT-5-mini...")
            final_transcript_text = process_transcript_with_gpt5(raw_transcript_text, custom_instructions=custom_instructions, job_id=job_id)
        logger.info(f"LLM processing completed for job {job_id}")
        update_job_progress(job_id, "Finalizing transcript...")

        # Save final processed transcript to file
        transcript_file_path = save_transcript_to_file(job_id, final_transcript_text)
        
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

        # Clean up speed estimates for this job
        cleanup_job_estimates(job_id)

    except Exception as e:
        logger.error(f"Transcription failed for job {job_id}: {str(e)}")
        # Clean up speed estimates on failure too
        cleanup_job_estimates(job_id)
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

# Custom exception handler for 401 Unauthorized
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 401:
        # Check if this is an HTMX request
        is_htmx_request = request.headers.get("HX-Request") == "true"

        unauthorized_html = """
            <div class="max-w-md mx-auto bg-flexoki-paper-light rounded-lg border border-flexoki-ui-3 p-6 text-center shadow-sm">
                <h2 class="text-2xl font-semibold mb-2 text-flexoki-tx text-left">Authentication Required</h2>
                <p class="text-flexoki-tx-3 mb-6 text-left">You need to be logged in to access this page.</p>
                <div class="space-y-3">
                    <a href="/login" class="block w-full bg-flexoki-bl hover:bg-flexoki-bl-2 text-flexoki-paper font-bold py-2 px-4 rounded transition duration-300">
                        Login
                    </a>
                    <a href="/register" class="block w-full bg-flexoki-or hover:bg-flexoki-or-2 text-flexoki-paper font-bold py-2 px-4 rounded transition duration-300">
                        Register
                    </a>
                </div>
            </div>
        """

        if is_htmx_request:
            # For HTMX requests, return just the fragment
            return HTMLResponse(content=unauthorized_html, status_code=401)
        else:
            # For direct browser visits, return full page
            from fastapi.templating import Jinja2Templates
            templates = Jinja2Templates(directory="templates")
            return templates.TemplateResponse(
                "unauthorized.html",
                {"request": request},
                status_code=401
            )

    # For other HTTP exceptions, use default JSON response
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail}
    )

@app.on_event("startup")
def on_startup():
    # Import User model here to ensure it's registered before creating tables
    from auth import User
    create_db_and_tables()

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Import authentication after app creation to avoid circular imports
from auth import auth_backend, fastapi_users, current_active_user, User, UserRead, UserCreate

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
    # Check if we're in development or production
    environment = os.getenv("ENVIRONMENT", "production")
    is_dev = environment == "development"
    logger.info(f"Starting server in {environment} mode (reload={is_dev})")
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=is_dev)

if __name__ == "__main__":
    main()
