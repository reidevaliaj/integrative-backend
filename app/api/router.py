from fastapi import APIRouter

from app.api.routes import auth, blog, health, magazines, payments, samples, subscriptions

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(blog.router, prefix="/blog", tags=["blog"])
api_router.include_router(samples.router, prefix="/samples", tags=["samples"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(magazines.router, prefix="/magazines", tags=["magazines"])
api_router.include_router(subscriptions.router, prefix="/subscriptions", tags=["subscriptions"])
api_router.include_router(payments.router, prefix="/payments", tags=["payments"])
