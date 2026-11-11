"""HTTP API is a phase-2 concern.

v1 ships as a CLI on purpose: developers install it, run it locally, and never
send a schema to our servers. The engine in ``smoker.core`` is transport
agnostic, so the API can be added without touching the generator.
"""
