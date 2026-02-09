from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

openapi_tags = [
    {
        "name": "Health",
        "description": "Service health and diagnostics.",
    },
    {
        "name": "Todos",
        "description": "CRUD operations for todo items.",
    },
]

app = FastAPI(
    title="Todo Backend API",
    description="FastAPI backend for a simple Todo app backed by SQLite.",
    version="0.1.0",
    openapi_tags=openapi_tags,
)

# CORS: allow frontend to call backend. Kept permissive to avoid cross-origin issues
# in multi-container environments.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _default_db_path() -> str:
    """
    Returns the default stable path to the SQLite DB file produced by the database container.

    We prefer the SQLITE_DB environment variable (provided by the database container as per
    project config). If it's not set, we fall back to the known workspace path.
    """
    # Env var comes from the "database" container definition: db_env_vars = SQLITE_DB
    env_path = os.getenv("SQLITE_DB")
    if env_path:
        return env_path

    # Fallback stable workspace path (useful for local dev / CI).
    return "/home/kavia/workspace/code-generation/simple-todo-application-216308-216321/database/myapp.db"


def _get_connection() -> sqlite3.Connection:
    """Create a SQLite connection with helpful defaults and row access by column name."""
    db_path = _default_db_path()
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def _db() -> sqlite3.Connection:
    """Context manager that ensures a DB connection is properly closed."""
    conn = _get_connection()
    try:
        yield conn
    finally:
        conn.close()


