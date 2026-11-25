from fastapi import FastAPI

from app.routers.health import router as health_router

app = FastAPI(title="KIC 대체자산 사후관리 에이전트")
app.include_router(health_router)
