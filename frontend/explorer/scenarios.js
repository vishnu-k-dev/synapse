/* Scripted agent sessions for the Live Console.
 *
 * Each scenario is a deterministic replay of an agent using SYNAPSE's compressed MCP tools.
 * The args/responses mirror the real sandbox semantics (create returns an id, that id chains
 * into the next call), so the demo is honest — it's a recorded-style session, not magic.
 * `naiveCalls` is the (typical) number of fumbled calls the same task takes with a raw
 * 1-tool-per-endpoint surface — used for the efficiency badge.
 */
window.SCENARIOS = [
  {
    id: "register_pet",
    title: "Register owner + add their dog",
    user: "Register a new owner, Carol (carol@example.com), and add a dog named Max that belongs to her.",
    plan: "I'll create the owner first, then create the pet using the id she's assigned.",
    naiveCalls: 5,
    steps: [
      { tool: "manage_owners",
        args: { action: "create", name: "Carol", email: "carol@example.com" },
        response: { id: "owner_84", name: "Carol", email: "carol@example.com" } },
      { tool: "manage_pets",
        args: { action: "create", name: "Max", species: "dog", owner_id: "owner_84" },
        response: { id: "pet_57", name: "Max", species: "dog", owner_id: "owner_84", status: "available" } },
    ],
    final: "Done ✓ Registered Carol (owner_84) and added her dog Max (pet_57), linked to her account.",
  },
  {
    id: "sell_pet",
    title: "Add a pet, then mark it sold",
    user: "Add a cat named Whiskers to the store, then mark it as sold.",
    plan: "Create the cat, then update its status to sold using the returned id.",
    naiveCalls: 4,
    steps: [
      { tool: "manage_pets",
        args: { action: "create", name: "Whiskers", species: "cat" },
        response: { id: "pet_58", name: "Whiskers", species: "cat", status: "available" } },
      { tool: "manage_pets",
        args: { action: "update", id: "pet_58", status: "sold" },
        response: { id: "pet_58", name: "Whiskers", status: "sold" } },
    ],
    final: "Done ✓ Whiskers (pet_58) is now marked sold.",
  },
  {
    id: "vet_visit",
    title: "Book a vet visit + file the record",
    badge: "workflow",
    user: "Book a checkup for pet pet_57 with vet vet_3 tomorrow, then file the medical record.",
    plan: "This matches the discovered book_vet_visit workflow: create the appointment, then file a medical record that references it.",
    naiveCalls: 6,
    steps: [
      { tool: "manage_appointments",
        args: { action: "create", pet_id: "pet_57", vet_id: "vet_3", date: "2026-06-20" },
        response: { id: "appt_12", pet_id: "pet_57", vet_id: "vet_3", status: "booked" } },
      { tool: "manage_medical_records",
        args: { action: "create", pet_id: "pet_57", vet_id: "vet_3", appointment_id: "appt_12",
                diagnosis: "Routine checkup — healthy" },
        response: { id: "rec_9", pet_id: "pet_57", appointment_id: "appt_12" } },
    ],
    final: "Done ✓ Booked appointment appt_12 and filed medical record rec_9 for the visit.",
  },
];