def _row_to_todo_dict(row: sqlite3.Row) -> Dict[str, Any]:
    """Convert a SQLite row to the canonical Todo JSON shape."""
    return {
        "id": int(row["id"]),
        "title": str(row["title"]),
        "completed": bool(row["completed"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _utc_now_iso() -> str:
    """Return an ISO-8601 timestamp for updated_at."""
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


class ErrorResponse(BaseModel):
    """Standard error response shape used by this API."""

    detail: str = Field(..., description="Human-readable error message.")


class Todo(BaseModel):
    """Todo item returned by the API."""

    id: int = Field(..., description="Todo id (auto-increment primary key).", examples=[1])
    title: str = Field(..., description="Title/content of the todo.", examples=["Buy milk"])
    completed: bool = Field(
        ...,
        description="Whether the todo is completed.",
        examples=[False],
    )
    created_at: str = Field(
        ...,
        description="Creation timestamp as stored by SQLite (text).",
    )
    updated_at: Optional[str] = Field(
        None,
        description="Update timestamp (ISO-8601 UTC) set by the API when modified.",
        examples=["2026-02-09T16:00:00Z"],
    )


class TodoCreate(BaseModel):
    """Request body for creating a todo."""

    title: str = Field(..., min_length=1, max_length=500, description="Todo title.")
    completed: bool = Field(
        False,
        description="Optional initial completed status.",
    )


class TodoUpdate(BaseModel):
    """Request body for updating a todo (partial update)."""

    title: Optional[str] = Field(None, min_length=1, max_length=500, description="Todo title.")
    completed: Optional[bool] = Field(None, description="Completed status.")


@app.get(
    "/",
    tags=["Health"],
    summary="Health check",
    description="Returns a simple health response.",
    operation_id="health_check",
)
# PUBLIC_INTERFACE
def health_check() -> Dict[str, str]:
    """Health check endpoint for monitoring and smoke tests."""
    return {"message": "Healthy"}


@app.get(
    "/todos",
    response_model=List[Todo],
    tags=["Todos"],
    summary="List todos",
    description="Return all todos ordered by id descending (newest first).",
    operation_id="list_todos",
    responses={
        200: {"description": "List of todos."},
        500: {"model": ErrorResponse, "description": "Database error."},
    },
)
# PUBLIC_INTERFACE
def list_todos() -> List[Dict[str, Any]]:
    """List all todos."""
    try:
        with _db() as conn:
            rows = conn.execute(
                "SELECT id, title, completed, created_at, updated_at FROM todos ORDER BY id DESC"
            ).fetchall()
            return [_row_to_todo_dict(r) for r in rows]
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}") from e


@app.post(
    "/todos",
    response_model=Todo,
    status_code=201,
    tags=["Todos"],
    summary="Create todo",
    description="Create a new todo and return it.",
    operation_id="create_todo",
    responses={
        201: {"description": "Created todo."},
        400: {"model": ErrorResponse, "description": "Invalid input."},
        500: {"model": ErrorResponse, "description": "Database error."},
    },
)
# PUBLIC_INTERFACE
def create_todo(payload: TodoCreate) -> Dict[str, Any]:
    """Create a todo."""
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="title must not be empty")

    try:
        with _db() as conn:
            cur = conn.execute(
                "INSERT INTO todos (title, completed, updated_at) VALUES (?, ?, ?)",
                (title, 1 if payload.completed else 0, None),
            )
            todo_id = int(cur.lastrowid)
            conn.commit()

            row = conn.execute(
                "SELECT id, title, completed, created_at, updated_at FROM todos WHERE id = ?",
                (todo_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=500, detail="Failed to read created todo")

            return _row_to_todo_dict(row)
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}") from e


@app.get(
    "/todos/{todo_id}",
    response_model=Todo,
    tags=["Todos"],
    summary="Get todo",
    description="Get a single todo by id.",
    operation_id="get_todo",
    responses={
        200: {"description": "Todo found."},
        404: {"model": ErrorResponse, "description": "Todo not found."},
        500: {"model": ErrorResponse, "description": "Database error."},
    },
)
# PUBLIC_INTERFACE
def get_todo(todo_id: int) -> Dict[str, Any]:
    """Fetch a todo by id."""
    try:
        with _db() as conn:
            row = conn.execute(
                "SELECT id, title, completed, created_at, updated_at FROM todos WHERE id = ?",
                (todo_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Todo not found")
            return _row_to_todo_dict(row)
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}") from e


@app.patch(
    "/todos/{todo_id}",
    response_model=Todo,
    tags=["Todos"],
    summary="Update todo (partial)",
    description="Partially update a todo. Only provided fields will be updated.",
    operation_id="patch_todo",
    responses={
        200: {"description": "Updated todo."},
        400: {"model": ErrorResponse, "description": "Invalid input."},
        404: {"model": ErrorResponse, "description": "Todo not found."},
        500: {"model": ErrorResponse, "description": "Database error."},
    },
)
# PUBLIC_INTERFACE
def patch_todo(todo_id: int, payload: TodoUpdate) -> Dict[str, Any]:
    """Partially update a todo."""
    if payload.title is None and payload.completed is None:
        raise HTTPException(status_code=400, detail="No fields provided to update")

    updates: List[str] = []
    params: List[Any] = []

    if payload.title is not None:
        title = payload.title.strip()
        if not title:
            raise HTTPException(status_code=400, detail="title must not be empty")
        updates.append("title = ?")
        params.append(title)

    if payload.completed is not None:
        updates.append("completed = ?")
        params.append(1 if payload.completed else 0)

    # Always bump updated_at when a change is made.
    updates.append("updated_at = ?")
    params.append(_utc_now_iso())

    params.append(todo_id)

    try:
        with _db() as conn:
            cur = conn.execute(
                f"UPDATE todos SET {', '.join(updates)} WHERE id = ?",
                tuple(params),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Todo not found")

            conn.commit()
            row = conn.execute(
                "SELECT id, title, completed, created_at, updated_at FROM todos WHERE id = ?",
                (todo_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Todo not found")
            return _row_to_todo_dict(row)
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}") from e


@app.put(
    "/todos/{todo_id}",
    response_model=Todo,
    tags=["Todos"],
    summary="Replace todo",
    description="Replace a todo's fields (title and completed).",
    operation_id="put_todo",
    responses={
        200: {"description": "Replaced todo."},
        400: {"model": ErrorResponse, "description": "Invalid input."},
        404: {"model": ErrorResponse, "description": "Todo not found."},
        500: {"model": ErrorResponse, "description": "Database error."},
    },
)
# PUBLIC_INTERFACE
def put_todo(todo_id: int, payload: TodoCreate) -> Dict[str, Any]:
    """Replace a todo (full update)."""
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="title must not be empty")

    try:
        with _db() as conn:
            cur = conn.execute(
                "UPDATE todos SET title = ?, completed = ?, updated_at = ? WHERE id = ?",
                (title, 1 if payload.completed else 0, _utc_now_iso(), todo_id),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Todo not found")

            conn.commit()
            row = conn.execute(
                "SELECT id, title, completed, created_at, updated_at FROM todos WHERE id = ?",
                (todo_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Todo not found")
            return _row_to_todo_dict(row)
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}") from e


@app.delete(
    "/todos/{todo_id}",
    status_code=204,
    tags=["Todos"],
    summary="Delete todo",
    description="Delete a todo by id.",
    operation_id="delete_todo",
    responses={
        204: {"description": "Todo deleted."},
        404: {"model": ErrorResponse, "description": "Todo not found."},
        500: {"model": ErrorResponse, "description": "Database error."},
    },
)
# PUBLIC_INTERFACE
def delete_todo(todo_id: int) -> Response:
    """Delete a todo."""
    try:
        with _db() as conn:
            cur = conn.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Todo not found")
            conn.commit()
        return Response(status_code=204)
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}") from e
