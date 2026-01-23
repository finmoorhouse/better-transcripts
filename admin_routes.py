from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select, func
from datetime import datetime
import logging
import os

from auth import User, current_active_user
from models import Job

# Initialize router
router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="templates")

# Set up logging
logger = logging.getLogger(__name__)


async def get_current_superuser(user: User = Depends(current_active_user)) -> User:
    """Dependency that requires the current user to be a superuser."""
    if not user.is_superuser:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def get_engine():
    from main import engine
    return engine


def format_cost(cost: float) -> str:
    """Format cost as currency string."""
    if cost is None:
        return "$0.00"
    return f"${cost:.2f}"


def format_date(dt: datetime) -> str:
    """Format datetime for display."""
    if dt is None:
        return ""
    return dt.strftime('%Y-%m-%d %H:%M')


@router.get("", response_class=HTMLResponse)
async def admin_dashboard(request: Request, user: User = Depends(get_current_superuser)):
    """Admin dashboard showing all users."""
    logger.info(f"Admin dashboard accessed by {user.email}")

    engine = get_engine()
    with Session(engine) as session:
        # Get all users with job counts and total costs
        users = session.exec(select(User)).all()

        user_data = []
        for u in users:
            # Count jobs for this user
            job_count = session.exec(
                select(func.count(Job.id)).where(Job.user_id == u.id)
            ).one()

            user_data.append({
                "user": u,
                "job_count": job_count,
                "total_cost": format_cost(u.total_api_cost),
            })

    # Check if this is an HTMX request
    is_htmx = request.headers.get("HX-Request") == "true"

    if is_htmx:
        return templates.TemplateResponse(
            "partials/admin_dashboard.html",
            {"request": request, "users": user_data, "current_user": user}
        )
    else:
        return templates.TemplateResponse(
            "admin_page.html",
            {"request": request, "users": user_data, "current_user": user}
        )


@router.get("/users/{user_id}/jobs", response_class=HTMLResponse)
async def admin_user_jobs(
    request: Request,
    user_id: str,
    admin: User = Depends(get_current_superuser)
):
    """View all jobs for a specific user."""
    import uuid

    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID")

    engine = get_engine()
    with Session(engine) as session:
        # Get the user
        user = session.get(User, user_uuid)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Get all jobs for this user
        jobs = session.exec(
            select(Job).where(Job.user_id == user_uuid).order_by(Job.created_at.desc())
        ).all()

        # Format job data
        job_data = []
        for job in jobs:
            # Determine status color
            status_colors = {
                "pending": "bg-flexoki-ye-2/20 text-flexoki-ye border-flexoki-ye-2",
                "processing": "bg-flexoki-bl-2/20 text-flexoki-bl border-flexoki-bl-2",
                "completed": "bg-flexoki-gr-2/20 text-flexoki-gr border-flexoki-gr-2",
                "failed": "bg-flexoki-re-2/20 text-flexoki-re border-flexoki-re-2",
            }
            status_color = status_colors.get(job.status.value, "bg-flexoki-ui-2 text-flexoki-tx-3 border-flexoki-ui-3")

            job_data.append({
                "job": job,
                "display_name": job.filename.rsplit('.', 1)[0] if '.' in job.filename else job.filename,
                "status_color": status_color,
                "created_date": format_date(job.created_at),
                "completed_date": format_date(job.completed_at) if job.completed_at else "-",
                "cost": format_cost(job.api_cost) if job.api_cost else "-",
            })

        user_info = {
            "user": user,
            "job_count": len(jobs),
            "total_cost": format_cost(user.total_api_cost),
        }

    return templates.TemplateResponse(
        "partials/admin_user_jobs.html",
        {"request": request, "user_info": user_info, "jobs": job_data, "current_user": admin}
    )


@router.delete("/users/{user_id}", response_class=HTMLResponse)
async def admin_delete_user(
    request: Request,
    user_id: str,
    admin: User = Depends(get_current_superuser)
):
    """Delete a user and all their associated files and jobs."""
    import uuid

    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID")

    # Prevent admin from deleting themselves
    if user_uuid == admin.id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")

    engine = get_engine()
    with Session(engine) as session:
        # Get the user
        user = session.get(User, user_uuid)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        user_email = user.email

        # Get all jobs for this user
        jobs = session.exec(select(Job).where(Job.user_id == user_uuid)).all()

        # Delete associated files
        files_deleted = 0
        for job in jobs:
            # Delete audio file if exists
            if job.file_path and os.path.exists(job.file_path):
                try:
                    os.remove(job.file_path)
                    files_deleted += 1
                    logger.info(f"Deleted audio file: {job.file_path}")
                except OSError as e:
                    logger.warning(f"Failed to delete audio file {job.file_path}: {e}")

            # Delete transcript file if exists
            if job.transcript_file_path and os.path.exists(job.transcript_file_path):
                try:
                    os.remove(job.transcript_file_path)
                    files_deleted += 1
                    logger.info(f"Deleted transcript file: {job.transcript_file_path}")
                except OSError as e:
                    logger.warning(f"Failed to delete transcript file {job.transcript_file_path}: {e}")

            # Delete the job record
            session.delete(job)

        # Delete the user
        session.delete(user)
        session.commit()

        logger.info(f"Admin {admin.email} deleted user {user_email} ({len(jobs)} jobs, {files_deleted} files)")

    # Return success message that will trigger a refresh of the user list
    return HTMLResponse(
        content=f"""
        <div hx-get="/admin" hx-trigger="load" hx-target="#main-content" hx-swap="innerHTML">
            <div class="text-flexoki-gr">User deleted successfully</div>
        </div>
        """,
        status_code=200
    )
