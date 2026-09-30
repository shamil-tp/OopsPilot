from fastapi import APIRouter

from app.api.routes import demo, incidents, services, system

api_router = APIRouter(prefix="/api")
api_router.include_router(system.router)
api_router.include_router(incidents.router)
api_router.include_router(services.router)
api_router.include_router(demo.router)
