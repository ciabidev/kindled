from fastapi import FastAPI
from models import Entry, DeleteEntry
from db import db
from bson import ObjectId
from typing import Optional
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from enum import Enum
from pydantic import BaseModel, constr
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
# ROOT
# ------------------------------
@app.get("/")
async def root():
    return JSONResponse(
        content={"message": "kindled is running!"},
        status_code=status.HTTP_200_OK
    )


# ------------------------------
# ENTRIES
# ------------------------------

class EntryType(str, Enum):
    note = "note"
    prayer = "prayer"
    
@app.get("/entries/{entry_type}/")
@limiter.limit("10/minute")
async def list_entries(entry_type: EntryType, request: Request):
    """List all entries of a given type."""
    data = [serialize_doc(doc) async for doc in db.entries.find({"type": entry_type.value})]
    return JSONResponse(content=data, status_code=200)


@app.get("/entries/{entry_type}/{unique_name}")
@limiter.limit("10/minute")
async def get_entry(entry_type: EntryType, unique_name: str, request: Request):
    """Get a single entry by type and unique_name."""
    entry = await db.entries.find_one({"type": entry_type.value, "unique_name": unique_name})
    if not entry:
        return JSONResponse(content={"error": "not found"}, status_code=404)
    return JSONResponse(content=serialize_doc(entry), status_code=200)


@app.post("/entries/{entry_type}/")
@limiter.limit("5/minute")
async def create_entry(entry_type: EntryType, entry: Entry, request: Request):
    """Create a new entry (note or prayer)."""
    entry_data = entry.model_dump()
    entry_data["type"] = entry_type.value

    text_to_check = f"{entry_data['title']} {entry_data['content']}"
    if await is_illegal_content(text_to_check):
        return JSONResponse(content={"error": "contains prohibited or unsafe content."}, status_code=400)

    data = await create_document(db.entries, entry_data)
    return JSONResponse(content=data, status_code=201)


@app.patch("/entries/{entry_type}/{unique_name}")
@limiter.limit("10/minute")
async def edit_entry(entry_type: EntryType, unique_name: str, entry: Entry, request: Request):
    """Edit an entry by type and unique_name."""
    entry_data = entry.model_dump()
    text_to_check = f"{entry_data['title']} {entry_data['content']}"
    if await is_illegal_content(text_to_check):
        return JSONResponse(content={"error": "contains prohibited or unsafe content."}, status_code=400)

    updated = await edit_document(db.entries, {**entry_data, "type": entry_type.value, "unique_name": unique_name})
    return JSONResponse(content=updated if updated else {"error": "invalid edit code or entry not found"},
                        status_code=200 if updated else 400)


@app.delete("/entries/{entry_type}/{unique_name}")
@limiter.limit("5/minute")
async def delete_entry(entry_type: EntryType, unique_name: str, entry: DeleteEntry, request: Request):
    """Delete an entry by type and unique_name."""
    deleted = await delete_document(db.entries, {"type": entry_type.value, "unique_name": unique_name, "edit_code": entry.edit_code})
    return JSONResponse(content=deleted if deleted else {"error": "invalid edit code or entry not found"},
                        status_code=200 if deleted else 400)

# ------------------------------
# ERROR HANDLING
# ------------------------------
@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request, exc):
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={"error": "Too many requests, matcha 24 karat labubu dubai chocolate benson boonbeam it's not clocking to you that i'm standing on moonbeam 6 7 crumble cookie"},
    )
