"""Prompts REST API — extended in a later task."""
from fastapi import APIRouter

router = APIRouter(prefix="/api/prompts", tags=["prompts"])
