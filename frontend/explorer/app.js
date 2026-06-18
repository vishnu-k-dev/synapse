/* SYNAPSE Capability Graph Explorer
 *
 * Renders the capability graph (same shape as GET /api/v1/graph/{app_id}) and animates the
 * core idea: raw API operations collapsing into a small set of semantic Tools, with discovered
 * Workflows orchestrating them. Loads window.SAMPLE_GRAPH so it runs with no backend/key.
 */
(function () {
  const G = window.SAMPLE_GRAPH;
  const COLORS = { Entity: "#4C78A8", Operation: "#8c9099", Tool: "#54A24B", Workflow: "#B279A2" };

  // op node id -> tool node id (from COMPRESSED_INTO edges)
  const opToTool = {};
  G.edges.filter(e => e.edge_type === "COMPRESSED_INTO").forEach(e => { opToTool[e.source] = e.target; });

  // ── deterministic positions: operations in a column (left), entities (center), tools (right) ──
  const entities = G.nodes.filter(n => n.node_type === "Entity");
  const allOps = G.nodes.filter(n => n.node_type === "Operation");
  const entY = {};
  const pos = {};
  entities.forEach((e, i) => {
    entY[e.id] = (i - (entities.length - 1) / 2) * 150;
    pos[e.id] = { x: 0, y: entY[e.id] };
  });
  allOps.forEach((op, i) => { pos[op.id] = { x: -540, y: (i - (allOps.length - 1) / 2) * 42 }; });
  G.nodes.filter(n => n.node_type === "Tool").forEach(t => {
    pos[t.id] = { x: 400, y: entY["e_" + t.properties.entity] };
  });
  G.nodes.filter(n => n.node_type === "Workflow").forEach((wf, i) => {
    pos[wf.id] = { x: 760, y: (i - (G.stats.workflow_count - 1) / 2) * 200 };
  });

  // ── build cytoscape elements ──
  const elements = [];
  G.nodes.forEach(n => elements.push({
    data: { id: n.id, label: n.label, type: n.node_type, props: n.properties },
    position: pos[n.id] || { x: 0, y: 0 },
  }));
  G.edges.forEach(e => elements.push({
    data: { id: e.id, source: e.source, target: e.target, type: e.edge_type },
  }));
  // synthesize Workflow -> Tool edges (a workflow orchestrates the tools holding its steps)
  G.nodes.filter(n => n.node_type === "Workflow").forEach(wf => {
    const tools = [...new Set((wf.props ? wf.props.steps : wf.properties.steps || [])
      .map(op => opToTool["op_" + op]))].filter(Boolean);
    (wf.properties.steps || []).forEach(() => {});
    tools.forEach(t => elements.push({
      data: { id: "wfuses_" + wf.id + "_" + t, source: wf.id, target: t, type: "WF_USES" },
    }));
  });

  const cy = cytoscape({
    container: document.getElementById("cy"),
    elements,
    layout: { name: "preset" },
    minZoom: 0.2, maxZoom: 2.5,
    style: [
      { selector: "node", style: {
          "background-color": ele => COLORS[ele.data("type")] || "#888",
          "label": "data(label)", "color": "#e6e9ef", "font-size": 11,
          "text-valign": "center", "text-halign": "center", "text-wrap": "wrap",
          "text-max-width": 90, "text-outline-color": "#14171f", "text-outline-width": 2,
          "width": 26, "height": 26 } },
      { selector: 'node[type="Entity"]', style: { "width": 60, "height": 60, "font-size": 13, "font-weight": "bold" } },
      { selector: 'node[type="Tool"]', style: { "width": 70, "height": 70, "font-size": 12, "font-weight": "bold", "shape": "round-rectangle" } },
      { selector: 'node[type="Workflow"]', style: { "width": 64, "height": 64, "shape": "diamond" } },
      { selector: "edge", style: {
          "width": 1.5, "line-color": "#3a4150", "target-arrow-color": "#3a4150",
          "target-arrow-shape": "triangle", "curve-style": "bezier", "arrow-scale": 0.8 } },
      { selector: 'edge[type="OWNS"]', style: { "line-color": "#4C78A8", "target-arrow-color": "#4C78A8", "line-style": "dashed" } },
      { selector: 'edge[type="WF_USES"]', style: { "line-color": "#B279A2", "target-arrow-color": "#B279A2" } },
      { selector: 'edge[type="EXPOSES"]', style: { "line-color": "#54A24B", "target-arrow-color": "#54A24B" } },
      { selector: ".dim", style: { "opacity": 0.12 } },
      { selector: ".hl", style: { "border-width": 3, "border-color": "#ffd166" } },
    ],
  });

  // ── visibility helpers ──
  const byType = t => cy.elements().filter(e => e.data("type") === t);
  const edgesOfTypes = types => cy.edges().filter(e => types.includes(e.data("type")));

  function show(coll) { coll.style("display", "element"); }
  function hide(coll) { coll.style("display", "none"); }
  function fadeIn(coll) {
    coll.style("display", "element").style("opacity", 0);
    coll.animate({ style: { opacity: 1 } }, { duration: 450 });
  }
  function fitVisible(dur) {
    const vis = cy.elements().filter(e => e.visible());
    cy.animate({ fit: { eles: vis, padding: 55 } }, { duration: dur || 0 });
  }

  let mode = null;

  function setRaw(animate) {
    mode = "raw";
    document.getElementById("btn-raw").classList.add("active");
    document.getElementById("btn-compress").classList.remove("active");
    // show entities + operations + OPERATES_ON + OWNS
    show(byType("Entity")); show(byType("Operation"));
    byType("Operation").forEach(op => { op.style("opacity", 1); op.position(pos[op.id()]); });
    show(edgesOfTypes(["OPERATES_ON", "OWNS"]));
    hide(byType("Tool")); hide(byType("Workflow"));
    hide(edgesOfTypes(["COMPRESSED_INTO", "EXPOSES", "PRECEDES", "PART_OF", "WF_USES"]));
    updateStats("raw");
    fitVisible(animate ? 400 : 0);
  }

  function setSynapse() {
    mode = "synapse";
    document.getElementById("btn-compress").classList.add("active");
    document.getElementById("btn-raw").classList.remove("active");
    hide(edgesOfTypes(["OPERATES_ON"]));
    // collapse each operation into its tool, then hide it
    byType("Operation").forEach(op => {
      const toolId = opToTool[op.id()];
      const target = toolId ? cy.getElementById(toolId).position() : op.position();
      op.animate({ position: target, style: { opacity: 0 } },
        { duration: 700, easing: "ease-in-out-cubic", complete: () => op.style("display", "none") });
    });
    // reveal tools, workflows, and their edges
    setTimeout(() => {
      fadeIn(byType("Tool")); fadeIn(byType("Workflow"));
      show(edgesOfTypes(["EXPOSES", "OWNS", "WF_USES"]));
      fitVisible(500);
    }, 450);
    updateStats("synapse");
  }

  function updateStats(m) {
    const s = G.stats;
    const ratio = (s.operation_count / s.tool_count).toFixed(1);
    const el = document.getElementById("stats");
    el.innerHTML = m === "raw"
      ? `Raw API surface: <b>${s.operation_count} operations</b> across ${s.entity_count} entities`
      : `SYNAPSE: <b>${s.tool_count} tools</b> + <b>${s.workflow_count} workflows</b> ` +
        `&nbsp;·&nbsp; <b>${ratio}× compression</b> (${s.operation_count} → ${s.tool_count})`;
  }

  // ── inspector panel ──
  const panel = document.getElementById("panel");
  function inspect(d) {
    const p = d.props || {};
    const tag = `<span class="type-tag" style="background:${COLORS[d.type]}33;color:${COLORS[d.type]}">${d.type}</span>`;
    let body = "";
    if (d.type === "Operation") {
      body = `<div class="kv"><b>${p.method}</b> <code>${p.path}</code></div>
              <div class="kv">entity: <b>${p.entity}</b></div>`;
    } else if (d.type === "Tool") {
      const members = (p.members || []).map(m => `<li>${m}</li>`).join("");
      body = `<div class="kv">${p.description || ""}</div>
              <div class="kv">entity: <b>${p.entity}</b></div>
              <div class="kv">merged <b>${p.member_count}</b> endpoints (${p.compression_ratio}× ):</div>
              <ul>${members}</ul>`;
    } else if (d.type === "Workflow") {
      const steps = (p.steps || []).map(s => `<li>${s}</li>`).join(" → ".length ? "" : "");
      body = `<div class="kv">${p.description || ""}</div>
              <div class="kv">steps:</div><ul>${(p.steps || []).map(s => `<li>${s}</li>`).join("")}</ul>`;
    } else if (d.type === "Entity") {
      body = `<div class="kv">plural: <b>${p.plural}</b></div>`;
    }
    panel.innerHTML = `${tag}<h3>${d.label}</h3>${body}`;
  }

  cy.on("tap", "node", e => {
    cy.elements().removeClass("hl");
    e.target.addClass("hl");
    inspect(e.target.data());
  });
  cy.on("tap", e => { if (e.target === cy) { cy.elements().removeClass("hl"); } });

  document.getElementById("btn-raw").addEventListener("click", () => setRaw(true));
  document.getElementById("btn-compress").addEventListener("click", () => {
    if (mode !== "synapse") setSynapse();
  });

  // start in raw view
  setRaw(false);
  window.__cy = cy;  // scripting/debug hook for the explorer
})();
