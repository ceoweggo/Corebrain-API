import os
import uvicorn
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from datetime import datetime

from app.routers import auth, chat, cli_token, database, analytics, public, api_keys
from app.database import connect_to_mongodb, close_mongodb_connection
from app.middleware import setup_middleware
from app.core.config import settings
from app.core.logging import LogEntry
from app.core.permissions import PermissionError

# Crear aplicación FastAPI
app = FastAPI(
    title="CoreBrain API",
    description="API para el procesamiento de mensajes con IA y consultas a bases de datos",
    version=os.environ.get("APP_VERSION", "0.1.0"),
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
)

# Configurar middleware
setup_middleware(app)

# Configurar CORS explícitamente
app.add_middleware(
    CORSMiddleware,
    #allow_origins=settings.SECURITY.CORS_ORIGINS,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Registrar routers
app.include_router(auth.router, prefix="/api/auth", tags=["Autenticación"])
app.include_router(cli_token.router, prefix="/api/auth", tags=["Token"])
app.include_router(api_keys.router, prefix="/api/auth", tags=["Token"])
app.include_router(chat.router, prefix="/api/chat", tags=["Chat"])
app.include_router(database.router, prefix="/api/database", tags=["Base de datos"])
app.include_router(analytics.router, prefix="/api/analytics", tags=["Analíticas"])
app.include_router(public.router, prefix="/api/public", tags=["Autenticación Pública"])

# Eventos de inicio y apagado
@app.on_event("startup")
async def startup_event():
    """Evento de inicio de la aplicación"""
    # Conectar a MongoDB
    await connect_to_mongodb()
    
    # Registrar inicio
    LogEntry("app_startup", "info") \
        .add_data("version", app.version) \
        .add_data("environment", settings.ENVIRONMENT) \
        .log()

@app.on_event("shutdown")
async def shutdown_event():
    """Evento de apagado de la aplicación"""
    # Cerrar conexión a MongoDB
    await close_mongodb_connection()
    
    # Registrar apagado
    LogEntry("app_shutdown", "info").log()

# Manejadores de excepciones
@app.exception_handler(PermissionError)
async def permission_error_handler(request: Request, exc: PermissionError):
    """Manejador de errores de permisos"""
    return JSONResponse(
        status_code=status.HTTP_403_FORBIDDEN,
        content={"detail": str(exc)},
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """Manejador general de excepciones"""
    # Registrar error
    LogEntry("unhandled_exception", "error") \
        .add_data("path", request.url.path) \
        .add_data("method", request.method) \
        .add_data("error", str(exc)) \
        .log()
    
    # Solo mostrar detalles en desarrollo
    detail = str(exc) if settings.DEBUG else "Error interno del servidor"
    
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": detail},
    )

# Personalizar esquema OpenAPI
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    
    # Añadir seguridad para API key
    openapi_schema["components"]["securitySchemes"] = {
        "ApiKeyAuth": {
            "type": "apiKey",
            "in": "header",
            "name": settings.SECURITY.API_KEY_NAME
        }
    }
    
    # Aplicar seguridad a todas las rutas
    openapi_schema["security"] = [{"ApiKeyAuth": []}]
    
    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi

# Rutas base
@app.get("/", tags=["Estado"])
async def root():
    """Endpoint raíz"""
    return {
        "name": "Corebrain API",
        "version": app.version,
        "status": "online"
    }

@app.get("/health", tags=["Estado"])
async def health_check():
    """Verificación de estado para balanceadores de carga"""
    return {"status": "ok", "timestamp": datetime.now().isoformat()}

# Ejecutar la aplicación directamente si se llama al script
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        reload=settings.DEBUG
    )