# kindled
API where Christians can leave notes for eachother. its my first API so don't expect it to be the best

Interactive documentation: [https://kindled.onrender.com/docs](https://kindled.onrender.com/docs)

Search & Filtering

The `/notes/` endpoint supports improved search, filtering, sorting and pagination.

Query parameters:
- q: full-text query (uses MongoDB $text if available; otherwise safe regex)
- type: note type, one of "general" or "prayer_request"
- limit: number of items to return (default 20, max 100)
- skip: number of items to skip (default 0)
- sort_by: "created_at" or "title" (default created_at)
- sort_order: "asc" or "desc" (default desc)
- date_from / date_to: ISO-8601 datetimes to filter creation time

Example curl:
curl "https://kindled.onrender.com/notes/?q=hope&type=general&limit=10&skip=0&sort_by=title&sort_order=asc"

Local testing:
uvicorn main:app --reload

Notes:
- Server creates a MongoDB text index on startup for title+content. If index creation fails, searches fall back to regex.
- Responses include metadata: meta (total_count, limit, skip, returned_count) and data (array of notes).

License: MIT