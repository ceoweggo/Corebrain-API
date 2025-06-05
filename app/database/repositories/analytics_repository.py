from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from app.database.repositories.base_repository import BaseRepository
import uuid

class AnalyticsRepository:
    """
    Repositorio para operaciones con analíticas
    """
    
    def __init__(self, db):
        self.db = db
        self.collection = db["analytics"]
    
    async def log_event(self, 
                        event_type: str,
                        user_id: Optional[str] = None,
                        api_key_id: Optional[str] = None,
                        data: Optional[Dict[str, Any]] = None) -> str:
        """
        Registra un evento analítico
        """
        event = {
            "id": str(uuid.uuid4()),
            "event_type": event_type,
            "timestamp": datetime.now(),
            "data": data or {}
        }
        
        if user_id:
            event["user_id"] = user_id
        
        if api_key_id:
            event["api_key_id"] = api_key_id
        
        await self.collection.insert_one(event)
        return event["id"]
    
    async def get_events_by_type(self, 
                                event_type: str, 
                                start_date: Optional[datetime] = None,
                                end_date: Optional[datetime] = None,
                                limit: int = 100) -> List[Dict[str, Any]]:
        """
        Obtiene eventos por tipo
        """
        query = {"event_type": event_type}
        
        if start_date or end_date:
            query["timestamp"] = {}
            
            if start_date:
                query["timestamp"]["$gte"] = start_date
            
            if end_date:
                query["timestamp"]["$lte"] = end_date
        
        cursor = self.collection.find(query).sort("timestamp", -1).limit(limit)
        return await cursor.to_list(length=limit)
    
    async def get_usage_by_period(self,
                                period: str = "day",
                                days: int = 30,
                                user_id: Optional[str] = None,
                                api_key_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Obtiene estadísticas de uso agrupadas por periodo
        
        Args:
            period: Periodo de agrupación ('day', 'week', 'month')
            days: Número de días a analizar
            user_id: Filtrar por usuario
            api_key_id: Filtrar por API key
        """
        # Calcular fecha de inicio
        start_date = datetime.now() - timedelta(days=days)
        
        # Crear filtro
        match_filter = {"timestamp": {"$gte": start_date}}
        
        if user_id:
            match_filter["user_id"] = user_id
        
        if api_key_id:
            match_filter["api_key_id"] = api_key_id
        
        # Determinar formato de fecha para agrupación
        date_format = "%Y-%m-%d"
        if period == "week":
            date_format = "%Y-%U"
        elif period == "month":
            date_format = "%Y-%m"
        
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
        return await self.collection.aggregate(pipeline).to_list(length=None)

