from typing import Dict, Any, List
import json

from app.core.logging import logger

class Diagnostic:

    @staticmethod
    def debug_mongodb_connection(db_connection, collection_name: str = None) -> Dict[str, Any]:
        """
        Función para diagnosticar problemas de conexión con MongoDB.
        
        Args:
            db_connection: Conexión a MongoDB
            collection_name: Nombre de una colección específica a probar
            
        Returns:
            Diccionario con información de diagnóstico
        """
        debug_info = {
            "connection_status": "unknown",
            "available_collections": [],
            "collection_stats": {},
            "errors": []
        }
        
        try:
            # Verificar si la conexión está activa
            server_info = db_connection.command("serverStatus")
            debug_info["connection_status"] = "connected"
            debug_info["server_version"] = server_info.get("version", "unknown")
            
            # Obtener listado de colecciones
            try:
                collections = db_connection.list_collection_names()
                debug_info["available_collections"] = collections
                
                # Si se especificó una colección, verificar que exista
                if collection_name:
                    if collection_name in collections:
                        debug_info["collection_found"] = True
                        
                        # Obtener estadísticas de la colección
                        stats = db_connection.command("collStats", collection_name)
                        debug_info["collection_stats"] = {
                            "document_count": stats.get("count", 0),
                            "size_bytes": stats.get("size", 0),
                            "avg_document_size": stats.get("avgObjSize", 0)
                        }
                        
                        # Obtener documento de muestra
                        try:
                            sample = db_connection[collection_name].find_one()
                            if sample:
                                # Convertir ObjectId a string para serialización
                                sample_serializable = {}
                                for key, value in sample.items():
                                    if key == "_id" and hasattr(value, "__str__"):
                                        sample_serializable[key] = str(value)
                                    else:
                                        sample_serializable[key] = value
                                
                                debug_info["sample_document"] = sample_serializable
                        except Exception as e:
                            debug_info["errors"].append(f"Error al obtener documento de muestra: {str(e)}")
                    else:
                        debug_info["collection_found"] = False
                        debug_info["errors"].append(f"La colección '{collection_name}' no existe en la base de datos")
            except Exception as e:
                debug_info["errors"].append(f"Error al obtener colecciones: {str(e)}")
        
        except Exception as e:
            debug_info["connection_status"] = "error"
            debug_info["errors"].append(f"Error de conexión: {str(e)}")
        
        return debug_info

    def log_mongodb_query_details(query: Dict[str, Any], collection_name: str) -> None:
        """
        Registra detalles de una consulta MongoDB para depuración.
        
        Args:
            query: Consulta MongoDB (diccionario o modelo)
            collection_name: Nombre de la colección
        """
        # Convertir query a diccionario si no lo es
        query_dict = {}
        if isinstance(query, dict):
            query_dict = query
        else:
            # Intentar convertir a diccionario si es modelo Pydantic
            try:
                if hasattr(query, "model_dump"):
                    query_dict = query.model_dump()
                elif hasattr(query, "dict"):
                    query_dict = query.dict()
                else:
                    # Intentar extraer atributos manualmente
                    for attr in ["operation", "query", "pipeline", "projection", "sort", "limit", "skip"]:
                        if hasattr(query, attr):
                            query_dict[attr] = getattr(query, attr)
            except Exception as e:
                logger.error(f"Error al convertir query a diccionario: {str(e)}")
                query_dict = {"error": "No se pudo convertir la consulta a formato imprimible"}
        
        # Registrar detalles de la consulta
        logger.info(f"Ejecutando consulta MongoDB en colección: {collection_name}")
        logger.info(f"Tipo de operación: {query_dict.get('operation', 'desconocido')}")
        
        if "query" in query_dict and query_dict["query"]:
            logger.info(f"Filtros: {json.dumps(query_dict['query'], default=str)}")
        elif "pipeline" in query_dict and query_dict["pipeline"]:
            logger.info(f"Pipeline: {json.dumps(query_dict['pipeline'], default=str)}")
        
        if "projection" in query_dict and query_dict["projection"]:
            logger.info(f"Proyección: {json.dumps(query_dict['projection'], default=str)}")
        
        if "sort" in query_dict and query_dict["sort"]:
            logger.info(f"Ordenamiento: {json.dumps(query_dict['sort'], default=str)}")
        
        logger.info(f"Límite: {query_dict.get('limit', 'no especificado')}")
        logger.info(f"Salto: {query_dict.get('skip', 'no especificado')}")


    async def execute_mongodb_query(
        db_connection, 
        mongo_query,
        max_results: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Ejecuta una consulta MongoDB con diagnóstico y depuración mejorados.
        
        Args:
            db_connection: Conexión a la base de datos MongoDB
            mongo_query: Consulta MongoDB a ejecutar (objeto MongoDBQuery o diccionario)
            max_results: Límite máximo de resultados a devolver
            
        Returns:
            Lista de resultados
        """
        # Extraer parámetros de la consulta de manera segura
        collection_name = getattr(mongo_query, "collection", None)
        operation = getattr(mongo_query, "operation", None)
        query_filter = getattr(mongo_query, "query", {})
        projection = getattr(mongo_query, "projection", None)
        sort = getattr(mongo_query, "sort", None)
        limit = getattr(mongo_query, "limit", 10)
        skip = getattr(mongo_query, "skip", 0)
        pipeline = getattr(mongo_query, "pipeline", [])
        
        # Si mongo_query es un diccionario, obtener valores directamente
        if isinstance(mongo_query, dict):
            collection_name = mongo_query.get("collection", collection_name)
            operation = mongo_query.get("operation", operation)
            query_filter = mongo_query.get("query", query_filter)
            projection = mongo_query.get("projection", projection)
            sort = mongo_query.get("sort", sort)
            limit = mongo_query.get("limit", limit or 10)
            skip = mongo_query.get("skip", skip or 0)
            pipeline = mongo_query.get("pipeline", pipeline)
        
        # Validar parámetros
        if not collection_name:
            raise ValueError("Nombre de colección no especificado")
        
        if not operation or operation not in ["find", "aggregate"]:
            logger.warning(f"Operación '{operation}' no válida, usando 'find' como predeterminado")
            operation = "find"
        
        # Limitar resultados
        if not limit or limit > max_results:
            limit = max_results
        
        # Registrar detalles para depuración
        Diagnostic.log_mongodb_query_details(mongo_query, collection_name)
        
        # Ejecutar la consulta
        try:
            # Obtener referencia a la colección
            collection = db_connection[collection_name]
            
            # Verificar que la colección existe
            if collection_name not in await db_connection.list_collection_names():
                logger.warning(f"La colección '{collection_name}' no existe en la base de datos")
                # Intentar encontrar una colección alternativa
                collections = await db_connection.list_collection_names()
                if collections:
                    alternative = collections[0]
                    logger.info(f"Usando colección alternativa: {alternative}")
                    collection = db_connection[alternative]
                else:
                    return []
            
            results = []
            
            if operation == "find":
                # Preparar cursor para find
                cursor = collection.find(
                    filter=query_filter if query_filter else {},
                    projection=projection
                )
                
                # Aplicar sort si está especificado
                if sort:
                    # Convertir sort a lista de tuplas si es un diccionario
                    if isinstance(sort, dict):
                        sort_list = list(sort.items())
                        cursor = cursor.sort(sort_list)
                    else:
                        cursor = cursor.sort(sort)
                
                # Aplicar skip y limit
                cursor = cursor.skip(skip).limit(limit)
                
                # Convertir cursor a lista
                async for document in cursor:
                    results.append(document)
                    
            elif operation == "aggregate":
                # Verificar y completar pipeline
                if not pipeline:
                    pipeline = [{"$match": {}}, {"$limit": limit}]
                else:
                    # Asegurar que hay un límite en el pipeline
                    has_limit = any("$limit" in stage for stage in pipeline)
                    if not has_limit:
                        pipeline.append({"$limit": limit})
                
                # Ejecutar agregación
                cursor = collection.aggregate(pipeline)
                
                # Convertir cursor a lista
                async for document in cursor:
                    results.append(document)
            
            # Registrar número de resultados encontrados
            logger.info(f"Consulta MongoDB completada. Encontrados {len(results)} resultados")
            
            return results
            
        except Exception as e:
            logger.error(f"Error al ejecutar consulta MongoDB: {str(e)}")
            # Realizar diagnóstico de la conexión
            debug_info = await Diagnostic.debug_mongodb_connection(db_connection, collection_name)
            logger.error(f"Diagnóstico MongoDB: {json.dumps(debug_info, default=str)}")
            
            # Re-lanzar la excepción
            raise

