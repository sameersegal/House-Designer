(function () {
  let viewer = null;
  let currentDesign = null;
  let pendingDiffs = [];        // last RequirementDiff[] from /prompt
  let pendingPrompt = "";       // text submitted to /prompt
  let inFlight = false;

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

    await loadDesign(initial);
  }

  async function loadDesign(name) {
    currentDesign = name;
    pendingDiffs = [];
    pendingPrompt = "";
    renderDiffs(null);
    setPromptStatus("");

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

    const updateActive = (sceneId) => {
      const room = roomsById[sceneId];
      Array.from(list.children).forEach((li) => {
        li.classList.toggle("active", li.dataset.sceneId === sceneId);
      });
      setRoomInfo(room);
      setTopdownToRoom(sceneId);
      if (multiFloor && room) setActiveFloor(room.floor);
    };

    viewer.on("scenechange", updateActive);
    updateActive(firstSceneId);

    setStyle(house.style);
    setRequirementsLog(reqs.requirements || []);
  }

  // ---- Prompt + diff approval flow --------------------------------------

  async function submitPrompt() {
    if (inFlight) return;
    const input = document.getElementById("prompt-input");
    const text = input.value.trim();
    if (!text) return;
    if (!currentDesign) return;

    setInFlight(true);
    setPromptStatus("Thinking…");
    pendingPrompt = text;

    try {
      const res = await fetch(`/designs/${encodeURIComponent(currentDesign)}/prompt`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail = body && body.detail ? body.detail : `HTTP ${res.status}`;
        setPromptStatus(`Error: ${detail}`, "error");
        return;
      }
      if (body.kind === "clarification") {
        pendingDiffs = [];
        renderClarification(body.question);
        setPromptStatus("Clarification requested.", "ok");
      } else if (body.kind === "diffs") {
        pendingDiffs = body.diffs || [];
        renderDiffs(pendingDiffs);
        setPromptStatus(
          pendingDiffs.length
            ? `${pendingDiffs.length} proposed diff${pendingDiffs.length === 1 ? "" : "s"}.`
            : "No diffs proposed.",
          "ok",
        );
      } else {
        setPromptStatus("Unexpected response from extractor.", "error");
      }
    } catch (err) {
      setPromptStatus(`Error: ${err.message || err}`, "error");
    } finally {
      setInFlight(false);
    }
  }

  function renderDiffs(diffs) {
    const container = document.getElementById("diffs-container");
    if (diffs === null) {
      container.innerHTML = `<p style="color:var(--muted);font-size:12px;margin:4px 0">Send a prompt to see proposed diffs.</p>`;
      return;
    }
    if (!diffs.length) {
      container.innerHTML = `<p style="color:var(--muted);font-size:12px;margin:4px 0">No diffs.</p>`;
      return;
    }

    container.innerHTML = "";
    diffs.forEach((diff, idx) => {
      const card = document.createElement("div");
      card.className = "diff-card";
      const cb = `<input type="checkbox" data-idx="${idx}" checked />`;
      const conflicts = (diff.conflicts_with || []).length
        ? `<div class="conflict">⚠ Conflicts with: ${diff.conflicts_with.join(", ")}${diff.suggested_resolution ? ` — ${escapeHtml(diff.suggested_resolution)}` : ""}</div>`
        : "";
      const mut = mutationSummary(diff.mutation);
      card.innerHTML = `
        <div class="top">
          ${cb}
          <div class="body">
            <div><strong>${escapeHtml(diff.proposed.statement)}</strong></div>
            <div class="meta">${escapeHtml(diff.proposed.type)} · scope: ${escapeHtml(diff.proposed.scope)} · affects: ${(diff.affected_rooms || []).map(escapeHtml).join(", ") || "—"}</div>
            ${mut ? `<div class="mut">${escapeHtml(mut)}</div>` : ""}
            <div class="span">"${escapeHtml(diff.source_span)}"</div>
            ${conflicts}
          </div>
        </div>
      `;
      container.appendChild(card);
    });

    const reasonInput = document.createElement("input");
    reasonInput.type = "text";
    reasonInput.id = "reject-reason";
    reasonInput.className = "reject-reason";
    reasonInput.placeholder = "Reason for rejection (optional)";
    container.appendChild(reasonInput);

    const actions = document.createElement("div");
    actions.className = "diff-actions";
    actions.innerHTML = `
      <button type="button" id="approve-btn" class="primary">Approve selected</button>
      <button type="button" id="reject-btn">Reject selected</button>
    `;
    container.appendChild(actions);

    document.getElementById("approve-btn").addEventListener("click", () => submitDecision("approve"));
    document.getElementById("reject-btn").addEventListener("click", () => submitDecision("reject"));
  }

  function renderClarification(question) {
    const container = document.getElementById("diffs-container");
    container.innerHTML = `
      <div class="diff-clarify">
        <strong>Clarification:</strong> ${escapeHtml(question)}
      </div>
    `;
  }

  async function submitDecision(kind) {
    if (inFlight) return;
    if (!pendingDiffs.length) return;

    const checked = Array.from(
      document.querySelectorAll('#diffs-container input[type="checkbox"]'),
    )
      .filter((cb) => cb.checked)
      .map((cb) => Number(cb.dataset.idx));
    if (!checked.length) {
      toast("Select at least one diff first.", "error");
      return;
    }
    const selected = checked.map((i) => pendingDiffs[i]);
    const reasonEl = document.getElementById("reject-reason");
    const reason = reasonEl && reasonEl.value.trim() ? reasonEl.value.trim() : null;

    const url =
      kind === "approve"
        ? `/designs/${encodeURIComponent(currentDesign)}/requirements/approve`
        : `/designs/${encodeURIComponent(currentDesign)}/requirements/reject`;

    setInFlight(true);
    setPromptStatus(kind === "approve" ? "Approving…" : "Rejecting…");

    try {
      const payload = {
        diffs: selected,
        user_prompt: pendingPrompt,
      };
      if (kind === "reject") payload.reason = reason;

      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = await res.json().catch(() => ({}));

      if (kind === "approve") {
        if (res.status === 409) {
          renderBlockedIssues(body.issues || []);
          setPromptStatus("Approval blocked by validation.", "error");
          return;
        }
        if (!res.ok) {
          setPromptStatus(`Error: ${body.detail || `HTTP ${res.status}`}`, "error");
          return;
        }
        toast(`Approved ${(body.applied || []).join(", ")}`, "ok");
        pendingDiffs = [];
        renderDiffs([]);
        document.getElementById("prompt-input").value = "";
        await loadDesign(currentDesign);
        setPromptStatus("Applied.", "ok");
      } else {
        if (!res.ok) {
          setPromptStatus(`Error: ${body.detail || `HTTP ${res.status}`}`, "error");
          return;
        }
        toast(`Rejected ${(body.rejected || []).join(", ")}`, "ok");
        pendingDiffs = [];
        renderDiffs([]);
        await refreshRequirementsLog();
        setPromptStatus("Rejected.", "ok");
      }
    } catch (err) {
      setPromptStatus(`Error: ${err.message || err}`, "error");
    } finally {
      setInFlight(false);
    }
  }

  function renderBlockedIssues(issues) {
    const container = document.getElementById("diffs-container");
    const banner = document.createElement("div");
    banner.className = "diff-clarify";
    banner.style.borderColor = "#e88a8a";
    banner.innerHTML =
      `<strong>Blocked:</strong> projection failed validation:<ul style="margin:4px 0 0 18px;padding:0">` +
      issues.map((i) => `<li>[${escapeHtml(i.code)}] ${escapeHtml(i.message)}</li>`).join("") +
      `</ul>`;
    container.insertBefore(banner, container.firstChild);
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

  function setPromptStatus(text, kind) {
    const el = document.getElementById("prompt-status");
    el.className = "status" + (kind ? " " + kind : "");
    el.textContent = text;
  }

  function setInFlight(flag) {
    inFlight = flag;
    document.getElementById("prompt-send").disabled = flag;
    document.querySelectorAll("#diffs-container button").forEach((b) => (b.disabled = flag));
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

  // ---- Existing helpers --------------------------------------------------

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
