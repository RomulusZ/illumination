"""Stable repository adapter errors."""

from __future__ import annotations


class RepoError(Exception):
    """Base for expected adapter failures."""


class LLMAdapterError(RepoError):
    """The model endpoint cannot be reached or returned an unusable payload."""


class ArtifactStoreError(RepoError):
    """An artifact cannot be written or located."""
