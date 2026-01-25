from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

class FileCreateRequest(BaseModel):
    name: str = Field(..., min_length=1)
    file_type: str = Field(..., pattern="^(file|folder)$")
    parent_id: Optional[int] = None
    content: Optional[str] = None

class FileUpdateRequest(BaseModel):
    name: Optional[str] = None
    content: Optional[str] = None

class FileResponse(BaseModel):
    id: int
    project_id: int
    parent_id: Optional[int]
    name: str
    file_type: str
    content: Optional[str]
    is_active: bool
    created_at: str
    updated_at: str
    
    class Config:
        from_attributes = True

class FileTreeResponse(BaseModel):
    id: int
    name: str
    file_type: str
    children: List['FileTreeResponse'] = []
    
    class Config:
        from_attributes = True

FileTreeResponse.model_rebuild()
