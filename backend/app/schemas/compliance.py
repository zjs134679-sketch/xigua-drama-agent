from __future__ import annotations

from pydantic import BaseModel


class ComplianceCheckRequest(BaseModel):
    text: str
