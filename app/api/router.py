from fastapi import APIRouter

from app.api.routes import auth, health, magazines, subscriptions

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(magazines.router, prefix="/magazines", tags=["magazines"])
api_router.include_router(subscriptions.router, prefix="/subscriptions", tags=["subscriptions"])
