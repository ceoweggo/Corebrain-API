from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
import json
from motor.motor_asyncio import AsyncIOMotorClient
from app.core.config import settings
from app.core.logging import LogEntry
from app.core.cache import Cache

db_client = AsyncIOMotorClient(settings.MONGODB.MONGODB_URL)
db = db_client[settings.MONGODB.MONGODB_DB_NAME]

analytics_collection = db["analytics"]

async def track_event(
    event_type: str,
    user_id: Optional[str],
    api_key_id: Optional[str],
    data: Optional[Dict[str, Any]] = None
) -> str:
    """
    Registra un evento analítico
    
    Args:
        event_type: Tipo de evento (ej. 'message_sent', 'query_executed')
        user_id: ID del usuario (opcional)
        api_key_id: ID de la API key (opcional)
        data: Datos adicionales del evento
        
    Returns:
        ID del evento registrado
    """
    # Crear documento de evento
    event = {
        "event_type": event_type,
        "timestamp": datetime.now(),
        "data": data or {}
    }
    
    if user_id:
        event["user_id"] = user_id
    
    if api_key_id:
        event["api_key_id"] = api_key_id
    
    # Insertar en la base de datos
    result = await analytics_collection.insert_one(event)
    
    # Invalidar caché de estadísticas
    Cache.delete(Cache.generate_key("stats_daily"))
    Cache.delete(Cache.generate_key("stats_monthly"))
    
    return str(result.inserted_id)

async def get_usage_stats(
    api_key_id: Optional[str] = None,
    user_id: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    group_by: str = "day"
) -> Dict[str, Any]:
    """
    Obtiene estadísticas de uso
    
    Args:
        api_key_id: Filtrar por API key
        user_id: Filtrar por usuario
        start_date: Fecha de inicio
        end_date: Fecha de fin
        group_by: Agrupar por ('day', 'week', 'month')
        
    Returns:
        Estadísticas de uso
    """
    # Valores por defecto
    if not start_date:
        start_date = datetime.now() - timedelta(days=30)
    
    if not end_date:
        end_date = datetime.now()
    
    # Crear filtro
    match_filter = {
        "timestamp": {
            "$gte": start_date,
            "$lte": end_date
        }
    }
    
    if api_key_id:
        match_filter["api_key_id"] = api_key_id
    
    if user_id:
        match_filter["user_id"] = user_id
    
    # Determinar formato de fecha para agrupación
    date_format = "%Y-%m-%d"
    if group_by == "week":
        date_format = "%Y-%U"  # Año-Semana
    elif group_by == "month":
        date_format = "%Y-%m"  # Año-Mes
    
    # Pipeline de agregación
    pipeline = [
        {"$match": match_filter},
        {
            "$group": {
                "_id": {
                    "date": {"$dateToString": {"format": date_format, "date": "$timestamp"}},
                    "event_type": "$event_type"
                },
                "count": {"$sum": 1}
            }
        },
        {
            "$group": {
                "_id": "$_id.date",
                "events": {
                    "$push": {
                        "event_type": "$_id.event_type",
                        "count": "$count"
                    }
                },
                "total": {"$sum": "$count"}
            }
        },
        {"$sort": {"_id": 1}}
    ]
    
    # Ejecutar agregación
    results = await analytics_collection.aggregate(pipeline).to_list(length=None)
    
    # Formatear resultados
    formatted_results = []
    for result in results:
        formatted_result = {
            "date": result["_id"],
            "total": result["total"],
            "events": {}
        }
        
        for event in result["events"]:
            formatted_result["events"][event["event_type"]] = event["count"]
        
        formatted_results.append(formatted_result)
    
    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "group_by": group_by,
        "stats": formatted_results
    }

