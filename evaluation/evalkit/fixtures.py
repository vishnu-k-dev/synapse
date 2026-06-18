"""Local tool fixtures for offline / keyless runs.

PETSTORE_NAIVE_TOOLS is a hand-written "1 tool per endpoint" surface for the Petstore
spec, shaped exactly like SynthesisEngine._build_tool_context output. It lets the harness
run end-to-end (and approximates the C1 naive condition) without standing up the SYNAPSE
backend. The live ablation gets its tool sets from SYNAPSE itself.
"""
from __future__ import annotations

from typing import Any

PETSTORE_APP_NAME = "Petstore"

PETSTORE_NAIVE_TOOLS: list[dict[str, Any]] = [
    {"name": "list_pets", "description": "List pets, optionally filtered by status.",
     "method": "GET", "path": "/pets", "has_body": False, "required_params": [],
     "optional_params": [{"arg": "limit", "type": "int"}, {"arg": "status", "type": "str"}],
     "query_args": [{"name": "limit", "arg": "limit"}, {"name": "status", "arg": "status"}],
     "body_args": []},
    {"name": "get_pet", "description": "Fetch a single pet by id.",
     "method": "GET", "path": "/pets/{pet_id}", "has_body": False,
     "required_params": [{"arg": "pet_id", "type": "str"}],
     "optional_params": [], "query_args": [], "body_args": []},
    {"name": "create_pet", "description": "Create a new pet. Returns the pet with its id.",
     "method": "POST", "path": "/pets", "has_body": True,
     "required_params": [{"arg": "name", "type": "str"}, {"arg": "species", "type": "str"}],
     "optional_params": [{"arg": "breed", "type": "str"}, {"arg": "owner_id", "type": "str"}],
     "query_args": [],
     "body_args": [{"name": "name", "arg": "name"}, {"name": "species", "arg": "species"},
                   {"name": "breed", "arg": "breed"}, {"name": "owner_id", "arg": "owner_id"}]},
    {"name": "update_pet", "description": "Update a pet, e.g. set status to sold.",
     "method": "PUT", "path": "/pets/{pet_id}", "has_body": True,
     "required_params": [{"arg": "pet_id", "type": "str"}],
     "optional_params": [{"arg": "status", "type": "str"}, {"arg": "name", "type": "str"}],
     "query_args": [],
     "body_args": [{"name": "status", "arg": "status"}, {"name": "name", "arg": "name"}]},
    {"name": "delete_pet", "description": "Delete a pet by id.",
     "method": "DELETE", "path": "/pets/{pet_id}", "has_body": False,
     "required_params": [{"arg": "pet_id", "type": "str"}],
     "optional_params": [], "query_args": [], "body_args": []},
    {"name": "list_owners", "description": "List all owners.",
     "method": "GET", "path": "/owners", "has_body": False,
     "required_params": [], "optional_params": [], "query_args": [], "body_args": []},
    {"name": "get_owner", "description": "Fetch a single owner by id.",
     "method": "GET", "path": "/owners/{owner_id}", "has_body": False,
     "required_params": [{"arg": "owner_id", "type": "str"}],
     "optional_params": [], "query_args": [], "body_args": []},
    {"name": "create_owner", "description": "Register a new owner. Returns the owner with its id.",
     "method": "POST", "path": "/owners", "has_body": True,
     "required_params": [{"arg": "name", "type": "str"}, {"arg": "email", "type": "str"}],
     "optional_params": [{"arg": "phone", "type": "str"}],
     "query_args": [],
     "body_args": [{"name": "name", "arg": "name"}, {"name": "email", "arg": "email"},
                   {"name": "phone", "arg": "phone"}]},
    {"name": "update_owner", "description": "Update an owner's details, e.g. set a phone number.",
     "method": "PUT", "path": "/owners/{owner_id}", "has_body": True,
     "required_params": [{"arg": "owner_id", "type": "str"}],
     "optional_params": [{"arg": "name", "type": "str"}, {"arg": "email", "type": "str"},
                         {"arg": "phone", "type": "str"}],
     "query_args": [],
     "body_args": [{"name": "name", "arg": "name"}, {"name": "email", "arg": "email"},
                   {"name": "phone", "arg": "phone"}]},
]
