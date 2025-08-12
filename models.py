from pydantic import BaseModel, constr

from typing import Optional
from pydantic import BaseModel, constr
from enum import Enum

class EntryType(str, Enum):
    note = "note"
    prayer = "prayer"
    
class Entry(BaseModel):
    title: constr(strip_whitespace=True, min_length=1, max_length=100)
    content: constr(strip_whitespace=True, min_length=1, max_length=2000)
    edit_code: constr(min_length=6, max_length=64)
    entry_type: EntryType = Field(..., alias="type")
    unique_name: str | None = None
    
class DeleteEntry(BaseModel):
    edit_code: constr(min_length=6, max_length=64)