from fastapi import APIRouter
from app.routers import auth
from app.routers import corebrain
from app.routers import api_keys
from app.routers import database
from app.routers import cli_token

router = APIRouter()

router.include_router(auth.router, prefix="/auth", tags=["auth"])
router.include_router(api_keys.router, prefix="/api-keys", tags=["api-keys"])
router.include_router(database.router, prefix="/database", tags=["databases"])
router.include_router(corebrain.router, prefix="/corebrain", tags=["corebrain"])
router.include_router(cli_token.router, prefix="/corebrain", tags=["corebrain","cli"])
