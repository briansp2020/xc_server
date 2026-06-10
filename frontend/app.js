// The dashboard is a pure API client: it only fetches the JSON endpoints,
// never touches the database directly.

const PT = "America/Los_Angeles";  // show all times in Pacific (PST/PDT)

// DB datetime columns come back without a timezone; treat them as UTC so they
// aren't misread as browser-local.
function toDate(iso) {
  return new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z");
}

const fmtKm = (m) => (m == null ? "—" : (m / 1000).toFixed(2) + " km");
const fmtHr = (bpm) => (bpm == null ? "—" : bpm + " bpm");

function fmtDate(iso) {
  return toDate(iso).toLocaleDateString("en-US", {
    month: "short", day: "numeric", timeZone: PT,
  });
}

function fmtDuration(sec) {
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  return `${m}m ${String(s).padStart(2, "0")}s`;
}

// Totals read better without seconds: "3h 42m" / "47m".
function fmtTotalTime(sec) {
  const h = Math.floor(sec / 3600);
  const m = Math.round((sec % 3600) / 60);
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  return `${m}m`;
}

async function getJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} -> ${res.status}`);
  return res.json();
}

// ---- This-week hero -----------------------------------------------------

function setDelta(id, diff, text) {
  const el = document.getElementById(id);
  el.textContent = text;
  el.className = "delta " + (diff > 0 ? "up" : diff < 0 ? "down" : "flat");
}

function renderSummary(s) {
  const tw = s.this_week, lw = s.last_week;

  const start = toDate(tw.week_start + "T12:00:00Z");
  document.getElementById("weekRange").textContent =
    "week of " + start.toLocaleDateString("en-US", { month: "short", day: "numeric" });

  document.getElementById("wkDistance").textContent =
    (tw.total_distance_meters / 1000).toFixed(1) + " km";
  document.getElementById("wkTime").textContent =
    fmtTotalTime(tw.total_duration_seconds);
  document.getElementById("wkRuns").textContent = tw.run_count;

  if (lw.session_count === 0 && tw.session_count === 0) {
    setDelta("wkDistanceDelta", 0, "no activity yet");
    setDelta("wkTimeDelta", 0, "no activity yet");
    setDelta("wkRunsDelta", 0, "no activity yet");
    return;
  }

  const dDist = tw.total_distance_meters - lw.total_distance_meters;
  setDelta("wkDistanceDelta", dDist,
    `${dDist >= 0 ? "+" : "−"}${Math.abs(dDist / 1000).toFixed(1)} km vs last week`);

  const dTime = tw.total_duration_seconds - lw.total_duration_seconds;
  setDelta("wkTimeDelta", dTime,
    `${dTime >= 0 ? "+" : "−"}${fmtTotalTime(Math.abs(dTime))} vs last week`);

  const dRuns = tw.run_count - lw.run_count;
  setDelta("wkRunsDelta", dRuns,
    `${dRuns >= 0 ? "+" : "−"}${Math.abs(dRuns)} vs last week`);
}

// ---- Recent workouts ----------------------------------------------------

// One list combining detected sessions with any recorded workouts that have no
// matching session (e.g. manual entries with no sensor samples).
function buildRecent(sessions, workouts) {
  const matched = new Set(
    sessions.map((s) => s.matched_workout_uuid).filter(Boolean));

  const items = sessions.map((s) => ({
    href: `/session.html?id=${s.id}`,
    date: s.start_time,
    type: s.matched_activity_type || s.inferred_activity || "—",
    duration: s.duration_seconds,
    distance: s.total_distance_meters,
    avgHr: s.avg_hr,
    badge: s.matched_workout_uuid ? "recorded" : "detected",
  }));

  for (const w of workouts) {
    if (matched.has(w.source_uuid)) continue;
    items.push({
      href: `/workout.html?uuid=${encodeURIComponent(w.source_uuid)}`,
      date: w.start_time,
      type: w.activity_type || "—",
      duration: w.duration_seconds,
      distance: w.total_distance_meters,
      avgHr: w.avg_heart_rate,
      badge: "recorded",
    });
  }

  return items.sort((a, b) => toDate(b.date) - toDate(a.date)).slice(0, 10);
}

function renderRecent(items) {
  const tbody = document.querySelector("#recentTable tbody");
  if (!items.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="muted">No workouts yet — sync from the app.</td></tr>`;
    return;
  }
  tbody.innerHTML = items.map((it) => `
    <tr class="clickable" onclick="location.href='${it.href}'">
      <td>${fmtDate(it.date)}</td>
      <td>${it.type}</td>
      <td class="num">${it.duration == null ? "—" : fmtDuration(it.duration)}</td>
      <td class="num">${fmtKm(it.distance)}</td>
      <td class="num">${fmtHr(it.avgHr)}</td>
      <td><span class="badge badge-${it.badge}">${it.badge}</span></td>
    </tr>`).join("");
}

// ---- Weekly chart -------------------------------------------------------

function renderWeeklyChart(weeks) {
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

// ---- Load everything ----------------------------------------------------

(async () => {
  try {
    const [summary, sessions, workouts, weeks] = await Promise.all([
      getJSON("/stats/summary"),
      getJSON("/sessions"),
      getJSON("/workouts"),
      getJSON("/stats/weekly"),
    ]);
    renderSummary(summary);
    renderRecent(buildRecent(sessions, workouts));
    renderWeeklyChart(weeks);
  } catch (err) {
    console.error("Dashboard failed to load:", err);
    document.querySelector("#recentTable tbody").innerHTML =
      `<tr><td colspan="6" class="muted">Failed to load data — see console.</td></tr>`;
  }
})();
