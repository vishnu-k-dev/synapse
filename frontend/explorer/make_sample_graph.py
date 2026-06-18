#!/usr/bin/env python3
"""Generate a sample Capability Graph for the explorer demo (no backend/key needed).

Emits a graph in the exact shape of `GET /api/v1/graph/{app_id}` (nodes + edges + stats)
so the static explorer renders the same data the live API would serve. The data is a real,
honest Petstore capability graph: 19 operations compressing into 5 entity-scoped tools, plus
two discovered workflows. Writes both `sample-graph.json` (canonical) and `sample-graph.js`
(a `window.SAMPLE_GRAPH = ...` global, so the page works even when opened via file://).

Run:  python make_sample_graph.py
"""
from __future__ import annotations

import json
from pathlib import Path

ENTITIES = {
    "Pet": "pets", "Owner": "owners", "Vet": "vets",
    "Appointment": "appointments", "MedicalRecord": "medical_records",
}

OWNS = [("Owner", "Pet"), ("Owner", "Appointment"), ("Pet", "MedicalRecord")]

# (operation_id, method, path, entity, tool)
OPERATIONS = [
    ("listPets",            "GET",    "/pets",                       "Pet", "manage_pets"),
    ("createPet",           "POST",   "/pets",                       "Pet", "manage_pets"),
    ("getPetById",          "GET",    "/pets/{petId}",               "Pet", "manage_pets"),
    ("updatePet",           "PUT",    "/pets/{petId}",               "Pet", "manage_pets"),
    ("deletePet",           "DELETE", "/pets/{petId}",               "Pet", "manage_pets"),
    ("listOwnerPets",       "GET",    "/owners/{ownerId}/pets",      "Pet", "manage_pets"),
    ("listOwners",          "GET",    "/owners",                     "Owner", "manage_owners"),
    ("createOwner",         "POST",   "/owners",                     "Owner", "manage_owners"),
    ("getOwner",            "GET",    "/owners/{ownerId}",           "Owner", "manage_owners"),
    ("updateOwner",         "PUT",    "/owners/{ownerId}",           "Owner", "manage_owners"),
    ("deleteOwner",         "DELETE", "/owners/{ownerId}",           "Owner", "manage_owners"),
    ("listAppointments",    "GET",    "/appointments",               "Appointment", "manage_appointments"),
    ("createAppointment",   "POST",   "/appointments",               "Appointment", "manage_appointments"),
    ("getAppointment",      "GET",    "/appointments/{id}",          "Appointment", "manage_appointments"),
    ("cancelAppointment",   "DELETE", "/appointments/{id}",          "Appointment", "manage_appointments"),
    ("listVets",            "GET",    "/vets",                       "Vet", "manage_vets"),
    ("getVet",              "GET",    "/vets/{vetId}",               "Vet", "manage_vets"),
    ("createMedicalRecord", "POST",   "/medical-records",            "MedicalRecord", "manage_medical_records"),
    ("getMedicalRecord",    "GET",    "/medical-records/{recordId}", "MedicalRecord", "manage_medical_records"),
]

TOOL_ENTITY = {
    "manage_pets": "Pet", "manage_owners": "Owner", "manage_appointments": "Appointment",
    "manage_vets": "Vet", "manage_medical_records": "MedicalRecord",
}
TOOL_DESC = {
    "manage_pets": "List, create, fetch, update, delete pets (incl. an owner's pets).",
    "manage_owners": "List, create, fetch, update, delete pet owners.",
    "manage_appointments": "List, book, fetch, and cancel vet appointments.",
    "manage_vets": "List and fetch veterinarians.",
    "manage_medical_records": "Create and fetch medical records.",
}

# Discovered workflows (ordered operation_ids).
WORKFLOWS = {
    "register_and_add_pet": ("Register an owner, then add a pet that belongs to them.",
                             ["createOwner", "createPet"]),
    "book_vet_visit": ("Book an appointment, then file the medical record for the visit.",
                       ["createAppointment", "createMedicalRecord"]),
}


def build() -> dict:
    nodes, edges = [], []

    for ent, plural in ENTITIES.items():
        nodes.append({"id": f"e_{ent}", "label": ent, "node_type": "Entity",
                      "properties": {"plural": plural}})

    tools = {}
    for op_id, method, path, entity, tool in OPERATIONS:
        nodes.append({"id": f"op_{op_id}", "label": op_id, "node_type": "Operation",
                      "properties": {"method": method, "path": path, "entity": entity}})
        edges.append({"id": f"oo_{op_id}", "source": f"op_{op_id}", "target": f"e_{entity}",
                      "edge_type": "OPERATES_ON", "properties": {}})
        edges.append({"id": f"ci_{op_id}", "source": f"op_{op_id}", "target": f"t_{tool}",
                      "edge_type": "COMPRESSED_INTO", "properties": {}})
        tools.setdefault(tool, []).append(op_id)

    for tool, members in tools.items():
        nodes.append({"id": f"t_{tool}", "label": tool, "node_type": "Tool",
                      "properties": {"entity": TOOL_ENTITY[tool], "description": TOOL_DESC[tool],
                                     "member_count": len(members),
                                     "members": members,
                                     "compression_ratio": round(len(members), 2)}})
        edges.append({"id": f"te_{tool}", "source": f"t_{tool}", "target": f"e_{TOOL_ENTITY[tool]}",
                      "edge_type": "EXPOSES", "properties": {}})

    for owner, owned in OWNS:
        edges.append({"id": f"owns_{owner}_{owned}", "source": f"e_{owner}", "target": f"e_{owned}",
                      "edge_type": "OWNS", "properties": {}})

    for wf, (desc, steps) in WORKFLOWS.items():
        nodes.append({"id": f"wf_{wf}", "label": wf, "node_type": "Workflow",
                      "properties": {"description": desc, "steps": steps}})
        for i, op_id in enumerate(steps):
            edges.append({"id": f"part_{wf}_{op_id}", "source": f"op_{op_id}", "target": f"wf_{wf}",
                          "edge_type": "PART_OF", "properties": {"step_index": i}})
        for i in range(len(steps) - 1):
            edges.append({"id": f"prec_{wf}_{i}", "source": f"op_{steps[i]}", "target": f"op_{steps[i+1]}",
                          "edge_type": "PRECEDES", "properties": {"signal": "workflow"}})

    return {
        "app_id": "petstore-demo",
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "entity_count": len(ENTITIES),
            "operation_count": len(OPERATIONS),
            "tool_count": len(tools),
            "workflow_count": len(WORKFLOWS),
        },
    }


def main() -> None:
    graph = build()
    out = Path(__file__).parent
    (out / "sample-graph.json").write_text(json.dumps(graph, indent=2), encoding="utf-8")
    (out / "sample-graph.js").write_text(
        "window.SAMPLE_GRAPH = " + json.dumps(graph, indent=2) + ";\n", encoding="utf-8")
    s = graph["stats"]
    print(f"wrote sample-graph.json/.js  —  {s['operation_count']} operations -> "
          f"{s['tool_count']} tools, {s['workflow_count']} workflows, {s['entity_count']} entities")


if __name__ == "__main__":
    main()
