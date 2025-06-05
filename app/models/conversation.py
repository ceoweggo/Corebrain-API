from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, Dict, Any, List
import uuid

# APP imports
from app.models.message import MessageResponse

class ConversationBase(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: Optional[str] = None
    api_key_id: Optional[str] = None
    title: str = "New Conversation"
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    last_message_at: Optional[datetime] = None
    message_count: int = 0
    metadata: Dict[str, Any] = Field(default_factory=dict)

class ConversationCreate(ConversationBase):
    user_id: Optional[str] = None
    api_key_id: str
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)

class ConversationUpdate(BaseModel):
    title: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.now)
    last_message_at: Optional[datetime] = None
    message_count: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None

class ConversationInDB(ConversationBase):
    id: str
    user_id: Optional[str] = None
    api_key_id: str
    created_at: datetime
    updated_at: datetime
    last_message_at: Optional[datetime] = None
    message_count: int = 0
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    model_config = {"from_attributes": True}

class ConversationResponse(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    last_message_at: Optional[datetime] = None
    message_count: int
    
    model_config = {"from_attributes": True}

class ConversationWithMessages(ConversationResponse):
    messages: List[MessageResponse] = []
    
    model_config = {"from_attributes": True}