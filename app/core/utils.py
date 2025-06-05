import uuid
import json
from datetime import datetime, date
from typing import Any
from pydantic import BaseModel
from typing import List

from app.core.logging import logger

class Utils:
    
    @staticmethod
    def determine_best_collection(query: str, available_collections: List[str], db_connection=None) -> str:
        """
        Determina la mejor colección a consultar basándose en el contenido de la consulta
        y las colecciones disponibles, sin depender de palabras clave predefinidas.
        
        Args:
            query: Consulta en lenguaje natural
            available_collections: Lista de colecciones disponibles
            db_connection: Conexión opcional a la base de datos para exploración
            
        Returns:
            Nombre de la colección seleccionada
        """
        if not available_collections:
            return None  # No hay colecciones disponibles
        
        if len(available_collections) == 1:
            return available_collections[0]  # Si solo hay una colección, usarla
        
        # Normalizar la consulta
        query_lower = query.lower()
        
        # 1. Buscar menciones directas de colecciones en la consulta
        for collection in available_collections:
            # Normalizar el nombre de la colección para la comparación
            collection_normalized = collection.lower()
            collection_singular = collection_normalized[:-1] if collection_normalized.endswith('s') else collection_normalized
            
            # Buscar el nombre completo o versión singular en la consulta
            if collection_normalized in query_lower or collection_singular in query_lower:
                logger.info(f"Colección seleccionada por mención directa: {collection}")
                return collection
        
        # 2. Inferir el tema de la consulta y relacionarlo con los nombres de colecciones
        # Palabras clave comunes en consultas de bases de datos
        query_themes = {
            "usuarios": ["usuario", "user", "persona", "cliente", "correo", "email", "nombre"],
            "productos": ["producto", "item", "artículo", "articulo", "mercancía", "mercancia"],
            "ventas": ["venta", "compra", "transacción", "transaccion", "pedido", "orden"],
            "servicios": ["servicio", "service", "prestación", "prestacion"],
            "documentos": ["documento", "archivo", "fichero", "file", "record"],
            "mensajes": ["mensaje", "message", "correo", "notificación", "notificacion", "comunicación", "comunicacion"],
            "registros": ["registro", "record", "log", "entrada", "history", "actividad"]
        }
        
        # Calcular relevancia de cada tema para la consulta
        theme_scores = {}
        for theme, keywords in query_themes.items():
            score = sum(1 for keyword in keywords if keyword in query_lower)
            if score > 0:
                theme_scores[theme] = score
        
        # Si encontramos temas relevantes, buscar colecciones que puedan relacionarse
        if theme_scores:
            # Ordenar temas por relevancia
            sorted_themes = sorted(theme_scores.items(), key=lambda x: x[1], reverse=True)
            
            # Buscar colecciones que coincidan con los temas más relevantes
            for theme, _ in sorted_themes:
                for collection in available_collections:
                    collection_lower = collection.lower()
                    
                    # Comprobar si el tema está relacionado con el nombre de la colección
                    if (theme in collection_lower or
                        any(kw in collection_lower for kw in query_themes[theme])):
                        logger.info(f"Colección seleccionada por tema de consulta: {collection}")
                        return collection
        
        # 3. Para consultas generales sobre la base de datos, priorizar ciertas colecciones comunes
        if any(term in query_lower for term in ["base de datos", "base datos", "bbdd", "bd", "database", "db", "schema", "estructura"]):
            # Priorizar colecciones comunes por nombre
            common_collections = ["users", "customers", "clients", "accounts", "products", "orders", "items"]
            for common in common_collections:
                for collection in available_collections:
                    if common in collection.lower():
                        logger.info(f"Colección seleccionada por nombre común: {collection}")
                        return collection
        
        # 4. Como última opción, intentar inferir la colección principal basada en el número de documentos
        if db_connection:
            try:
                # Obtener colección con más documentos (posiblemente la principal)
                counts = []
                for coll in available_collections:
                    count = db_connection[coll].estimated_document_count()
                    counts.append((coll, count))
                
                if counts:
                    # Ordenar por número de documentos (descendente)
                    sorted_counts = sorted(counts, key=lambda x: x[1], reverse=True)
                    logger.info(f"Colección seleccionada por número de documentos: {sorted_counts[0][0]}")
                    return sorted_counts[0][0]
            except Exception as e:
                logger.warning(f"Error al obtener recuentos de documentos: {str(e)}")
        
        # 5. Si todo lo demás falla, usar una heurística basada en nombres comunes
        for collection in available_collections:
            coll_lower = collection.lower()
            # Priorizar colecciones que suelen ser centrales en muchas aplicaciones
            if any(name in coll_lower for name in ["user", "customer", "client", "account", "main", "core"]):
                logger.info(f"Colección seleccionada por heurística de nombre: {collection}")
                return collection
        
        # Si no se encuentra ninguna coincidencia mejor, devolver la primera colección
        logger.info(f"Colección seleccionada por defecto (primera disponible): {available_collections[0]}")
        return available_collections[0]
    
    class JSON:

        class CorebrainJSONEncoder(json.JSONEncoder):
            """
            Encoder JSON personalizado para manejar tipos comunes no serializables 
            por defecto en la aplicación Corebrain.
            """
            def default(self, obj: Any) -> Any:
                # Manejar modelos Pydantic
                if isinstance(obj, BaseModel):
                    # Intentar primero con la versión V2 de Pydantic
                    if hasattr(obj, "model_dump"):
                        return obj.model_dump()
                    # Luego con V1
                    elif hasattr(obj, "dict"):
                        return obj.dict()
                
                # Manejar fechas y horas
                if isinstance(obj, (datetime, date)):
                    return obj.isoformat()
                
                # Manejar UUID
                if isinstance(obj, uuid.UUID):
                    return str(obj)
                
                # Manejar bytes y bytearray
                if isinstance(obj, (bytes, bytearray)):
                    return obj.decode('utf-8', errors='replace')
                
                # Manejar sets y frozensets
                if isinstance(obj, (set, frozenset)):
                    return list(obj)
                
                # Manejar objetos con método de serialización personalizado
                if hasattr(obj, "to_json"):
                    return obj.to_json()
                
                # Manejar objetos personalizados con __dict__
                if hasattr(obj, "__dict__"):
                    return {k: v for k, v in obj.__dict__.items() if not k.startswith("_")}
                
                # Dejar que JSONEncoder estándar maneje el error para tipos desconocidos
                return super().default(obj)


            def dumps(obj: Any, **kwargs) -> str:
                """
                Wrapper para json.dumps que usa el encoder personalizado.
                
                Args:
                    obj: Objeto a serializar a JSON
                    **kwargs: Argumentos adicionales para json.dumps
                    
                Returns:
                    Cadena JSON
                """
                return json.dumps(obj, cls=Utils.JSON.CorebrainJSONEncoder, **kwargs)


            def loads(s: str, **kwargs) -> Any:
                """
                Wrapper para json.loads.
                
                Args:
                    s: Cadena JSON a deserializar
                    **kwargs: Argumentos adicionales para json.loads
                    
                Returns:
                    Objeto Python
                """
                return json.loads(s, **kwargs)


            # Función para serializar modelos Pydantic específicamente
            def serialize_model(model: Any) -> dict:
                """
                Convierte un modelo Pydantic u otro objeto a un diccionario serializable a JSON.
                
                Args:
                    model: Modelo a serializar
                    
                Returns:
                    Diccionario serializable
                """
                if hasattr(model, "model_dump"):  # Pydantic V2
                    return model.model_dump()
                elif hasattr(model, "dict"):      # Pydantic V1
                    return model.dict()
                elif isinstance(model, dict):
                    return model
                elif hasattr(model, "__dict__"):
                    return {k: v for k, v in model.__dict__.items() if not k.startswith("_")}
                return model  # Si no podemos serializar, devolvemos el objeto original