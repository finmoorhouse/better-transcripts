from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from auth_utils import get_current_user_safe

# Initialize router
router = APIRouter()

# Initialize templates
templates = Jinja2Templates(directory="templates")

@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    # Check if user is already logged in
    user = await get_current_user_safe(request)
    if user:
        return RedirectResponse(url="/jobs", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request})

@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    # Check if user is already logged in
    user = await get_current_user_safe(request)
    if user:
        return RedirectResponse(url="/jobs", status_code=303)
    return templates.TemplateResponse("register.html", {"request": request})