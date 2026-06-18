"""SYNAPSE evaluation harness.

A standalone package that measures whether SYNAPSE's semantic tool compression
improves LLM-agent task success. It drives SYNAPSE entirely over HTTP and executes
the *real* generated MCP server, so it imports nothing from ``backend/``.
"""

__version__ = "0.1.0"

# The fixed experiment seed — every stochastic component (id generation, faker,
# agent sampling) is pinned to this for byte-reproducible runs.
SEED = 42
