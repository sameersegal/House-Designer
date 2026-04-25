(async function () {
  const [tour, house] = await Promise.all([
    fetch("/tour.json").then((r) => r.json()),
    fetch("/house.json").then((r) => r.json()),
  ]);

  const firstSceneId = tour.default.firstScene;
  if (!firstSceneId) {
    document.getElementById("panorama").textContent = "No rooms defined.";
    return;
  }

  const viewer = pannellum.viewer("panorama", {
    default: { ...tour.default, sceneFadeDuration: 600 },
    scenes: tour.scenes,
  });

  const roomsById = Object.fromEntries(house.rooms.map((r) => [r.id, r]));
  const list = document.getElementById("rooms");
  list.innerHTML = "";
  house.rooms.forEach((room) => {
    const li = document.createElement("li");
    li.dataset.sceneId = room.id;
    li.innerHTML = `${room.name}<small>${roomDims(room)} · ceiling ${room.ceiling_height_m.toFixed(1)} m</small>`;
    li.addEventListener("click", () => viewer.loadScene(room.id));
    list.appendChild(li);
  });

  const topdown = document.getElementById("topdown");
  const setTopdown = (sceneId) => {
    topdown.src = `/massing/${sceneId}/topdown.png?v=${Date.now()}`;
  };
  topdown.onerror = () => {
    topdown.src = "/massing/topdown.png";
  };

  const updateActive = (sceneId) => {
    Array.from(list.children).forEach((li) => {
      li.classList.toggle("active", li.dataset.sceneId === sceneId);
    });
    setRoomInfo(roomsById[sceneId]);
    setTopdown(sceneId);
  };

  viewer.on("scenechange", updateActive);
  updateActive(firstSceneId);

  setStyle(house.style);

  const needle = document.getElementById("needle");
  const spin = () => {
    const yaw = viewer.getYaw();
    const scene = tour.scenes[viewer.getScene()] || {};
    const northOffset = scene.northOffset || 0;
    const bearing = yaw + northOffset;
    needle.style.transform = `translate(-50%, -100%) rotate(${bearing}deg)`;
    requestAnimationFrame(spin);
  };
  requestAnimationFrame(spin);

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
})();
