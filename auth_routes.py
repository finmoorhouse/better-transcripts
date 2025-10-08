from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
import logging

# Initialize router
router = APIRouter()

# Set up logging
logger = logging.getLogger(__name__)

# Import dependencies from auth utils
from auth_utils import get_current_user_safe

@router.get("/check-auth")
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
        <div class="max-w-md mx-auto bg-flexoki-paper-light rounded-lg border border-flexoki-ui-3 p-6 text-center shadow-sm">
            <h2 class="text-2xl font-semibold mb-2 text-flexoki-tx text-left">Welcome to Better Transcripts</h2>
            <p class="text-flexoki-tx-3 mb-6 text-left">Please log in to access your transcription jobs.</p>
            <div class="space-y-3">
                <a href="/login" class="block w-full bg-flexoki-bl hover:bg-flexoki-bl-2 text-flexoki-paper font-bold py-2 px-4 rounded transition duration-300">
                    Login
                </a>
                <a href="/register" class="block w-full bg-flexoki-or hover:bg-flexoki-or-2 text-flexoki-paper font-bold py-2 px-4 rounded transition duration-300">
                    Register
                </a>
            </div>
        </div>
    """)

@router.get("/auth-test")
async def auth_test(request: Request):
    user = await get_current_user_safe(request)
    if user:
        logger.info(f"Auth test successful for user: {user.email}")
        return HTMLResponse(f"""
            <div hx-get="/jobs" hx-trigger="load" hx-push-url="true"></div>
            <script>
                // Load auth status
                htmx.ajax('GET', '/auth-status', {{target: '#auth-status'}});
            </script>
        """)
    else:
        logger.info("Auth test failed - showing login screen")
        # Invalid/expired cookie - show login screen
        return HTMLResponse("""
            <div class="max-w-md mx-auto bg-flexoki-ui border border-flexoki-ui-3 p-6 text-center">
                <h2 class="text-2xl font-semibold mb-4 text-flexoki-tx">Session Expired</h2>
                <p class="text-flexoki-tx-3 mb-6">Please log in again to access your transcription jobs.</p>
                <div class="space-y-3">
                    <a href="/login" class="block w-full bg-flexoki-bl hover:bg-flexoki-bl-2 text-flexoki-paper font-bold py-2 px-4 rounded transition duration-300">
                        Login
                    </a>
                    <a href="/register" class="block w-full bg-flexoki-gr hover:bg-flexoki-gr-2 text-flexoki-paper font-bold py-2 px-4 rounded transition duration-300">
                        Register
                    </a>
                </div>
            </div>
            <script>
                // Clear the invalid cookie
                document.cookie = "fastapiusersauth=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;";
            </script>
        """)

@router.get("/auth-status")
async def auth_status(request: Request):
    user = await get_current_user_safe(request)
    if user:
        return HTMLResponse(f"""
            <div class="flex items-center gap-3">
                <span class="text-sm text-flexoki-tx-3">Welcome, {user.name}</span>
                <button onclick="logout()" class="bg-flexoki-re hover:bg-flexoki-re-2 text-flexoki-paper text-sm px-3 py-1 rounded transition duration-300">
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
    else:
        # Invalid/expired session - return empty auth status
        return HTMLResponse("")