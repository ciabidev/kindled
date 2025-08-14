# --- Standard library ---
import datetime
import hashlib
import os
import random
import re
import string
import uuid

# --- Third-party packages ---
from bson import ObjectId
from dotenv import load_dotenv
from enum import Enum
from fastapi import FastAPI, Query, Request, status
from fastapi.responses import JSONResponse
from getstream import Stream
from openai import OpenAI
from pydantic import BaseModel, constr
import sentry_sdk
from typing import Optional, Annotated
from random_word import RandomWords
from slugify import slugify
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

# --- Local modules ---
from db import db
from models import Note, DeleteNote
stream_api_key = os.getenv("STREAM_API_KEY")
stream_api_secret = os.getenv("STREAM_API_SECRET")
# ------------------------------
# initialization
# ------------------------------

app = FastAPI()
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter

sentry_sdk.init(
    dsn="https://1b32260ea9e47a42922d92b34b2e48c2@o4509832492220416.ingest.us.sentry.io/4509832505720832",
    # Add data like request headers and IP for users,
    # see https://docs.sentry.io/platforms/python/data-management/data-collected/ for more info
    send_default_pii=True,
)

@app.get("/sentry-debug")
async def trigger_error():
    division_by_zero = 1 / 0

reset_db = False
if reset_db:
    db.notes.drop()
    db.entries.drop()
    db.prayer_requests.drop()

stream_api_key = os.getenv("STREAM_API_KEY")
stream_api_secret = os.getenv("STREAM_API_SECRET")

# ------------------------------
# Helpers
# ------------------------------

async def is_illegal_content(text: str) -> bool:
    if not stream_api_key or not stream_api_secret:
        raise RuntimeError("Stream API credentials not set in environment.")

    client = Stream(api_key=stream_api_key, api_secret=stream_api_secret)
    entity_id = str(uuid.uuid4())
    creator_id = str(uuid.uuid4())
    response = client.moderation.check(
        entity_type="general",
        entity_id="entity_" + entity_id,
        entity_creator_id="user_" + creator_id,
        moderation_payload={"texts": [text], "images": []},
        config_key="custom:kindled",
        options={"force_sync": True}
    )
    result = response.data
    print(result)
    action = getattr(result, "recommended_action", None) or getattr(result.item, "recommended_action", None)

    print("Action:", action)

    is_blocked = action in ("block", "shadow_block", "remove")
    return is_blocked



def serialize_doc(doc: dict) -> dict:
    """Serialize MongoDB document to JSON-friendly dict."""
    return {
        "id": str(doc["_id"]),
        "title": str(doc["title"]),
        "content": str(doc["content"]),
        "created_at": doc["_id"].generation_time.isoformat(),  # use ObjectId timestamp
        "unique_name": doc.get("unique_name"),
        "type": doc.get("type")
    }

# easy to pronounce and spell and remember
BIBLE_WORDS = [
    # Common nouns / concepts
    "light", "hope", "peace", "grace", "joy", "truth", "vine", "lamb",
    "seed", "star", "bread", "rock", "path", "gift", "ark", "fish",
    "well", "door", "oil", "crown",

    # Names
    "abel", "levi", "amos", "noah", "ruth", "ezra", "luke", "mark",
    "joel", "paul", "john", "mary", "anna", "adam", "eve", "matthew",
    "david", "samuel", "joseph", "elijah", "benjamin", "isaac", "jacob",

    # Nature / imagery
    "river", "hill", "rain", "water", "wind", "sun", "fig", "oak", "leaf",
    "sand", "stone", "water", "cloud", "mountain", "tree", "flower",
]

async def generate_unique_name(collection, title: str) -> str:
    unique_name = ""
    word1 = random.choice(BIBLE_WORDS)
    word2 = random.choice(BIBLE_WORDS)

    base_slug = f"{word1}-{word2}"
    pattern = f"^{re.escape(base_slug)}(?:-\\d+)?$"
    existing_names = await collection.distinct(
        "unique_name",
        {"unique_name": {"$regex": pattern, "$options": "i"}}
    )

    counter = 0
    unique_name = base_slug
    suffix = ""
    while unique_name.lower() in (name.lower() for name in existing_names):
        counter += 1
        suffix = f"-{counter}"
        unique_name = f"{word1}-{word2}{suffix}"
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
# NOTES
# ------------------------------

class NoteType(str, Enum):
    general = "general"
    prayer_request = "prayer_request"
    
@app.get("/notes/")
async def list_notes(request: Request, note_type: Annotated[NoteType | None, Query(alias="text-filter")] = None, text_filter: Annotated[str | None, Query(alias="text-filter")] = None):
    """List all notes, optionally filter by type and what the title/content contains."""
    query = {}
    if note_type:
        query["type"] = note_type.value
    if text_filter:
        # no edit codes
        query["$or"] = [
            {"title": {"$regex": text_filter, "$options": "i"}},
            {"content": {"$regex": text_filter, "$options": "i"}}
        ]

    data = [serialize_doc(doc) async for doc in db.notes.find(query)]
    return JSONResponse(content=data, status_code=200)

@app.get("/notes/{unique_name}")
async def get_note(unique_name: str, request: Request):
    """Get an note by unique_name."""
    note = await db.notes.find_one({"unique_name": unique_name})
    if not note:
        return JSONResponse(content={"error": "not found"}, status_code=404)
    return JSONResponse(content=serialize_doc(note), status_code=200)

@app.post("/notes/")
@limiter.limit("10/minute")
async def create_note(note: Note, request: Request):
    """Create a new note."""
    note_data = note.model_dump()

    text_to_check = f"{note_data['title']} {note_data['content']}"
    if await is_illegal_content(text_to_check):
        return JSONResponse(content={"error": "contains prohibited or unsafe content."}, status_code=400)

    data = await create_document(db.notes, note_data)
    return JSONResponse(content=data, status_code=201)

@app.patch("/notes/{unique_name}")
async def edit_note(unique_name: str, note: Note, request: Request):
    """Edit an note by unique_name."""
    note_data = note.model_dump()
    text_to_check = f"{note_data['title']} {note_data['content']}"
    if await is_illegal_content(text_to_check):
        return JSONResponse(content={"error": "contains prohibited or unsafe content.", "type":""}, status_code=400)

    updated = await edit_document(db.notes, {**note_data, "unique_name": unique_name})
    return JSONResponse(content=updated if updated else {"error": "invalid edit code or note not found"},
                        status_code=200 if updated else 400)

@app.delete("/notes/{unique_name}")
async def delete_note(unique_name: str, note: DeleteNote, request: Request):
    """Delete an note by unique_name."""
    deleted = await delete_document(db.notes, {"unique_name": unique_name, "edit_code": note.edit_code})
    return JSONResponse(content=deleted if deleted else {"error": "invalid edit code or note not found"},
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
