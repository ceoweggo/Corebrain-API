
import logging
import json
from datetime import datetime
from typing import Dict, Any, Optional
import uuid
import os

# Configuración básica
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)

# Crear logger
logger = logging.getLogger("corebrain")

def get_request_id() -> str:
    """Genera un ID único para la solicitud"""
    return str(uuid.uuid4())

class LogEntry:
    def __init__(self, event: str, level: str = "info"):
        self.event = event
        self.level = level
        self.timestamp = datetime.now().isoformat()
        self.data = {}
        self.request_id = get_request_id()
        self.user_id = None
        self.api_key_id = None
    
    def add_data(self, key: str, value: Any) -> 'LogEntry':
        """Añade datos al log"""
        self.data[key] = value
        return self
    
    def set_user_id(self, user_id: str) -> 'LogEntry':
        """Establece el ID de usuario"""
        self.user_id = user_id
        return self
    
    def set_api_key_id(self, api_key_id: str) -> 'LogEntry':
        """Establece el ID de la API key"""
        self.api_key_id = api_key_id
        return self
    
    def to_dict(self) -> Dict[str, Any]:
        """Convierte el log a diccionario"""
        result = {
            "event": self.event,
            "level": self.level,
            "timestamp": self.timestamp,
            "request_id": self.request_id,
            "data": self.data
        }
        
        if self.user_id:
            result["user_id"] = self.user_id
        
        if self.api_key_id:
            result["api_key_id"] = self.api_key_id
        
        return result
    
    def log(self) -> None:
        """Registra el log"""
        log_dict = self.to_dict()
        log_json = json.dumps(log_dict)
        
        if self.level == "debug":
            logger.debug(log_json)
        elif self.level == "info":
            logger.info(log_json)
        elif self.level == "warning":
            logger.warning(log_json)
        elif self.level == "error":
            logger.error(log_json)
        elif self.level == "critical":
            logger.critical(log_json)