from pydantic import BaseModel, constr

class Note(BaseModel):
    title: constr(strip_whitespace=True, min_length=1, max_length=100)
    content: constr(strip_whitespace=True, min_length=1, max_length=2000)
    edit_code: constr(min_length=6, max_length=64)
    unique_name: str = None

class PrayerRequest(BaseModel):
    title: constr(strip_whitespace=True, min_length=1, max_length=100)
    content: constr(strip_whitespace=True, min_length=1, max_length=2000)
    edit_code: constr(min_length=6, max_length=64)
    unique_name: str = None

class DeleteNote(BaseModel):
    edit_code: constr(min_length=6, max_length=64)
    unique_name: str = None

class DeletePrayerRequest(BaseModel):
    edit_code: constr(min_length=6, max_length=64)
    unique_name: str = None

# class UserSignup(BaseModel):
#     email: str
#     username: str
#     password: str # hashed

# class UserLogin(BaseModel):
#     email: str
#     password: str

# class UserUpdate(BaseModel):
#     username: str = None
#     email: str = None
#     password: str = None # hashed

# class User(BaseModel):
#     email: str
#     username: str
#     password: str # hashed