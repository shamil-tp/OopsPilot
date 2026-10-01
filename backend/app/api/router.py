from fastapi import APIRouter

from app.api.routes import cicd, code_reviews, demo, incidents, services, system, webhooks

api_router = APIRouter(prefix="/api")
api_router.include_router(system.router)
api_router.include_router(incidents.router)
api_router.include_router(services.router)
api_router.include_router(demo.router)
api_router.include_router(cicd.router)
api_router.include_router(webhooks.router)
api_router.include_router(code_reviews.router)
