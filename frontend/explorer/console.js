/* SYNAPSE Live Console — visualize an agent using the MCP tools.
 *
 * Drives a deterministic agent session (from scenarios.js): streams the chat + tool-call cards,
 * pulses the tool nodes, flies particles Agent -> Tool -> API, and logs the raw MCP JSON-RPC
 * frames on the wire panel. No backend / key — a recorded-style replay matching sandbox semantics.
 */
(function () {
  const TOOLS = ["manage_owners", "manage_pets", "manage_appointments", "manage_medical_records", "manage_vets"];
  const C = { agent: "#6ea8fe", tool: "#54A24B", api: "#b18cf2", particle: "#ffd166" };

  // ── build the MCP "fabric": Agent ──(mcp)── Tools ──(http)── API hub ──
  const nodes = [
    { data: { id: "agent", label: "🤖 Agent", kind: "agent" }, position: { x: -340, y: 0 } },
    { data: { id: "api", label: "🔌 API", kind: "api" }, position: { x: 340, y: 0 } },
  ];
  const edges = [];
  TOOLS.forEach((t, i) => {
    const y = (i - (TOOLS.length - 1) / 2) * 95;
    nodes.push({ data: { id: t, label: t, kind: "tool" }, position: { x: 0, y } });
    edges.push({ data: { id: "a_" + t, source: "agent", target: t, kind: "mcp" } });
    edges.push({ data: { id: "h_" + t, source: t, target: "api", kind: "http" } });
  });

  const cy = cytoscape({
    container: document.getElementById("cy"),
    elements: [...nodes, ...edges],
    layout: { name: "preset" },
    userZoomingEnabled: false, userPanningEnabled: false, autoungrabify: true,
    style: [
      { selector: "node", style: {
          "label": "data(label)", "color": "#e8ecf4", "font-size": 12, "font-weight": 600,
          "text-valign": "center", "text-halign": "center", "text-outline-color": "#0b0e16",
          "text-outline-width": 3, "width": 54, "height": 54,
          "background-color": e => C[e.data("kind")] || "#888" } },
      { selector: 'node[kind="agent"]', style: { "width": 78, "height": 78, "shape": "round-rectangle" } },
      { selector: 'node[kind="api"]', style: { "width": 90, "height": 90, "shape": "round-rectangle",
          "background-color": C.api, "border-width": 2, "border-color": "rgba(177,140,242,.5)" } },
      { selector: 'node[kind="tool"]', style: { "shape": "round-rectangle", "width": 72, "height": 50, "font-family": "ui-monospace, Menlo, monospace", "font-size": 10 } },
      { selector: 'node[kind="particle"]', style: { "width": 14, "height": 14, "background-color": C.particle,
          "border-width": 0, "label": "", "z-index": 99 } },
      { selector: "edge", style: { "width": 1.4, "line-color": "rgba(255,255,255,.12)",
          "curve-style": "bezier", "target-arrow-shape": "none" } },
      { selector: 'edge[kind="mcp"]', style: { "line-style": "dashed" } },
      { selector: ".firing", style: { "line-color": C.gold || "#ffd166", "width": 3 } },
    ],
  });
  cy.fit(undefined, 55);
  window.addEventListener("resize", () => cy.fit(undefined, 55));

  // ── helpers ──
  const $ = sel => document.querySelector(sel);
  const chat = $("#chat"), wire = $("#wirelog"), metrics = $("#metrics");
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  function scrollDown(el) { el.scrollTop = el.scrollHeight; }

  function addMsg(role, who, text, cls) {
    const d = document.createElement("div");
    d.className = `msg ${role}${cls ? " " + cls : ""}`;
    d.innerHTML = `<div class="who">${who}</div><span class="body"></span>`;
    chat.appendChild(d); scrollDown(chat);
    return d.querySelector(".body");
  }
  async function type(el, text, speed = 12) {
    for (let i = 1; i <= text.length; i++) { el.textContent = text.slice(0, i); if (i % 3 === 0) scrollDown(chat); await sleep(speed); }
  }

  function toolCard(tool, args) {
    const c = document.createElement("div");
    c.className = "toolcard";
    c.innerHTML =
      `<div class="tc-head"><span>⇢</span><span class="tc-name">${tool}</span>
         <span class="tc-status"><span class="spinner"></span> calling…</span></div>
       <pre class="tc-args">${pretty(args)}</pre>`;
    chat.appendChild(c); scrollDown(chat);
    return c;
  }
  function cardResolve(card, response, ms) {
    card.classList.add("ok");
    card.querySelector(".tc-status").innerHTML = `<span class="dotok">●</span> 200 OK · ${ms}ms`;
    const r = document.createElement("pre");
    r.className = "tc-resp"; r.textContent = pretty(response);
    card.appendChild(r); scrollDown(chat);
  }

  function pretty(o) { return JSON.stringify(o, null, 2); }

  function wireFrame(kind, obj) {
    const f = document.createElement("div");
    f.className = "frame " + kind;
    const dir = kind === "req" ? "▶ tools/call" : "◀ result";
    f.innerHTML = `<span class="dir">${dir}</span> ` + colorJSON(obj);
    wire.appendChild(f); scrollDown(wire);
  }
  function colorJSON(o) {
    return JSON.stringify(o)
      .replace(/"([^"]+)":/g, '<span class="k">"$1"</span>:')
      .replace(/:"([^"]*)"/g, ':<span class="s">"$1"</span>');
  }

  // graph fx
  function pulse(id) {
    const n = cy.getElementById(id);
    n.animate({ style: { "background-color": C.particle, "width": n.width() + 14, "height": n.height() + 10 } }, { duration: 220,
      complete: () => n.animate({ style: { "background-color": C.tool, "width": n.width() - 14, "height": n.height() - 10 } }, { duration: 420 }) });
  }
  function particle(fromId, toId) {
    return new Promise(res => {
      const id = "p" + Math.random().toString(36).slice(2);
      const from = cy.getElementById(fromId).position();
      cy.add({ group: "nodes", data: { id, kind: "particle" }, position: { ...from } });
      cy.getElementById(id).animate({ position: cy.getElementById(toId).position() },
        { duration: 480, easing: "ease-in-out-cubic", complete() { cy.getElementById(id).remove(); res(); } });
    });
  }
  function fireEdge(id) {
    const e = cy.getElementById(id); e.addClass("firing");
    setTimeout(() => e.removeClass("firing"), 700);
  }

  // ── run a scenario ──
  let running = false;
  async function run(s) {
    if (running) return;
    running = true;
    setChips(true);
    chat.innerHTML = ""; wire.innerHTML = ""; metrics.className = "";
    const t0 = performance.now();

    await type(addMsg("user", "You", ""), s.user, 8);
    await sleep(250);
    await type(addMsg("assistant", "Agent · planning", ""), s.plan, 9);
    await sleep(300);

    const used = new Set();
    for (const step of s.steps) {
      used.add(step.tool);
      const card = toolCard(step.tool, step.args);
      wireFrame("req", { jsonrpc: "2.0", id: used.size, method: "tools/call",
        params: { name: step.tool, arguments: step.args } });
      fireEdge("a_" + step.tool);
      await particle("agent", step.tool);
      pulse(step.tool);
      fireEdge("h_" + step.tool);
      await particle(step.tool, "api");
      await sleep(220);
      const ms = 40 + Math.floor(Math.random() * 90);
      await particle("api", step.tool);
      cardResolve(card, step.response, ms);
      wireFrame("res", { jsonrpc: "2.0", id: used.size, result: step.response });
      await particle(step.tool, "agent");
      await sleep(300);
    }

    await type(addMsg("assistant", "Agent", "", "final"), s.final, 8);

    const elapsed = ((performance.now() - t0) / 1000).toFixed(1);
    metrics.innerHTML =
      `<span class="ok">✓ completed</span> · <b>${s.steps.length}</b> MCP calls · ` +
      `<b>${used.size}</b> tool${used.size > 1 ? "s" : ""} · ${elapsed}s ` +
      `&nbsp;·&nbsp; <span class="save">naive surface would fumble ~${s.naiveCalls} calls</span>`;
    metrics.className = "show";
    running = false;
    setChips(false);
  }

  function setChips(disabled) {
    document.querySelectorAll(".chip").forEach(c => (c.disabled = disabled));
  }

  // ── task chips ──
  const chiplist = document.getElementById("chiplist");
  window.SCENARIOS.forEach(s => {
    const b = document.createElement("button");
    b.className = "chip";
    b.innerHTML = s.title + (s.badge ? `<span class="wf">${s.badge}</span>` : "");
    b.addEventListener("click", () => run(s));
    chiplist.appendChild(b);
  });

  window.__runScenario = i => run(window.SCENARIOS[i]);  // scripting hook
})();
