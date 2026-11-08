"""Shared answer limits: policy, schema and runtime use the same definitions.

These are code contracts, not a second configuration or a permissions system.
"""
MAX_REPLY_CHARS = 4000
TARGET_REPLY_CHARS = 3500
MAX_SOURCES = 8
MAX_MEMORIES = 6
MAX_RESEARCH_REQUESTS = 4  # Shared by news, timelines and post batches.
