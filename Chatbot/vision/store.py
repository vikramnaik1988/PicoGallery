"""
store.py — SQLite metadata store for vision pipeline results.

Database: Chatbot/vision_metadata.db
One row per photo. Tags stored as space-separated string for LIKE queries.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent.parent / "vision_metadata.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS photos (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_id    TEXT UNIQUE,
                path        TEXT NOT NULL,
                filename    TEXT,
                date_taken  TEXT,
                gps_lat     REAL,
                gps_lon     REAL,
                camera_model TEXT,
                faces       INTEGER DEFAULT 0,
                scene       TEXT,
                ocr_text    TEXT,
                tags        TEXT DEFAULT '',
                indexed_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tags ON photos(tags)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_scene ON photos(scene)")
        conn.commit()


def upsert(
    path: str,
    asset_id: str = "",
    date_taken: Optional[datetime] = None,
    gps_lat: Optional[float] = None,
    gps_lon: Optional[float] = None,
    camera_model: Optional[str] = None,
    faces: int = 0,
    scene: str = "",
    ocr_text: str = "",
    tags: list[str] = [],
) -> None:
    """Insert or update a photo's metadata."""
    init_db()
    filename = Path(path).name
    tags_str = " ".join(sorted(set(t.lower() for t in tags if t)))
    date_str = date_taken.isoformat() if date_taken else None

    with _connect() as conn:
        conn.execute("""
            INSERT INTO photos
                (asset_id, path, filename, date_taken, gps_lat, gps_lon,
                 camera_model, faces, scene, ocr_text, tags)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(asset_id) DO UPDATE SET
                path=excluded.path,
                filename=excluded.filename,
                date_taken=excluded.date_taken,
                gps_lat=excluded.gps_lat,
                gps_lon=excluded.gps_lon,
                camera_model=excluded.camera_model,
                faces=excluded.faces,
                scene=excluded.scene,
                ocr_text=excluded.ocr_text,
                tags=excluded.tags,
                indexed_at=CURRENT_TIMESTAMP
        """, (
            asset_id or filename,
            path, filename, date_str,
            gps_lat, gps_lon, camera_model,
            faces, scene, ocr_text, tags_str,
        ))
        conn.commit()


def search(
    objects: list[str] = [],
    scenes: list[str] = [],
    attributes: list[str] = [],
    persons: list[str] = [],
    has_faces: Optional[bool] = None,
    limit: int = 20,
) -> list[dict]:
    """
    Search photos by tags/scene.
    Returns list of dicts with path, filename, scene, tags, faces.
    """
    init_db()
    terms = [t.lower() for t in objects + scenes + attributes if t]

    if not terms and has_faces is None:
        return []

    clauses = []
    params: list = []

    for term in terms:
        clauses.append("tags LIKE ?")
        params.append(f"%{term}%")

    if has_faces is True:
        clauses.append("faces > 0")
    elif has_faces is False:
        clauses.append("faces = 0")

    where = " AND ".join(clauses) if clauses else "1=1"

    with _connect() as conn:
        rows = conn.execute(
            f"SELECT path, filename, scene, tags, faces, date_taken "
            f"FROM photos WHERE {where} ORDER BY indexed_at DESC LIMIT ?",
            params + [limit],
        ).fetchall()

    return [dict(r) for r in rows]


def count() -> int:
    """Total number of indexed photos."""
    init_db()
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]


def already_indexed(path: str) -> bool:
    """Check if a photo path has already been indexed."""
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT id FROM photos WHERE path=?", (path,)
        ).fetchone()
    return row is not None
