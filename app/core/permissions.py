
from typing import List, Optional, Set
from app.core.config import settings
from fastapi import HTTPException, status

class PermissionError(Exception):
    """Excepción para errores de permisos"""
    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)

def check_api_key_permissions(
    api_key_level: str,
    required_permission: str
) -> bool:
    """
    Verifica si una API key tiene el permiso requerido basado en su nivel
    """
    allowed_permissions = settings.API_KEY_PERMISSION_LEVELS.get(api_key_level, [])
    return required_permission in allowed_permissions

async def check_collection_access(
    api_key_level: str,
    collection_name: str
) -> bool:
    """
    Verifica si una API key tiene acceso a una colección específica
    """
    print("Entra en el check collection con acceso: ", api_key_level)
    allowed_collections = settings.COLLECTION_ACCESS.get(api_key_level, [])
    print("Allowed collections: ", allowed_collections)
    return "*" in allowed_collections or collection_name in allowed_collections

def verify_permissions(
    api_key_level: str,
    required_permission: str,
    collection_name: Optional[str] = None
) -> None:
    """
    Verifica permisos y lanza una excepción si no son suficientes
    """
    print("Entra al permisos: ", api_key_level, required_permission, collection_name)
    # Verificar permisos generales
    if not check_api_key_permissions(api_key_level, required_permission):
        raise PermissionError(
            f"La API key no tiene permisos suficientes. Se requiere: {required_permission}"
        )
    
    # Verificar acceso a colección si se especifica
    if collection_name and not check_collection_access(api_key_level, collection_name):
        raise PermissionError(
            f"La API key no tiene acceso a la colección: {collection_name}"
        )