"""One-time migration: rewrite image_url from ComfyUI /view? to /oss/filename."""
import sqlite3
import urllib.parse
from pathlib import Path
import sys

DB_PATH = Path(__file__).resolve().parent / "data" / "xigua_guonei.db"

def extract_filename(url: str) -> str | None:
    """Extract filename from ComfyUI view URL, e.g.:
    http://127.0.0.1:8188/view?filename=flux_schnell_gguf_00007_.png&subfolder=&type=output
    → /oss/flux_schnell_gguf_00007_.png
    """
    if not url or not url.startswith("http"):
        return None
    try:
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        filenames = qs.get("filename", [])
        if filenames:
            return "/oss/" + filenames[0]
    except Exception:
        pass
    return None


def migrate():
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row

    # Tables with image_url column
    image_tables = ["characters", "scenes", "props", "image_generations", "video_generations"]
    composed_tables = [("storyboards", "composed_image")]

    for table in image_tables:
        rows = db.execute(
            f'SELECT id, image_url FROM "{table}" WHERE image_url LIKE ?',
            ("%127.0.0.1:8188/view%",)
        ).fetchall()
        updated = 0
        for row in rows:
            oss_url = extract_filename(row["image_url"])
            if oss_url:
                db.execute(
                    f'UPDATE "{table}" SET image_url = ? WHERE id = ?',
                    (oss_url, row["id"])
                )
                updated += 1
        if updated:
            print(f"  {table}.image_url: {updated} rows fixed")

    for table, col in composed_tables:
        rows = db.execute(
            f'SELECT id, "{col}" FROM "{table}" WHERE "{col}" LIKE ?',
            ("%127.0.0.1:8188/view%",)
        ).fetchall()
        updated = 0
        for row in rows:
            oss_url = extract_filename(row[col])
            if oss_url:
                db.execute(
                    f'UPDATE "{table}" SET "{col}" = ? WHERE id = ?',
                    (oss_url, row["id"])
                )
                updated += 1
        if updated:
            print(f"  {table}.{col}: {updated} rows fixed")

    db.commit()
    db.close()
    print("Migration complete.")


if __name__ == "__main__":
    migrate()
