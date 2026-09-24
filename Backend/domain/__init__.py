"""Shared database tables and API schemas.

    models.py   ORM tables: Clients (a company / tenant), Users, Batches, call logs,
                conversations, recordings. LeadAI's own tables live in LeadAI/models*.py.
    schema.py   Pydantic request / response models for the batch-calling API.
"""
import sys

from . import models, schema  # noqa: F401

sys.modules.setdefault("domain", sys.modules[__name__])
sys.modules.setdefault("domain.models", models)
sys.modules.setdefault("domain.schema", schema)
