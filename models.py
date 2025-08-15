from pydantic import BaseModel, constr

from typing import Optional
from pydantic import BaseModel, constr
from enum import Enum

class NoteType(str, Enum):
    general = "general"
    prayer_request = "prayer_request"
    
class Note(BaseModel):
    title: constr(strip_whitespace=True, min_length=1, max_length=100)
    content: constr(strip_whitespace=True, min_length=1, max_length=2000)
    edit_code: constr(min_length=6, max_length=64)
    type: NoteType
    unique_name: str | None = None
    
class DeleteNote(BaseModel):
    edit_code: constr(min_length=6, max_length=64)