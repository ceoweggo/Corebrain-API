"""
Routes to manage API Keys

Used to create, list, update and revoke API Keys.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Body, Request
from fastapi.security import OAuth2PasswordBearer, APIKeyHeader
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
import secrets
import logging

from app.core.config import settings
from app.models.api_key import ApiKeyBase, ApiKeyCreate, ApiKeyUpdate, ApiKeyInDB, ApiKeyResponse
from app.services import auth_service
from app.middleware.authentication import get_current_user, get_api_key
from app.core.logging import LogEntry
from app.core.permissions import verify_permissions

# Configurar logger
logger = logging.getLogger(__name__)

API_KEY_HEADER = APIKeyHeader(name=settings.SECURITY.API_KEY_NAME)

router = APIRouter()

@router.get("/api-keys", response_model=List[ApiKeyInDB])
async def get_api_keys(current_user: Dict[str, Any] = Depends(get_current_user)):
    """
    Obtiene todas las API Keys del usuario autenticado
    """
    try:
        user_id = current_user.get("sub")
        print("user_id: ", user_id)
        logger.info(f"Solicitando API Keys para usuario: {user_id}")
        
        api_keys = await auth_service.get_user_api_keys(user_id)
        print("api_keys: ", api_keys)

        # Sanitizamos para no exponer información sensible en logs
        key_count = len(api_keys) if api_keys else 0
        logger.info(f"Obtenidas {key_count} API Keys para usuario: {user_id}")
        
        return api_keys
    except Exception as e:
        logger.error(f"Error al obtener API Keys: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al obtener API Keys: {str(e)}"
        )

@router.get("/api-keys/{api_key}", response_model=ApiKeyInDB)
async def get_api_key(api_key: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """
    Obtiene todas las API Keys del usuario autenticado
    """
    try:
        api_key_data = await auth_service.get_api_key(api_key)
        logger.info(f"Key {api_key_data} consultada")
        
        return api_key_data
    except Exception as e:
        logger.error(f"Error al obtener API Key: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al obtener API Key: {str(e)}"
        )



@router.put("/api-keys/{key_id}")
async def update_api_key(
    key_id: str,
    update_data: ApiKeyUpdate = Body(...),  # Match what you're sending from frontend
    current_user: Dict[str, Any] = Depends(get_current_user)):
    """
    Update a API Key
    """
    try:
        logger.info(f"Actualizando informacion de la API Key: {key_id}")

        if not key_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="API Key no encontrada"
            )
        
        print("update_data: ", update_data)
        # Extraer la información de configuración
        config_id = update_data.metadata.get("config_id") if update_data.metadata else None
        
        if not config_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Se requiere config_id en los metadatos"
            )
        
        # Actualizar la API key con los nuevos metadatos
        print("data to update: ", update_data)
        updated_api_key = await auth_service.update_api_key(
            key_id, 
            update_data
        )
        
        print("updated_api_key: ", updated_api_key)
        
        # Registrar la actualización
        logger.info(f"API key actualizada con configuración Corebrain: {config_id}")
        
        return updated_api_key
    
    except HTTPException:
        raise
    
    except Exception as e:
        logger.error(f"Error al actualizar API Key: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al actualizar API Key: {str(e)}"
        )

@router.post("/api-keys", response_model=ApiKeyResponse)
async def create_api_key(
    api_key_data: ApiKeyBase = Body(...),  # Match what you're sending from frontend
    current_user: Dict[str, Any] = Depends(get_current_user)  # Use the same auth as your GET endpoint
):
    """
    Crea una nueva API Key para el usuario autenticado
    """
    try:
        print("Entra en el api key create")

        # Verificar permisos
        #verify_permissions(api_key_data.level, "write")
        
        # Validación adicional del nombre de la API Key
        if not api_key_data.name or len(api_key_data.name.strip()) < 3:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El nombre de la API Key debe tener al menos 3 caracteres"
            )

        api_key = await auth_service.create_api_key(api_key_data, user_id=current_user.get("sub"))
        return api_key
       
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error al crear API Key: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al crear API Key: {str(e)}"
        )

@router.get("/api-keys/validate")
async def validate_api_key(
    request: Request,
    api_key: str = Depends(API_KEY_HEADER)
):
    """
    Valida una API key
    """
    print("Pasa al await get api key")
    api_key_data = getattr(request.state, "api_key_data", None)
    
    if not api_key_data:
        print("No hay datos en request.state, obteniendo directamente")
        try:
            api_key_data = await auth_service.get_api_key_data(api_key)
            print("api_key_data: ", api_key_data)
        except Exception as e:
            print("Error al obtener api key: ", e)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API key inválida"
            )
        
    print("Pasa al return")
    return {
        "valid": True,
        "level": api_key_data.level,
        "name": api_key_data.name,
        "allowed_domains": api_key_data.allowed_domains
    }

@router.delete("/api-keys/{key_id}")
async def revoke_api_key(
    key_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """
    Revoca una API Key existente
    """
    try:
        user_id = current_user.get("sub")
        logger.info(f"Solicitud para revocar API Key {key_id} del usuario: {user_id}")
        
        # Validación adicional del ID de la API Key
        if not key_id or not key_id.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ID de API Key inválido"
            )
        
        success = await auth_service.revoke_api_key(key_id, user_id)
        
        if not success:
            logger.warning(f"Intento de revocar API Key inexistente o sin permiso: {key_id} por usuario: {user_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="API Key no encontrada o no tienes permisos para revocarla"
            )
        
        logger.info(f"API Key {key_id} revocada correctamente por usuario: {user_id}")
        return {"message": "API Key revocada correctamente"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error al revocar API Key {key_id}: {str(e)}", exc_info=True)
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al revocar API Key: {str(e)}"
        )

