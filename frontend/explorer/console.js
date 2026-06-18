/* SYNAPSE Live Console v2 — replays REAL recorded agent sessions, naive vs SYNAPSE.
 *
 * Data: window.TRANSCRIPTS (from scripts/record_transcripts.py) — genuine MCP round-trips
 * against the real generated servers + sandbox (incl. real 404 fumbles on the naive surface).
 * Both lanes replay in parallel; the SYNAPSE lane drives the MCP-fabric graph (pulses +
 * particles Agent -> Tool -> API). A verdict bar quantifies the difference.
 */
(function () {
  const T = window.TRANSCRIPTS;
  const TOOLS = ["manage_owners", "manage_pets", "manage_appointments", "manage_medical_records", "manage_vets"];
  const C = { agent: "#6ea8fe", tool: "#5bd17b", api: "#b18cf2", particle: "#ffd166", ring: "#ffd166" };
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const $ = s => document.querySelector(s);

  // ── MCP fabric graph: Agent ──(mcp)── Tools ──(http)── API ──
  const nodes = [
    { data: { id: "agent", label: "🤖 Agent", kind: "agent" }, position: { x: -330, y: 0 } },
    { data: { id: "api", label: "🔌 Sandbox API", kind: "api" }, position: { x: 330, y: 0 } },
  ];
  const edges = [];
  TOOLS.forEach((t, i) => {
    nodes.push({ data: { id: t, label: t, kind: "tool" }, position: { x: 0, y: (i - 2) * 78 } });
    edges.push({ data: { id: "a_" + t, source: "agent", target: t, kind: "mcp" } });
    edges.push({ data: { id: "h_" + t, source: t, target: "api", kind: "http" } });
  });

  const cy = cytoscape({
    container: $("#cy"), elements: [...nodes, ...edges], layout: { name: "preset" },
    userZoomingEnabled: false, userPanningEnabled: false, autoungrabify: true, autounselectify: true,
    style: [
      { selector: "node", style: { label: "data(label)", color: "#eaeef7", "font-size": 11, "font-weight": 600,
          "text-valign": "center", "text-halign": "center", "text-outline-color": "#090c14", "text-outline-width": 3,
          width: 50, height: 50, "background-color": e => C[e.data("kind")] || "#888" } },
      { selector: 'node[kind="agent"]', style: { width: 76, height: 60, shape: "round-rectangle" } },
      { selector: 'node[kind="api"]', style: { width: 96, height: 70, shape: "round-rectangle",
          "border-width": 2, "border-color": "rgba(177,140,242,.55)" } },
      { selector: 'node[kind="tool"]', style: { shape: "round-rectangle", width: 78, height: 46,
          "font-family": "ui-monospace, Menlo, monospace", "font-size": 10,
          "border-width": 1, "border-color": "rgba(91,209,123,.35)" } },
      { selector: 'node[kind="particle"]', style: { width: 13, height: 13, "background-color": C.particle,
          "border-width": 0, label: "", "z-index": 99 } },
      { selector: 'node[kind="ring"]', style: { "background-opacity": 0, "border-width": 2,
          "border-color": C.ring, "border-opacity": 0.9, label: "", "z-index": 50 } },
      { selector: "edge", style: { width: 1.3, "line-color": "rgba(255,255,255,.10)", "curve-style": "bezier",
          "target-arrow-shape": "none" } },
      { selector: 'edge[kind="mcp"]', style: { "line-style": "dashed" } },
      { selector: ".firing", style: { "line-color": C.particle, width: 3, "line-style": "solid" } },
    ],
  });
  const fit = () => cy.fit(undefined, 48);
  fit(); window.addEventListener("resize", fit);

  // ── graph fx (tuned) ──
  function pulse(id) {
    const n = cy.getElementById(id); if (n.empty()) return;
    n.animate({ style: { "background-color": C.particle, width: 92, height: 56, "border-color": C.particle } }, { duration: 200,
      complete: () => n.animate({ style: { "background-color": C.tool, width: 78, height: 46, "border-color": "rgba(91,209,123,.35)" } }, { duration: 460 }) });
    ring(id);
  }
  function ring(id) {
    const p = cy.getElementById(id).position();
    const r = cy.add({ group: "nodes", data: { id: "r" + Math.random().toString(36).slice(2), kind: "ring" }, position: { ...p } });
    r.style({ width: 40, height: 40 });
    r.animate({ style: { width: 120, height: 120, "border-opacity": 0 } }, { duration: 620, complete: () => r.remove() });
  }
  function particle(fromId, toId) {
    return new Promise(res => {
      const from = cy.getElementById(fromId).position();
      const id = "p" + Math.random().toString(36).slice(2);
      cy.add({ group: "nodes", data: { id, kind: "particle" }, position: { ...from } });
      cy.getElementById(id).animate({ position: cy.getElementById(toId).position() },
        { duration: 360, easing: "ease-in-out-cubic", complete() { cy.getElementById(id).remove(); res(); } });
    });
  }
  function fire(id) { const e = cy.getElementById(id); if (e.empty()) return; e.addClass("firing"); setTimeout(() => e.removeClass("firing"), 620); }

  async function graphCall(tool) {
    fire("a_" + tool); await particle("agent", tool);
    pulse(tool); fire("h_" + tool); await particle(tool, "api");
    await sleep(140); await particle("api", tool);
  }

  // ── DOM helpers ──
  const pretty = o => JSON.stringify(o, null, 2);
  function bubble(laneBody, cls, text) {
    const d = document.createElement("div"); d.className = "bubble " + cls;
    laneBody.appendChild(d); laneBody.scrollTop = laneBody.scrollHeight;
    return type(d, text);
  }
  async function type(el, text, speed = 7) {
    for (let i = 1; i <= text.length; i++) { el.textContent = text.slice(0, i); if (i % 4 === 0) el.parentElement.scrollTop = el.parentElement.scrollHeight; await sleep(speed); }
  }
  function toolCard(laneBody, step) {
    const c = document.createElement("div"); c.className = "tcard";
    c.innerHTML = `<div class="h"><span class="mc">▶ tools/call</span><span class="nm">${step.tool}</span>
        <span class="st"><span class="spinner"></span> calling…</span></div>
      <pre>${pretty(step.args)}</pre>`;
    laneBody.appendChild(c); laneBody.scrollTop = laneBody.scrollHeight;
    return c;
  }
  function resolveCard(c, step) {
    const ok = step.ok;
    c.classList.add(ok ? "ok" : "err");
    c.querySelector(".st").innerHTML = ok ? `● 200 OK · ${step.ms}ms` : `✗ error · ${step.ms}ms`;
    const r = document.createElement("pre"); r.className = "resp";
    r.textContent = ok ? pretty(step.response) : (step.response.error || "request failed");
    c.appendChild(r); c.parentElement.scrollTop = c.parentElement.scrollHeight;
  }

  // ── replay one lane ──
  async function replayLane(laneSel, variant, driveGraph) {
    const lane = $(laneSel), body = lane.querySelector(".lane-body");
    lane.querySelector(".count-badge").textContent = `${variant.tool_count} tools`;
    lane.querySelector(".lane-status").textContent = "running…";
    body.innerHTML = "";
    await bubble(body, "plan", variant.plan);
    await sleep(200);
    for (const step of variant.steps) {
      const card = toolCard(body, step);
      if (driveGraph && TOOLS.includes(step.tool)) await graphCall(step.tool);
      else await sleep(420);
      await sleep(160);
      resolveCard(card, step);
      await sleep(step.ok ? 360 : 220);
    }
    await bubble(body, "final", variant.final);
    lane.querySelector(".lane-status").innerHTML =
      `${variant.calls} calls · ${variant.errors} err · ${variant.success ? "✓ done" : "✗ failed"}`;
  }

  // ── verdict ──
  function showVerdict(sc) {
    const n = sc.variants.naive, s = sc.variants.synapse;
    const callX = (n.calls / s.calls).toFixed(1);
    const toolX = (n.tool_count / s.tool_count).toFixed(1);
    const v = $("#verdict");
    v.innerHTML =
      `<span class="v"><span class="tag naive">Naive</span><span class="nums">${n.tool_count} tools · ${n.calls} calls · ${n.errors} error${n.errors !== 1 ? "s" : ""}</span></span>
       <span class="win">SYNAPSE: ${toolX}× smaller surface · ${callX}× fewer calls · no fumbling</span>
       <span class="v"><span class="tag synapse">SYNAPSE</span><span class="nums">${s.tool_count} tools · ${s.calls} calls · ${s.errors} errors</span></span>`;
    v.className = "show";
  }

  // ── run a scenario (both lanes in parallel) ──
  let running = false;
  async function run(sc) {
    if (running) return; running = true;
    document.querySelectorAll(".chip").forEach(c => (c.disabled = true));
    $("#verdict").className = "";
    const pr = $("#prompt"); pr.className = "show"; pr.querySelector(".ptext").textContent = "";
    await type(pr.querySelector(".ptext"), sc.user, 6);
    await sleep(250);
    await Promise.all([
      replayLane(".lane.naive", sc.variants.naive, false),
      replayLane(".lane.synapse", sc.variants.synapse, true),
    ]);
    showVerdict(sc);
    running = false;
    document.querySelectorAll(".chip").forEach(c => (c.disabled = false));
  }

  // ── chips ──
  if (!T || !T.scenarios) {
    $("#chiplist").innerHTML = '<span style="color:#8b97ad">No transcripts.json — run scripts/record_transcripts.py</span>';
    return;
  }
  T.scenarios.forEach((sc, i) => {
    const b = document.createElement("button"); b.className = "chip";
    b.innerHTML = sc.title + (sc.badge ? `<span class="wf">${sc.badge}</span>` : "");
    b.addEventListener("click", () => run(sc));
    $("#chiplist").appendChild(b);
  });
  window.__run = i => run(T.scenarios[i]);  // scripting hook
})();
