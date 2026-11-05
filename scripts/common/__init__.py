"""Reusable building blocks shared by every application handler."""

from .ai_answers import Answer, Question, answer_question, ask_ollama, is_sensitive
from .candidate_context import (
    CandidateContext,
    get_explicit_profile_value,
    load_candidate_context,
)
from .choices import match_option, normalize_choice
from .logging_utils import get_logger

__all__ = [
    "Answer",
    "CandidateContext",
    "Question",
    "answer_question",
    "ask_ollama",
    "get_explicit_profile_value",
    "get_logger",
    "is_sensitive",
    "load_candidate_context",
    "match_option",
    "normalize_choice",
]
