"""
Route to generate tokens for CLI

Used to set up configuration for CLI.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Body, Request, Header
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm, APIKeyHeader
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime, timedelta
import secrets
import logging

# Reemplazamos jwt con jose
from jose import jwt, JWTError

from app.core.config import settings
from app.models.token import TokenRequest, TokenResponse, Token, TokenCreate
from app.services import auth_service, cli_token_service
from app.middleware.authentication import get_api_key, get_current_user
from app.core.permissions import verify_permissions
from app.core.logging import LogEntry
from app.models.api_key import ApiKeyInDB

# Configurar logger
logger = logging.getLogger(__name__)

router = APIRouter()

# Para autenticación basada en JWT (dashboard)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

@router.post("/sso/token", response_model=TokenResponse)
async def get_api_token(request: TokenRequest):
    """
    Genera un token API basado en un token SSO de Globodain
    """
    try:
        # Log de la solicitud 
        logger.info(f"Recibida solicitud de token SSO para client_id: {request.client_id}")
        print("Entra en el /token/sso/token")
        # Verificar token SSO con Globodain
        sso_data = await cli_token_service.validate_sso_token(request.access_token)
        print("sso_data: ", sso_data)
        print("Sale del validate_sso_token")
        if not sso_data:
            logger.warning(f"Token SSO inválido o expirado para client_id: {request.client_id}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token SSO inválido o expirado"
            )
        
        user_data = sso_data['user']
        print("user_data: ", user_data)
        logger.info(f"Token SSO validado correctamente para usuario: {user_data.get('email', False)}")
        
        # Generar token API
        api_token, expiration = await cli_token_service.create_api_token(user_data, request.client_id)
        
        logger.info(f"Token API generado correctamente para usuario: {user_data.get('id', False)}")
        
        return {
            "token": api_token,
            "user_data": user_data,
            "expires": expiration.isoformat()
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error al procesar token SSO: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al procesar token SSO: {str(e)}"
        )

@router.get("/tokens", response_model=List[Token])
async def get_tokens(current_user: Dict[str, Any] = Depends(get_current_user)):
    """
    Obtiene todos los tokens del usuario autenticado
    """
    try:
        user_id = current_user.get("sub")
        print("user_id: ", user_id)
        logger.info(f"Solicitando tokens para usuario: {user_id}")
        
        tokens = await cli_token_service.get_user_tokens(user_id)
        
        # Sanitizamos los tokens para no exponer información sensible en logs
        token_count = len(tokens) if tokens else 0
        logger.info(f"Obtenidos {token_count} tokens para usuario: {user_id}")
        
        return tokens
    except Exception as e:
        logger.error(f"Error al obtener tokens: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al obtener tokens: {str(e)}"
        )

@router.post("/tokens", response_model=TokenResponse)
async def create_token(
    token_data: TokenCreate,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """
    Crea un nuevo token para el usuario autenticado
    """
    try:
        user_id = current_user.get("sub")
        logger.info(f"Creando nuevo token '{token_data.name}' para usuario: {user_id}")
        
        # Validación adicional del nombre del token
        if not token_data.name or len(token_data.name.strip()) < 3:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El nombre del token debe tener al menos 3 caracteres"
            )
        
        token, expiration = await cli_token_service.create_user_token(user_id, token_data.name)
        
        logger.info(f"Token '{token_data.name}' creado correctamente para usuario: {user_id}")
        
        return {
            "token": token,
            "expires": expiration.isoformat()
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error al crear token: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al crear token: {str(e)}"
        )

@router.delete("/tokens/{token_id}")
async def revoke_token(
    token_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """
    Revoca un token existente
    """
    try:
        user_id = current_user.get("sub")
        logger.info(f"Solicitud para revocar token {token_id} del usuario: {user_id}")
        
        # Validación adicional del ID del token
        if not token_id or not token_id.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ID de token inválido"
            )
        
        success = await cli_token_service.revoke_token(token_id, user_id)
        
        if not success:
            logger.warning(f"Intento de revocar token inexistente o sin permiso: {token_id} por usuario: {user_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Token no encontrado o no tienes permisos para revocarlo"
            )
        
        logger.info(f"Token {token_id} revocado correctamente por usuario: {user_id}")
        return {"message": "Token revocado correctamente"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error al revocar token {token_id}: {str(e)}", exc_info=True)
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al revocar token: {str(e)}"
        )

@router.post("/tokens/{token_id}/refresh", response_model=TokenResponse)
async def refresh_token(
    token_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """
    Renueva un token existente
    """
    try:
        user_id = current_user.get("sub")
        logger.info(f"Solicitud para renovar token {token_id} del usuario: {user_id}")
        
        # Validación adicional del ID del token
        if not token_id or not token_id.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ID de token inválido"
            )
        
        token, expiration = await cli_token_service.refresh_token(token_id, user_id)
        
        if not token:
            logger.warning(f"Intento de renovar token inexistente o sin permiso: {token_id} por usuario: {user_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Token no encontrado o no tienes permisos para renovarlo"
            )
        
        logger.info(f"Token {token_id} renovado correctamente por usuario: {user_id}")
        
        return {
            "token": token,
            "expires": expiration.isoformat()
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error al renovar token {token_id}: {str(e)}", exc_info=True)
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al renovar token: {str(e)}"
        )

@router.get("/verify")
async def verify_cli_token(
    api_key: ApiKeyInDB = Depends(get_api_key)
):
    """
    Verifica un token de la CLI.
    Esta ruta verifica la API key usando el middleware de autenticación.
    """
    try:
        logger.info(f"Solicitud de verificación de API key para: {api_key.id}")
        
        # La API key ya está verificada por get_api_key
        # Devolver información básica de la API key
        return {
            "valid": True,
            "api_key_id": api_key.id,
            "level": api_key.level,
            "expires": api_key.expires.isoformat() if api_key.expires else None
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error al verificar API key: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al verificar API key: {str(e)}"
        )