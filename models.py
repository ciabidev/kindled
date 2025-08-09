from pydantic import BaseModel, constr

from typing import Optional
from pydantic import BaseModel, constr

# ---------- Create/Update Models ----------
class Note(BaseModel):
    title: constr(strip_whitespace=True, min_length=1, max_length=100)
    content: constr(strip_whitespace=True, min_length=1, max_length=2000)
    edit_code: constr(min_length=6, max_length=64)  # raw edit code on create
    # unique_name removed — generated automatically

class PrayerRequest(BaseModel):
    title: constr(strip_whitespace=True, min_length=1, max_length=100)
    content: constr(strip_whitespace=True, min_length=1, max_length=2000)
    edit_code: constr(min_length=6, max_length=64)
    # unique_name removed — generated automatically

# ---------- Delete Models ----------
class DeleteNote(BaseModel):
    edit_code: constr(min_length=6, max_length=64)

class DeletePrayerRequest(BaseModel):
    edit_code: constr(min_length=6, max_length=64)
