// Workout detail page: a pure API client. Reads ?uuid= from the URL and
// fetches GET /workouts/{uuid}, which includes the full raw_payload.

const uuid = new URLSearchParams(location.search).get("uuid");

const fmtKm = (m) => (m == null ? "—" : (m / 1000).toFixed(2) + " km");
const fmtBpm = (b) => (b == null ? "—" : b + " bpm");

function fmtDateTime(iso) {
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: "medium", timeStyle: "short",
  });
}
function fmtDuration(sec) {
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  return `${m}m ${String(s).padStart(2, "0")}s`;
}

// Friendly names for the sliced stream arrays on a workout's raw_payload.
const SAMPLE_LABELS = {
  heart_rate_samples: "Heart rate",
  step_samples: "Steps",
  distance_samples: "Distance",
  total_calorie_samples: "Calories",
  active_energy_samples: "Active energy",
  basal_energy_samples: "Basal energy",
  speed_samples: "Speed",
  hrv_rmssd_samples: "HRV",
  resting_heart_rate_samples: "Resting HR",
  respiratory_rate_samples: "Respiratory rate",
  blood_oxygen_samples: "Blood oxygen",
  skin_temperature_samples: "Skin temp",
  body_temperature_samples: "Body temp",
  flights_climbed_samples: "Flights climbed",
  activity_intensity_samples: "Activity intensity",
};

const stat = (label, value) =>
  `<div class="stat"><div class="label">${label}</div><div class="value">${value}</div></div>`;

function renderHeader(w, raw) {
  const sub = [fmtDateTime(w.start_time), w.source_app, w.recording_method]
    .filter(Boolean).join(" · ");
  document.getElementById("detail").innerHTML = `
    <h2 class="detail-title">${w.activity_type}</h2>
    <p class="detail-sub">${sub}</p>
    <div class="stats">
      ${stat("Duration", fmtDuration(w.duration_seconds))}
      ${stat("Distance", fmtKm(w.total_distance_meters))}
      ${stat("Avg HR", fmtBpm(w.avg_heart_rate))}
      ${stat("Max HR", fmtBpm(w.max_heart_rate))}
      ${stat("Calories", w.total_energy_kcal == null ? "—" : w.total_energy_kcal + " kcal")}
      ${stat("Steps", w.total_steps == null ? "—" : w.total_steps.toLocaleString())}
    </div>`;
}

function renderHrChart(raw) {
  document.getElementById("hrCard").hidden = false;
  const hr = raw.heart_rate_samples || [];
  if (!hr.length) {
    document.getElementById("hrChart").hidden = true;
    document.getElementById("hrEmpty").hidden = false;
    return;
  }
  new Chart(document.getElementById("hrChart"), {
    type: "line",
    data: {
      labels: hr.map((s) => new Date(s.time).toLocaleTimeString()),
      datasets: [{
        label: "bpm",
        data: hr.map((s) => s.value),
        borderColor: "#e0245e",
        backgroundColor: "rgba(224, 36, 94, 0.08)",
        fill: true,
        pointRadius: 0,
        borderWidth: 1.5,
        tension: 0.2,
      }],
    },
    options: {
      responsive: true,
      animation: false,  // 900+ points: skip the animation
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { autoSkip: true, maxTicksLimit: 8 } },
        y: { title: { display: true, text: "bpm" } },
      },
    },
  });
}

function renderSampleChips(raw) {
  const chips = Object.entries(SAMPLE_LABELS)
    .map(([key, label]) => {
      const n = Array.isArray(raw[key]) ? raw[key].length : 0;
      return n > 0 ? `<span class="chip">${label}: ${n.toLocaleString()}</span>` : null;
    })
    .filter(Boolean);
  if (!chips.length) return;
  document.getElementById("samplesCard").hidden = false;
  document.getElementById("chips").innerHTML = chips.join("");
}

async function load() {
  const detail = document.getElementById("detail");
  if (!uuid) {
    detail.innerHTML = `<p class="muted">No workout id in the URL.</p>`;
    return;
  }
  const res = await fetch(`/workouts/${encodeURIComponent(uuid)}`);
  if (res.status === 404) {
    detail.innerHTML = `<p class="muted">Workout not found.</p>`;
    return;
  }
  if (!res.ok) throw new Error(`/workouts/${uuid} -> ${res.status}`);

  const w = await res.json();
  const raw = w.raw_payload || {};
  renderHeader(w, raw);
  renderHrChart(raw);
  renderSampleChips(raw);
}

load().catch((err) => {
  console.error("Failed to load workout:", err);
  document.getElementById("detail").innerHTML =
    `<p class="muted">Failed to load — see console.</p>`;
});
