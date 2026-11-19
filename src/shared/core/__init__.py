"""Dependency-light core of ``shared``: config, logging, DB engine/session helpers, HTTP helpers.

The package defines no tables and imports no application code; ``tests/test_core_is_table_free.py`` pins
that. Schema-aware engine builders belong to the consuming project, not here.
"""
