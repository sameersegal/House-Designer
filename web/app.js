(function () {
  let viewer = null;

  document.addEventListener("DOMContentLoaded", main);

  async function main() {
    const designs = await fetch("designs.json").then((r) => r.json()).then((d) => d.designs || []);
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

    await loadDesign(initial);
  }

  async function loadDesign(name) {
    const [tour, house] = await Promise.all([
      fetch(`designs/${encodeURIComponent(name)}/tour.json`).then((r) => r.json()),
      fetch(`designs/${encodeURIComponent(name)}/house.json`).then((r) => r.json()),
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
      topdown.src = `designs/${encodeURIComponent(name)}/massing/${sceneId}/topdown.png?v=${Date.now()}`;
    };
    const setTopdownToFloor = (floor) => {
      topdown.src = `designs/${encodeURIComponent(name)}/massing/topdown-floor${floor}.png?v=${Date.now()}`;
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
      topdown.src = `designs/${encodeURIComponent(name)}/massing/topdown.png`;
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
  }

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
