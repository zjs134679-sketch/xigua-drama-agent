"""Frozen backend entry point for the desktop application."""
from __future__ import annotations

import uvicorn

from app.main import app


def main() -> None:
    uvicorn.run(app, host="127.0.0.1", port=5678)


if __name__ == "__main__":
    main()
