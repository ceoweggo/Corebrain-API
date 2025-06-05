from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import ConnectionFailure
from app.core.config import settings
from app.core.logging import LogEntry
import asyncio

# Variable global para el cliente MongoDB
client = None
db = None

async def connect_to_mongodb():
    """Establece la conexión a MongoDB"""
    global client, db
    
    try:
        # Crear cliente con opciones de conexión
        client = AsyncIOMotorClient(
            settings.MONGODB.MONGODB_URL,
            maxPoolSize=settings.MONGODB.MAX_CONNECTIONS,
            minPoolSize=settings.MONGODB.MIN_CONNECTIONS,
            connectTimeoutMS=settings.MONGODB.CONNECTION_TIMEOUT
        )
        
        # Verificar conexión
        await client.admin.command("ping")
        
        # Obtener referencia a la base de datos
        db = client[settings.MONGODB.MONGODB_DB_NAME]
        
        LogEntry("mongodb_connected", "info") \
            .add_data("database", settings.MONGODB.MONGODB_DB_NAME) \
            .log()
        print("db: ", db)
        return db
    
    except ConnectionFailure as e:
        LogEntry("mongodb_connection_error", "critical") \
            .add_data("error", str(e)) \
            .log()
        raise
    
    except Exception as e:
        LogEntry("mongodb_error", "critical") \
            .add_data("error", str(e)) \
            .log()
        raise

async def close_mongodb_connection():
    """Cierra la conexión a MongoDB"""
    global client
    if client is not None:  # Also fixed here
        client.close()
        LogEntry("mongodb_disconnected", "info").log()

async def get_database():
    """Obtiene la instancia de la base de datos de forma asíncrona"""
    global db
    if db is None:
        # Intentar conectar si no está conectado
        await connect_to_mongodb()
        if db is None:
            raise RuntimeError("La conexión a MongoDB no pudo establecerse")
    return db