// The dashboard is a pure API client: it only fetches the JSON endpoints,
// never touches the database directly.

const fmtKm = (m) => (m == null ? "—" : (m / 1000).toFixed(2) + " km");
const fmtHr = (bpm) => (bpm == null ? "—" : bpm + " bpm");

function fmtDate(iso) {
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric", month: "short", day: "numeric",
  });
}

function fmtDuration(sec) {
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  return `${m}m ${String(s).padStart(2, "0")}s`;
}

async function getJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} -> ${res.status}`);
  return res.json();
}

async function loadWorkouts() {
  const tbody = document.querySelector("#workoutsTable tbody");
  const workouts = await getJSON("/workouts");  // already newest-first
  if (!workouts.length) {
    tbody.innerHTML = `<tr><td colspan="5" class="muted">No workouts yet.</td></tr>`;
    return;
  }
  tbody.innerHTML = workouts.map((w) => `
    <tr>
      <td>${fmtDate(w.start_time)}</td>
      <td>${w.activity_type}</td>
      <td class="num">${fmtDuration(w.duration_seconds)}</td>
      <td class="num">${fmtKm(w.total_distance_meters)}</td>
      <td class="num">${fmtHr(w.avg_heart_rate)}</td>
    </tr>`).join("");
}

async function loadWeeklyChart() {
  const weeks = await getJSON("/stats/weekly");
  const canvas = document.getElementById("weeklyChart");
  if (!weeks.length) {
    canvas.hidden = true;
    document.getElementById("chartEmpty").hidden = false;
    return;
  }
  new Chart(canvas, {
    type: "line",
    data: {
      labels: weeks.map((w) => w.week_start),
      datasets: [{
        label: "Distance (km)",
        data: weeks.map((w) => +(w.total_distance_meters / 1000).toFixed(2)),
        borderColor: "#2f6fed",
        backgroundColor: "rgba(47, 111, 237, 0.10)",
        fill: true,
        tension: 0.25,
        pointRadius: 3,
      }],
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: { y: { beginAtZero: true, title: { display: true, text: "km" } } },
    },
  });
}

(async () => {
  try {
    await Promise.all([loadWorkouts(), loadWeeklyChart()]);
  } catch (err) {
    console.error("Dashboard failed to load:", err);
    document.querySelector("#workoutsTable tbody").innerHTML =
      `<tr><td colspan="5" class="muted">Failed to load data — see console.</td></tr>`;
  }
})();
