
from fastapi import APIRouter, Depends, HTTPException, status, Query
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta

from app.services import analytics_service
from app.middleware.authentication import get_api_key
from app.core.permissions import verify_permissions, PermissionError
from app.core.logging import LogEntry

router = APIRouter()

@router.get("/usage", response_model=Dict[str, Any])
async def get_usage_stats(
    days: int = Query(30, ge=1, le=90),
    group_by: str = Query("day", regex="^(day|week|month)$"),
    api_key = Depends(get_api_key)
):
    """
    Obtiene estadísticas de uso
    """
    try:
        # Verificar permisos
        verify_permissions(api_key.level, "admin")
        
        # Obtener estadísticas
        stats = await analytics_service.get_usage_stats(
            api_key_id=api_key.id,
            start_date=datetime.now() - timedelta(days=days),
            end_date=datetime.now(),
            group_by=group_by
        )
        
        return stats
        
    except PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e)
        )
        
    except Exception as e:
        LogEntry("get_usage_stats_error", "error") \
            .set_api_key_id(api_key.id) \
            .add_data("error", str(e)) \
            .log()
            
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error al obtener estadísticas de uso"
        )

@router.get("/top-queries", response_model=List[Dict[str, Any]])
async def get_top_queries(
    limit: int = Query(10, ge=1, le=50),
    days: int = Query(7, ge=1, le=30),
    api_key = Depends(get_api_key)
):
    """
    Obtiene las consultas más populares
    """
    try:
        # Verificar permisos
        verify_permissions(api_key.level, "admin")
        
        # Obtener consultas
        queries = await analytics_service.get_top_queries(
            limit=limit,
            days=days
        )
        
        return queries
        
    except PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e)
        )
        
    except Exception as e:
        LogEntry("get_top_queries_error", "error") \
            .set_api_key_id(api_key.id) \
            .add_data("error", str(e)) \
            .log()
            
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error al obtener consultas populares"
        )

