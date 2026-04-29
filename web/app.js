(function () {
  let viewer = null;
  let currentDesign = null;
  let inFlight = false;
  let _updateActive = null;  // bound to the current design's room-info updater

  document.addEventListener("DOMContentLoaded", main);

  async function main() {
    const designs = await fetch("/designs").then((r) => r.json()).then((d) => d.designs || []);
    if (!designs.length) {
      document.getElementById("panorama").textContent = "No designs found in designs/.";
      return;
    }

    const params = new URLSearchParams(location.search);
    const requested = params.get("design");
    const initial = designs.includes(requested) ? requested : designs[0];

    const select = document.getElementById("design-select");
    select.innerHTML = designs
      .map((name) => `<option value="${name}">${name}</option>`)
      .join("");
    select.value = initial;
    select.addEventListener("change", (e) => {
      const next = e.target.value;
      history.replaceState(null, "", `?design=${encodeURIComponent(next)}`);
      loadDesign(next);
    });

    document.getElementById("prompt-send").addEventListener("click", submitPrompt);
    document.getElementById("prompt-input").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submitPrompt();
    });
    document.getElementById("new-chat-btn").addEventListener("click", newChat);

    await loadDesign(initial);
  }

  async function loadDesign(name) {
    currentDesign = name;
    document.getElementById("chat-log").innerHTML = "";

    const [tour, house, reqs] = await Promise.all([
      fetch(`/designs/${encodeURIComponent(name)}/tour.json`).then((r) => r.json()),
      fetch(`/designs/${encodeURIComponent(name)}/house.json`).then((r) => r.json()),
      fetch(`/designs/${encodeURIComponent(name)}/requirements.jsonl`).then((r) => r.json()),
    ]);

    document.getElementById("design-sub").textContent =
      `${house.rooms.length} room${house.rooms.length === 1 ? "" : "s"}`;

    if (viewer) {
      viewer.destroy();
      viewer = null;
    }

    const firstSceneId = tour.default && tour.default.firstScene;
    if (!firstSceneId) {
      document.getElementById("panorama").textContent = "No rooms defined for this design.";
      return;
    }

    viewer = pannellum.viewer("panorama", {
      default: { ...tour.default, sceneFadeDuration: 600 },
      scenes: tour.scenes,
    });

    const roomsById = Object.fromEntries(house.rooms.map((r) => [r.id, r]));
    const tourableRooms = house.rooms.filter((r) => r.tourable !== false);
    const list = document.getElementById("rooms");
    list.innerHTML = "";

    const byFloor = new Map();
    tourableRooms.forEach((r) => {
      if (!byFloor.has(r.floor)) byFloor.set(r.floor, []);
      byFloor.get(r.floor).push(r);
    });
    const floors = [...byFloor.keys()].sort((a, b) => a - b);
    const multiFloor = floors.length > 1;

    floors.forEach((floor) => {
      byFloor.get(floor).forEach((room) => {
        const li = document.createElement("li");
        li.dataset.sceneId = room.id;
        li.dataset.floor = String(room.floor);
        li.innerHTML = `${room.name}<small>${roomDims(room)} · ceiling ${room.ceiling_height_m.toFixed(1)} m</small>`;
        li.addEventListener("click", () => viewer.loadScene(room.id));
        list.appendChild(li);
      });
    });

    const topdown = document.getElementById("topdown");
    const tabs = document.getElementById("floor-tabs");
    tabs.innerHTML = "";

    const setTopdownToRoom = (sceneId) => {
      topdown.src = `/designs/${encodeURIComponent(name)}/massing/${sceneId}/topdown.png?v=${Date.now()}`;
    };
    const setTopdownToFloor = (floor) => {
      topdown.src = `/designs/${encodeURIComponent(name)}/massing/topdown-floor${floor}.png?v=${Date.now()}`;
    };
    const filterListToFloor = (floor) => {
      Array.from(list.children).forEach((li) => {
        li.style.display = !multiFloor || Number(li.dataset.floor) === floor ? "" : "none";
      });
    };
    const setActiveFloor = (floor) => {
      Array.from(tabs.children).forEach((b) =>
        b.classList.toggle("active", Number(b.dataset.floor) === floor),
      );
      filterListToFloor(floor);
    };
    if (multiFloor) {
      floors.forEach((f) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.textContent = floorLabel(f);
        btn.dataset.floor = String(f);
        btn.addEventListener("click", () => {
          setActiveFloor(f);
          setTopdownToFloor(f);
        });
        tabs.appendChild(btn);
      });
    }

    topdown.onerror = () => {
      topdown.onerror = null;
      topdown.src = `/designs/${encodeURIComponent(name)}/massing/topdown.png`;
    };

    _updateActive = (sceneId) => {
      const room = roomsById[sceneId];
      Array.from(list.children).forEach((li) => {
        li.classList.toggle("active", li.dataset.sceneId === sceneId);
      });
      setRoomInfo(room);
      setTopdownToRoom(sceneId);
      if (multiFloor && room) setActiveFloor(room.floor);
    };

    viewer.on("scenechange", _updateActive);
    _updateActive(firstSceneId);

    setStyle(house.style);
    setRequirementsLog(reqs.requirements || []);
  }

  /**
   * Lightweight refresh after an approve/reject — keeps the chat log and
   * panorama visible while new data loads, then swaps in the updated
   * viewer at the same scene.
   */
  async function refreshDesign(name) {
    const overlay = document.getElementById("pano-overlay");
    overlay.classList.add("visible");

    // Remember current scene so we can return to it.
    const prevScene = viewer ? viewer.getScene() : null;

    try {
      const [tour, house, reqs] = await Promise.all([
        fetch(`/designs/${encodeURIComponent(name)}/tour.json`).then((r) => r.json()),
        fetch(`/designs/${encodeURIComponent(name)}/house.json`).then((r) => r.json()),
        fetch(`/designs/${encodeURIComponent(name)}/requirements.jsonl`).then((r) => r.json()),
      ]);

      document.getElementById("design-sub").textContent =
        `${house.rooms.length} room${house.rooms.length === 1 ? "" : "s"}`;

      // Rebuild the viewer.
      if (viewer) { viewer.destroy(); viewer = null; }

      const firstSceneId = tour.default && tour.default.firstScene;
      if (!firstSceneId) {
        document.getElementById("panorama").textContent = "No rooms defined for this design.";
        return;
      }

      // Restore the scene the user was looking at (if it still exists).
      const targetScene = (prevScene && tour.scenes[prevScene]) ? prevScene : firstSceneId;
      const cfg = { default: { ...tour.default, firstScene: targetScene, sceneFadeDuration: 600 }, scenes: tour.scenes };
      viewer = pannellum.viewer("panorama", cfg);

      // Rebuild room list + topdown (same as loadDesign).
      const roomsById = Object.fromEntries(house.rooms.map((r) => [r.id, r]));
      const tourableRooms = house.rooms.filter((r) => r.tourable !== false);
      const list = document.getElementById("rooms");
      list.innerHTML = "";

      const byFloor = new Map();
      tourableRooms.forEach((r) => {
        if (!byFloor.has(r.floor)) byFloor.set(r.floor, []);
        byFloor.get(r.floor).push(r);
      });
      const floors = [...byFloor.keys()].sort((a, b) => a - b);
      const multiFloor = floors.length > 1;

      floors.forEach((floor) => {
        byFloor.get(floor).forEach((room) => {
          const li = document.createElement("li");
          li.dataset.sceneId = room.id;
          li.dataset.floor = String(room.floor);
          li.innerHTML = `${room.name}<small>${roomDims(room)} · ceiling ${room.ceiling_height_m.toFixed(1)} m</small>`;
          li.addEventListener("click", () => viewer.loadScene(room.id));
          list.appendChild(li);
        });
      });

      const topdown = document.getElementById("topdown");
      const tabs = document.getElementById("floor-tabs");
      tabs.innerHTML = "";

      const setTopdownToRoom = (sceneId) => {
        topdown.src = `/designs/${encodeURIComponent(name)}/massing/${sceneId}/topdown.png?v=${Date.now()}`;
      };
      const setTopdownToFloor = (floor) => {
        topdown.src = `/designs/${encodeURIComponent(name)}/massing/topdown-floor${floor}.png?v=${Date.now()}`;
      };
      const filterListToFloor = (floor) => {
        Array.from(list.children).forEach((li) => {
          li.style.display = !multiFloor || Number(li.dataset.floor) === floor ? "" : "none";
        });
      };
      const setActiveFloor = (floor) => {
        Array.from(tabs.children).forEach((b) =>
          b.classList.toggle("active", Number(b.dataset.floor) === floor),
        );
        filterListToFloor(floor);
      };
      if (multiFloor) {
        floors.forEach((f) => {
          const btn = document.createElement("button");
          btn.type = "button";
          btn.textContent = floorLabel(f);
          btn.dataset.floor = String(f);
          btn.addEventListener("click", () => { setActiveFloor(f); setTopdownToFloor(f); });
          tabs.appendChild(btn);
        });
      }

      topdown.onerror = () => {
        topdown.onerror = null;
        topdown.src = `/designs/${encodeURIComponent(name)}/massing/topdown.png`;
      };

      _updateActive = (sceneId) => {
        const room = roomsById[sceneId];
        Array.from(list.children).forEach((li) => {
          li.classList.toggle("active", li.dataset.sceneId === sceneId);
        });
        setRoomInfo(room);
        setTopdownToRoom(sceneId);
        if (multiFloor && room) setActiveFloor(room.floor);
      };

      viewer.on("scenechange", _updateActive);
      _updateActive(targetScene);

      setStyle(house.style);
      setRequirementsLog(reqs.requirements || []);
    } finally {
      overlay.classList.remove("visible");
    }
  }

  // ---- Chat / SSE flow ---------------------------------------------------

  async function submitPrompt() {
    if (inFlight || !currentDesign) return;
    const input = document.getElementById("prompt-input");
    const text = input.value.trim();
    if (!text) return;

    setInFlight(true);
    input.value = "";
    appendUserBubble(text);
    const agent = appendAgentBubble();

    try {
      const res = await fetch(
        `/designs/${encodeURIComponent(currentDesign)}/prompt`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text }),
        },
      );
      if (!res.ok) {
        const detail = await res.text().catch(() => `HTTP ${res.status}`);
        appendAgentError(agent, detail);
        return;
      }
      await readSSE(res, (event) => onEvent(agent, event, text));
    } catch (err) {
      appendAgentError(agent, err.message || String(err));
    } finally {
      setInFlight(false);
    }
  }

  function onEvent(agentEl, event, userPrompt) {
    if (event.type === "session") {
      // Ignore — the server already saved it.
      return;
    }
    if (event.type === "status") {
      const loader = agentEl.querySelector(".loading-indicator");
      if (loader) {
        const label = loader.querySelector(".loading-label");
        if (label) label.textContent = event.label || event.tool || "Working\u2026";
      }
      scrollChat();
      return;
    }
    if (event.type === "error") {
      const loader = agentEl.querySelector(".loading-indicator");
      if (loader) loader.remove();
      appendAgentError(agentEl, event.message || "Something went wrong.");
      return;
    }
    if (event.type === "result") {
      // Drop the loading indicator once the result is in.
      const loader = agentEl.querySelector(".loading-indicator");
      if (loader) loader.remove();
      const result = event.extractor_result || {};
      if (result.kind === "clarification") {
        const bubble = document.createElement("div");
        bubble.className = "bubble clarify";
        bubble.textContent = result.question || "Could you tell me more?";
        agentEl.appendChild(bubble);
      } else if (result.kind === "diffs") {
        renderDiffs(agentEl, result.diffs || [], userPrompt);
      } else {
        appendAgentError(agentEl, "Unexpected response from Claude.");
      }
      scrollChat();
    }
  }

  function renderDiffs(agentEl, diffs, userPrompt) {
    if (!diffs.length) {
      const bubble = document.createElement("div");
      bubble.className = "bubble";
      bubble.textContent = "No changes proposed.";
      agentEl.appendChild(bubble);
      return;
    }

    const wrapper = document.createElement("div");
    wrapper.className = "bubble";
    wrapper.style.padding = "6px 8px";

    const intro = document.createElement("div");
    intro.style.marginBottom = "4px";
    intro.style.fontSize = "12px";
    intro.style.color = "var(--muted)";
    intro.textContent = `Proposed ${diffs.length} change${diffs.length === 1 ? "" : "s"}:`;
    wrapper.appendChild(intro);

    diffs.forEach((diff, idx) => {
      const card = document.createElement("div");
      card.className = "diff-card";
      const conflicts = (diff.conflicts_with || []).length
        ? `<div class="conflict">⚠ Conflicts with: ${diff.conflicts_with.join(", ")}${
            diff.suggested_resolution ? ` — ${escapeHtml(diff.suggested_resolution)}` : ""
          }</div>`
        : "";
      const mut = mutationSummary(diff.mutation);
      card.innerHTML = `
        <div class="top">
          <input type="checkbox" data-idx="${idx}" checked />
          <div class="body">
            <div><strong>${escapeHtml(diff.proposed.statement)}</strong></div>
            <div class="meta">${escapeHtml(diff.proposed.type)} · scope: ${escapeHtml(diff.proposed.scope)}${
              (diff.affected_rooms || []).length
                ? " · affects: " + diff.affected_rooms.map(escapeHtml).join(", ")
                : ""
            }</div>
            ${mut ? `<div class="mut">${escapeHtml(mut)}</div>` : ""}
            <div class="span">"${escapeHtml(diff.source_span)}"</div>
            ${conflicts}
          </div>
        </div>
      `;
      wrapper.appendChild(card);
    });

    const reasonInput = document.createElement("input");
    reasonInput.type = "text";
    reasonInput.className = "reject-reason";
    reasonInput.placeholder = "Reason if rejecting (optional)";
    wrapper.appendChild(reasonInput);

    const actions = document.createElement("div");
    actions.className = "diff-actions";
    actions.innerHTML = `
      <button type="button" class="primary approve-btn">Approve selected</button>
      <button type="button" class="reject-btn">Reject selected</button>
    `;
    wrapper.appendChild(actions);

    actions.querySelector(".approve-btn").addEventListener("click", () =>
      submitDecision(wrapper, diffs, userPrompt, "approve", reasonInput),
    );
    actions.querySelector(".reject-btn").addEventListener("click", () =>
      submitDecision(wrapper, diffs, userPrompt, "reject", reasonInput),
    );

    agentEl.appendChild(wrapper);
  }

  async function submitDecision(wrapper, diffs, userPrompt, kind, reasonInput) {
    if (inFlight) return;

    const checked = Array.from(wrapper.querySelectorAll('input[type="checkbox"]'))
      .filter((cb) => cb.checked)
      .map((cb) => Number(cb.dataset.idx));
    if (!checked.length) {
      toast("Select at least one change first.", "error");
      return;
    }
    const selected = checked.map((i) => diffs[i]);
    const reason = reasonInput && reasonInput.value.trim() ? reasonInput.value.trim() : null;

    const url =
      kind === "approve"
        ? `/designs/${encodeURIComponent(currentDesign)}/requirements/approve`
        : `/designs/${encodeURIComponent(currentDesign)}/requirements/reject`;

    setDecisionPending(wrapper, true);

    try {
      const payload = { diffs: selected, user_prompt: userPrompt };
      if (kind === "reject") payload.reason = reason;

      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = await res.json().catch(() => ({}));

      if (kind === "approve") {
        if (res.status === 409) {
          renderBlockedIssues(wrapper, body.issues || []);
          setDecisionPending(wrapper, false);
          return;
        }
        if (!res.ok) {
          toast(body.detail || `HTTP ${res.status}`, "error");
          setDecisionPending(wrapper, false);
          return;
        }
        replaceWithResolved(wrapper, "ok", `✓ Approved (${(body.applied || []).join(", ")})`);
        toast(`Approved ${(body.applied || []).join(", ")}`, "ok");
        await refreshDesign(currentDesign);  // refresh viewer + topdown + reqs log
      } else {
        if (!res.ok) {
          toast(body.detail || `HTTP ${res.status}`, "error");
          setDecisionPending(wrapper, false);
          return;
        }
        replaceWithResolved(wrapper, "rej", `✗ Rejected (${(body.rejected || []).join(", ")})`);
        toast(`Rejected ${(body.rejected || []).join(", ")}`, "ok");
        await refreshRequirementsLog();
      }
    } catch (err) {
      toast(err.message || String(err), "error");
      setDecisionPending(wrapper, false);
    }
  }

  function setDecisionPending(wrapper, pending) {
    wrapper.querySelectorAll("button").forEach((b) => (b.disabled = pending));
  }

  function replaceWithResolved(wrapper, kind, label) {
    const note = document.createElement("div");
    note.className = "resolved " + kind;
    note.textContent = label;
    wrapper.replaceWith(note);
  }

  function renderBlockedIssues(wrapper, issues) {
    const existing = wrapper.querySelector(".blocked-banner");
    if (existing) existing.remove();
    const banner = document.createElement("div");
    banner.className = "blocked-banner";
    banner.style.cssText =
      "margin-top:6px;padding:6px 8px;border:1px solid #e88a8a;border-radius:4px;color:#f1c4c4;background:#3a2828;font-size:12px";
    banner.innerHTML =
      "<strong>Can't apply yet:</strong><ul style=\"margin:4px 0 0 18px;padding:0\">" +
      issues.map((i) => `<li>[${escapeHtml(i.code)}] ${escapeHtml(i.message)}</li>`).join("") +
      "</ul>";
    wrapper.insertBefore(banner, wrapper.querySelector(".diff-actions"));
  }

  async function newChat() {
    if (inFlight || !currentDesign) return;
    if (!confirm("Start a fresh conversation? Pending decisions in this chat will be hidden.")) return;
    try {
      await fetch(`/designs/${encodeURIComponent(currentDesign)}/sessions/clear`, {
        method: "POST",
      });
    } catch (err) {
      toast(err.message || String(err), "error");
      return;
    }
    document.getElementById("chat-log").innerHTML = "";
    toast("Started a new chat.", "ok");
  }

  // ---- SSE parsing -------------------------------------------------------

  async function readSSE(response, onEvent) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) !== -1) {
        const frame = buf.slice(0, idx).trim();
        buf = buf.slice(idx + 2);
        if (!frame.startsWith("data:")) continue;
        const payload = frame.slice("data:".length).trim();
        if (!payload) continue;
        try {
          onEvent(JSON.parse(payload));
        } catch {
          // skip malformed frames
        }
      }
    }
  }

  // ---- Chat-bubble helpers -----------------------------------------------

  function appendUserBubble(text) {
    const log = document.getElementById("chat-log");
    const msg = document.createElement("div");
    msg.className = "msg user";
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.textContent = text;
    msg.appendChild(bubble);
    log.appendChild(msg);
    scrollChat();
  }

  function appendAgentBubble() {
    const log = document.getElementById("chat-log");
    const msg = document.createElement("div");
    msg.className = "msg agent";
    const loader = document.createElement("div");
    loader.className = "loading-indicator";
    loader.innerHTML =
      '<div class="loading-dots"><span></span><span></span><span></span></div>' +
      '<span class="loading-label">Thinking\u2026</span>';
    msg.appendChild(loader);
    log.appendChild(msg);
    scrollChat();
    return msg;
  }

  function appendAgentError(agentEl, message) {
    const bubble = document.createElement("div");
    bubble.className = "bubble error";
    bubble.textContent = `Error: ${message}`;
    agentEl.appendChild(bubble);
    scrollChat();
  }

  function scrollChat() {
    const log = document.getElementById("chat-log");
    log.scrollTop = log.scrollHeight;
  }

  async function refreshRequirementsLog() {
    const reqs = await fetch(`/designs/${encodeURIComponent(currentDesign)}/requirements.jsonl`)
      .then((r) => r.json())
      .catch(() => ({ requirements: [] }));
    setRequirementsLog(reqs.requirements || []);
  }

  function setRequirementsLog(reqs) {
    const el = document.getElementById("reqs-log");
    if (!reqs.length) {
      el.innerHTML = `<p style="margin:0;color:var(--muted)">None yet.</p>`;
      return;
    }
    const last = reqs.slice(-5).reverse();
    el.innerHTML = last
      .map(
        (r) => `
      <p style="margin:4px 0">
        <code style="color:var(--accent)">${r.id}</code>
        <span class="chip" style="margin-left:4px">${r.status}</span>
        <span style="color:var(--muted)"> · ${escapeHtml(r.type)}</span>
        <br/>${escapeHtml(r.statement)}
      </p>`,
      )
      .join("");
  }

  function mutationSummary(m) {
    if (!m) return "";
    if (m.op === "add_room") return `add_room: ${m.room.id} (${roomDims(m.room)} on floor ${m.room.floor})`;
    if (m.op === "update_room") {
      const fields = ["name", "polygon", "floor", "camera", "ceiling_height_m"]
        .filter((k) => m[k] !== undefined && m[k] !== null);
      return `update_room: ${m.room_id} (${fields.join(", ") || "no fields"})`;
    }
    if (m.op === "remove_room") return `remove_room: ${m.room_id}`;
    if (m.op === "add_opening") return `add_opening: ${m.room_id} ${m.opening.type} on ${m.opening.wall} wall`;
    if (m.op === "remove_opening") return `remove_opening: ${m.room_id} #${m.opening_index}`;
    return JSON.stringify(m);
  }

  function setInFlight(flag) {
    inFlight = flag;
    document.getElementById("prompt-send").disabled = flag;
  }

  let toastTimer = null;
  function toast(message, kind) {
    const el = document.getElementById("toast");
    el.className = "toast" + (kind ? " " + kind : "") + " show";
    el.textContent = message;
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("show"), 2400);
  }

  function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    })[c]);
  }

  // ---- Side-panel helpers ------------------------------------------------

  function setRoomInfo(room) {
    const el = document.getElementById("room-info");
    if (!room) {
      el.innerHTML = "";
      return;
    }
    const dims = roomDims(room);
    const doors = room.openings.filter((o) => o.type === "door").map((o) => `${o.wall} → ${o.to_room}`);
    const windows = room.openings.filter((o) => o.type === "window").map((o) => `${o.wall} (${o.width_m.toFixed(1)} m)`);
    el.innerHTML = `
      <h3>${room.name}</h3>
      <p>${dims} · floor ${room.floor} · ceiling ${room.ceiling_height_m.toFixed(1)} m</p>
      <p>camera (${room.camera.x.toFixed(1)}, ${room.camera.y.toFixed(1)}), yaw ${room.camera.yaw_deg}°</p>
      <p><strong>Doors:</strong> ${doors.length ? doors.join(", ") : "—"}</p>
      <p><strong>Windows:</strong> ${windows.length ? windows.join(", ") : "—"}</p>
    `;
  }

  function setStyle(style) {
    const el = document.getElementById("style-info");
    const chips = (style.materials || []).map((m) => `<span class="chip">${m}</span>`).join("");
    const moods = (style.mood_tokens || []).map((m) => `<span class="chip">${m}</span>`).join("");
    el.innerHTML = `
      <p><strong>${style.period}</strong> · ${style.lighting}</p>
      <div class="chips">${chips}</div>
      <div class="chips" style="margin-top:6px">${moods}</div>
    `;
  }

  function roomDims(room) {
    const xs = room.polygon.map((p) => p[0]);
    const ys = room.polygon.map((p) => p[1]);
    const w = Math.max(...xs) - Math.min(...xs);
    const h = Math.max(...ys) - Math.min(...ys);
    return `${w.toFixed(1)} × ${h.toFixed(1)} m`;
  }

  function floorLabel(floor) {
    if (floor === 0) return "Ground floor";
    if (floor === 1) return "First floor";
    return `Floor ${floor}`;
  }
})();
