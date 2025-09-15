from fastapi import Request
from sqlmodel import Session
import logging

# Set up logging
logger = logging.getLogger(__name__)

async def get_current_user_safe(request: Request):
    """Safely get current user, return None if not authenticated"""
    try:
        from auth import auth_backend, get_user_manager, get_user_db
        from fastapi_users.authentication.strategy import JWTStrategy
        from fastapi_users.authentication.transport import CookieTransport
        from main import engine
        
        # Get the auth backend components
        cookie_transport = CookieTransport(cookie_max_age=3600)
        jwt_strategy = JWTStrategy(secret="SECRET", lifetime_seconds=3600)
        
        # Try to get token from cookies
        token = request.cookies.get("fastapiusersauth")
        logger.info(f"Cookie token present: {token is not None}")
        if not token:
            logger.info("No auth cookie found")
            return None
            
        # Get user manager
        user_db = await get_user_db(Session(engine)).__anext__()
        user_manager = await get_user_manager(user_db).__anext__()
        
        # Verify token and get user
        user_data = await jwt_strategy.read_token(token, user_manager)
        logger.info(f"User data from token: {user_data}")
        if not user_data:
            logger.info("Invalid token")
            return None
        
        # Check if we got the full user object or just the ID
        if hasattr(user_data, 'email'):
            # We got the full user object
            user = user_data
            logger.info(f"Got full user object: {user.email}")
        else:
            # We got just the ID, need to fetch the user
            user = await user_manager.get(user_data)
            logger.info(f"Retrieved user by ID: {user.email if user else None}")
        
        return user
        
    except Exception as e:
        logger.info(f"Authentication failed: {e}")
        return None