
from pydantic import BaseModel, Field, EmailStr
from datetime import datetime
from typing import Optional, Dict, Any, List

class UserBase(BaseModel):
    email: EmailStr
    name: Optional[str] = None

class UserCreate(UserBase):
    password: str
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)

class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    name: Optional[str] = None
    password: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

class UserInDB(UserBase):
    id: str
    hashed_password: str
    created_at: datetime
    updated_at: datetime
    last_login: Optional[datetime] = None
    active: bool = True
    onboarding: bool = False
    role: str = "user"  # Valores posibles: "user", "admin"
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    model_config = {"from_attributes": True}

class UserResponse(BaseModel):
    id: str
    email: EmailStr
    name: Optional[str] = None
    role: str
    active: bool
    onboarding: bool
    created_at: datetime
    last_login: Optional[datetime] = None
    model_config = {"from_attributes": True}