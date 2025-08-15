# --- Standard library ---
import datetime
import hashlib
import os
import random
import re
import unicodedata
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

# Ensure a MongoDB text index exists for fast, relevant text searches.
# This runs on startup and will not fail the app if index creation errors.
@app.on_event("startup")
async def ensure_text_index():
    try:
        # create a combined text index on title and content for efficient $text searches
        await db.notes.create_index([("title", "text"), ("content", "text")], name="title_content_text_idx")
    except Exception as e:
        # Log and continue -- searching will fall back to regex if $text is not available.
        print("ensure_text_index: failed to create text index:", e)

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

def sanitize_search_query(q: str, max_length: int = 500) -> str:
    """
    Sanitize a user-provided search query to handle special characters and control bytes.

    Steps:
    - Unicode normalize to NFKC.
    - Remove control characters (0x00-0x1F).
    - Collapse whitespace to single spaces and trim.
    - Truncate to max_length characters.
    """
    if q is None:
        return q
    # Normalize unicode to a stable form
    s = unicodedata.normalize("NFKC", q)
    # Remove C0 control characters (including null, bell, etc.)
    s = re.sub(r'[\x00-\x1F]+', ' ', s)
    # Collapse whitespace and trim
    s = re.sub(r'\s+', ' ', s).strip()
    # Truncate to a safe length
    if len(s) > max_length:
        s = s[:max_length]
    return s

# easy to pronounce and spell and remember
BIBLE_WORDS = [
    # Common nouns / concepts
    "light", "hope", "peace", "grace", "joy", "truth", "vine", "lamb",
    "seed", "star", "bread", "rock", "path", "gift", "ark", "fish",
    "well", "door", "oil", "crown", "prayer", "bible", "church", "faith",
    "love", "mercy", "praise", "shepherd", "soul", "wisdom"

    # Names
    "abel", "levi", "amos", "noah", "ruth", "ezra", "luke", "mark",
    "joel", "paul", "john", "mary", "anna", "adam", "eve", "matthew",
    "david", "samuel", "joseph", "elijah", "benjamin", "isaac", "jacob",

    # Nature / imagery
    "river", "hill", "rain", "water", "wind", "sun", "fig", "sand", "stone", "water", "cloud", "mountain", "tree", "flower",
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

# ai lowk carried this function
@app.get("/notes/")
async def list_notes(
    request: Request,
    q: Annotated[Optional[str], Query(None, alias="q")] = None,
    note_type: Annotated[Optional[NoteType], Query(None, alias="type")] = None,
    limit: Annotated[int, Query(20, ge=1, le=100)] = 20,
    skip: Annotated[int, Query(0, ge=0)] = 0,
    sort_by: Annotated[str, Query("created_at", alias="sort_by")] = "created_at",
    sort_order: Annotated[str, Query("desc", alias="sort_order", regex="^(asc|desc)$")] = "desc",
    date_from: Annotated[Optional[str], Query(None, alias="date_from")] = None,
    date_to: Annotated[Optional[str], Query(None, alias="date_to")] = None,
):
    """
    List notes with improved search and filtering.

    Query params:
    - q: full-text query (uses $text when available, falls back to regex)
    - type: filter by note type (general, prayer_request)
    - limit: number of items to return (1-100)
    - skip: number of items to skip (for pagination)
    - sort_by: 'created_at' or 'title'
    - sort_order: 'asc' or 'desc'
    - date_from/date_to: ISO-8601 datetimes to filter by creation time
    """
    query: dict = {}

    # filter by explicit type
    if note_type:
        query["type"] = note_type.value

    # filter by date range using ObjectId timestamp if provided
    id_query: dict = {}
    if date_from:
        try:
            dt = datetime.datetime.fromisoformat(date_from)
            id_query["$gte"] = ObjectId.from_datetime(dt)
        except Exception:
            pass
    if date_to:
        try:
            dt = datetime.datetime.fromisoformat(date_to)
            id_query["$lte"] = ObjectId.from_datetime(dt)
        except Exception:
            pass
    if id_query:
        query["_id"] = id_query

    # Prepare search: prefer $text (requires text index); fallback to escaped regex.
    # Sanitize user input to handle special characters/control bytes before using it.
    final_query = dict(query)  # may be modified if fallback needed
    if q:
        sanitized_q = sanitize_search_query(q)
        # If sanitization strips the query to empty, treat as no-search.
        if not sanitized_q:
            total = await db.notes.count_documents(final_query)
        else:
            # Try to use $text; if it fails at execution, fall back to regex approach.
            try:
                # test a count to see if $text is supported / index exists
                test_q = {**query, **{"$text": {"$search": sanitized_q}}}
                total = await db.notes.count_documents(test_q)
                final_query = test_q
            except Exception:
                # fallback: safe regex search (escape sanitized user input)
                escaped = re.escape(sanitized_q)
                regex_query = {"$or": [
                    {"title": {"$regex": escaped, "$options": "i"}},
                    {"content": {"$regex": escaped, "$options": "i"}}
                ]}
                final_query = {**query, **regex_query}
                total = await db.notes.count_documents(final_query)
    else:
        total = await db.notes.count_documents(final_query)

    # Determine sort field mapping
    sort_map = {"created_at": "_id", "title": "title"}
    sort_field = sort_map.get(sort_by, "_id")
    direction = 1 if sort_order == "asc" else -1

    cursor = db.notes.find(final_query).sort(sort_field, direction).skip(skip).limit(limit)
    data = [serialize_doc(doc) async for doc in cursor]

    meta = {
        "total_count": total,
        "limit": limit,
        "skip": skip,
        "returned_count": len(data),
    }

    return JSONResponse(content={"meta": meta, "data": data}, status_code=200)

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
