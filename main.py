from fastapi import FastAPI
from models import Note, PrayerRequest, DeleteNote, DeletePrayerRequest
from db import db
from bson import ObjectId
from typing import Optional
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi.responses import JSONResponse
from fastapi import Request
import os
import uuid
import dotenv
dotenv.load_dotenv()
import hashlib
import datetime
app = FastAPI()
from openai import OpenAI
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
from getstream import Stream
stream_api_key = os.getenv("STREAM_API_KEY")
stream_api_secret = os.getenv("STREAM_API_SECRET")

# ------------------------------
# Helpers
# ------------------------------
from getstream import Stream
import os

stream_api_key = os.getenv("STREAM_API_KEY")
stream_api_secret = os.getenv("STREAM_API_SECRET")

async def is_illegal_content(text: str) -> bool:
    if not stream_api_key or not stream_api_secret:
        raise RuntimeError("Stream API credentials not set in environment.")

    client = Stream(api_key=stream_api_key, api_secret=stream_api_secret)
    # randomly generated entity_id and creator_id
    entity_id = str(uuid.uuid4())
    creator_id = str(uuid.uuid4())
    stream_response = client.moderation.check(
        entity_type="note",
        entity_id="entity_" + entity_id,
        entity_creator_id="user_" + creator_id,
        moderation_payload={"texts": [text], "images": []},
        config_key="custom:kindled",
        options={"force_sync": True}
    )

    # even though its literally not defined if it works dont change it ✅✅✅✅✅
    result: CheckResponse = stream_response.data

    # Pull recommended_action from either top-level or item
    action = getattr(result, "recommended_action", None) or getattr(result.item, "recommended_action", None)

    print("Action:", action)
    return action in ("block", "shadow_block", "remove")



def serialize_doc(doc: dict) -> dict:
    """Serialize MongoDB document to JSON-friendly dict."""
    return {
        "id": str(doc["_id"]),
        "edit_code": str(doc["edit_code"]),
        "title": str(doc["title"]),
        "content": str(doc["content"]),
        "created_at": doc["_id"].generation_time.isoformat(),  # use ObjectId timestamp
        "unique_name": doc.get("unique_name")
    }

import re

import re

async def generate_unique_name(collection, title: str) -> str:
    base_name = title.lower().strip().replace(" ", "-")[:10]
    existing_names = await collection.distinct(
        "unique_name",
        {"unique_name": {"$regex": f"^{base_name}(?:-\\d+)?$", "$options": "i"}}
    )
    counter = 0
    unique_name = base_name
    while unique_name in existing_names:
        counter += 1
        unique_name = f"{base_name}-{counter}"
    print("Existing:", existing_names)
    return unique_name



def hash_code(code: str) -> str:
    """Hash the edit code."""
    return hashlib.sha256(code.encode()).hexdigest()

async def create_document(collection, data: dict) -> Optional[dict]:
    data["unique_name"] = await generate_unique_name(collection, data["title"])
    data["edit_code"] = hash_code(data["edit_code"])
    result = await collection.insert_one(data)
    new_doc = await collection.find_one({"_id": result.inserted_id})
    return serialize_doc(new_doc)

async def edit_document(collection, data: dict) -> Optional[dict]:

    """Edit document by edit_code."""
    data["edit_code"] = hash_code(data["edit_code"])
    updated = await collection.find_one_and_update(
        {"edit_code": data["edit_code"], "unique_name": data.get("unique_name")},
        {"$set": data},
        return_document=True
    )
    return serialize_doc(updated) if updated else None

async def delete_document(collection, data: dict) -> Optional[dict]:
    """Delete document by edit_code and unique_name."""
    data["edit_code"] = hash_code(data["edit_code"])
    result = await collection.delete_one({
        "edit_code": data["edit_code"],
        "unique_name": data.get("unique_name")
    })
    if result.deleted_count > 0:
        return {"deleted": True, "deleted_count": result.deleted_count}
    return None


# ------------------------------
# GET Endpoints
# ------------------------------
@app.get("/")
async def root():
    return {"message": "Welcome to the Kindled API!"}

@app.get("/notes/")
@limiter.limit("10/minute")
async def get_notes(request: Request):
    return [serialize_doc(doc) async for doc in db.notes.find({})], 200

@app.get("/prayer-requests/")
@limiter.limit("10/minute")
async def get_prayer_requests(request: Request):
    return [serialize_doc(doc) async for doc in db.prayer_requests.find({})], 200

# ------------------------------
# POST Endpoints
# ------------------------------
@app.post("/notes/")
@limiter.limit("5/minute")
async def create_note(note: Note, request: Request):
    note = note.model_dump()
    text_to_check = f"{note["title"]} {note["content"]}"
    
    if await is_illegal_content(text_to_check):
        return {"error": "contains prohibited or unsafe content."}, 400
    return await create_document(db.notes, note), 201

@app.post("/prayer-requests/")
@limiter.limit("5/minute")
async def create_prayer_request(prayer_request: PrayerRequest, request: Request):
    prayer_request = prayer_request.model_dump()
    text_to_check = f"{prayer_request["title"]} {prayer_request["content"]}"
    
    if await is_illegal_content(text_to_check):
        return {"error": "contains prohibited or unsafe content."}, 400
    return await create_document(db.prayer_requests, prayer_request), 201

# ------------------------------
# PATCH Endpoints
# ------------------------------
@app.patch("/notes/")
@limiter.limit("10/minute")
async def edit_note(note: Note, request: Request):
    note = note.model_dump()
    text_to_check = f"{note["title"]} {note["content"]}"
    
    if await is_illegal_content(text_to_check):
        return {"error": "contains prohibited or unsafe content."}, 400

    updated = await edit_document(db.notes, note)
    return updated if updated else ({"error": "invalid edit code or note not found"}, 400)

@app.patch("/prayer-requests/")
@limiter.limit("10/minute")
async def edit_prayer_request(prayer_request: PrayerRequest, request: Request):
    prayer_request = prayer_request.model_dump()
    text_to_check = f"{prayer_request["title"]} {prayer_request["content"]}"
    
    if await is_illegal_content(text_to_check):
        return {"error": "contains prohibited or unsafe content."}, 400

    updated = await edit_document(db.prayer_requests, prayer_request)
    return updated if updated else ({"error": "invalid edit code or prayer request not found"}, 400)
# ------------------------------
# DELETE Endpoints
# ------------------------------
@app.delete("/notes/")
@limiter.limit("5/minute")
async def delete_note(note: DeleteNote, request: Request):
    note = note.model_dump()
    deleted = await delete_document(db.notes, note)
    return deleted if deleted else ({"error": "invalid edit code or note not found"}, 400)

@app.delete("/prayer-requests/")
@limiter.limit("5/minute")
async def delete_prayer_request(prayer_request: DeletePrayerRequest, request: Request):
    prayer_request = prayer_request.model_dump()
    deleted = await delete_document(db.prayer_requests, prayer_request)
    return deleted if deleted else ({"error": "invalid edit code or prayer request not found"}, 400)

@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request, exc):
    return JSONResponse(
        status_code=429,
        content={"error": "Too many requests, matcha 24 karat labubu dubai chocolate benson boonbeam it's not clocking to you that i'm standing on moonbeam 6 7 crumble cookie"},
    )