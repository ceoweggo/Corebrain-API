from fastapi import FastAPI
from app.middleware.authentication import AuthenticationMiddleware
from app.middleware.rate_limiter import RateLimiter
from app.middleware.cors import setup_cors
from app.middleware.request_validator import RequestValidator

def setup_middleware(app: FastAPI) -> None:
    """
    Configura todos los middleware para la aplicación
    """
    # Configurar CORS
    setup_cors(app)
    
    # Añadir middleware personalizado
    app.middleware("http")(AuthenticationMiddleware())
    app.middleware("http")(RateLimiter())
    app.middleware("http")(RequestValidator())