async def get_top_queries(
    limit: int = 10,
    days: int = 7
) -> List[Dict[str, Any]]:
    """
    Obtiene las consultas más populares
    
    Args:
        limit: Número máximo de consultas a devolver
        days: Número de días para analizar
        
    Returns:
        Lista de consultas más populares
    """
    # Calcular fecha de inicio
    start_date = datetime.now() - timedelta(days=days)
    
    # Intentar obtener de caché
    cache_key = Cache.generate_key("top_queries", limit, days)
    cached_data = Cache.get(cache_key)
    
    if cached_data:
        return cached_data
    
    # Pipeline de agregación
    pipeline = [
        {
            "$match": {
                "event_type": "nl_query_processed",
                "timestamp": {"$gte": start_date}
            }
        },
        {
            "$group": {
                "_id": "$data.query",
                "count": {"$sum": 1},
                "collections": {"$addToSet": "$data.collection"},
                "last_used": {"$max": "$timestamp"}
            }
        },
        {"$sort": {"count": -1}},
        {"$limit": limit}
    ]
    
    # Ejecutar agregación
    results = await analytics_collection.aggregate(pipeline).to_list(length=None)
    
    # Formatear resultados
    formatted_results = []
    for result in results:
        formatted_result = {
            "query": result["_id"],
            "count": result["count"],
            "collections": result["collections"],
            "last_used": result["last_used"].isoformat()
        }
        
        formatted_results.append(formatted_result)
    
    # Guardar en caché (1 hora)
    Cache.set(cache_key, formatted_results, ttl=3600)
    
    return formatted_results


    
class DataAnalysisService:
    """Service for performing data analysis on query results"""
    
    @staticmethod
    def summarize_results(data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Generate a summary of query results"""
        if not data:
            return {"count": 0, "summary": "No data found"}
            
        summary = {
            "count": len(data),
            "fields": set()
        }
        
        # Collect all fields
        for item in data:
            summary["fields"].update(item.keys())
        
        summary["fields"] = list(summary["fields"])
        
        # Try to identify numeric fields for basic statistics
        numeric_stats = {}
        for field in summary["fields"]:
            values = [item.get(field) for item in data if field in item and isinstance(item[field], (int, float))]
            if values:
                numeric_stats[field] = {
                    "min": min(values),
                    "max": max(values),
                    "avg": sum(values) / len(values),
                    "sum": sum(values)
                }
        
        if numeric_stats:
            summary["numeric_stats"] = numeric_stats
            
        return summary
    
    @staticmethod
    def get_field_distribution(data: List[Dict[str, Any]], field: str) -> Dict[Any, int]:
        """Calculate distribution of values for a specified field"""
        if not data:
            return {}
            
        distribution = {}
        for item in data:
            value = item.get(field)
            if value is not None:
                distribution[value] = distribution.get(value, 0) + 1
                
        return distribution
    
    @staticmethod
    def detect_correlations(data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Detect potential correlations between numeric fields"""
        if len(data) < 10:
            return []
            
        # Identify numeric fields
        numeric_fields = set()
        for item in data:
            for field, value in item.items():
                if isinstance(value, (int, float)):
                    numeric_fields.add(field)
        
        numeric_fields = list(numeric_fields)
        
        # Check correlations between pairs of fields
        correlations = []
        
        for i in range(len(numeric_fields)):
            for j in range(i+1, len(numeric_fields)):
                field1 = numeric_fields[i]
                field2 = numeric_fields[j]
                
                # Extract paired values where both exist
                pairs = [
                    (item.get(field1), item.get(field2))
                    for item in data
                    if field1 in item and field2 in item and 
                    isinstance(item[field1], (int, float)) and 
                    isinstance(item[field2], (int, float))
                ]
                
                if len(pairs) < 10:
                    continue
                    
                # Calculate correlation coefficient (simplified approach)
                x_values = [p[0] for p in pairs]
                y_values = [p[1] for p in pairs]
                
                x_mean = sum(x_values) / len(x_values)
                y_mean = sum(y_values) / len(y_values)
                
                numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_values, y_values))
                denominator_x = sum((x - x_mean) ** 2 for x in x_values)
                denominator_y = sum((y - y_mean) ** 2 for y in y_values)
                
                if denominator_x > 0 and denominator_y > 0:
                    correlation = numerator / (denominator_x ** 0.5 * denominator_y ** 0.5)
                    
                    # Only include significant correlations
                    if abs(correlation) > 0.5:
                        correlations.append({
                            "field1": field1,
                            "field2": field2,
                            "correlation": correlation
                        })
        
        return correlations
