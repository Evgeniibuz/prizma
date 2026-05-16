"""
Services package initialization.

Whale tracking and derivatives services were removed — see git history
if you need to resurrect them. Only social sentiment is kept.
"""
from .sentiment import SentimentService

__all__ = ["SentimentService"]
