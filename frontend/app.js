/**
 * HROne Attendance Dashboard — JavaScript
 * Communicates with the FastAPI backend at API_BASE.
 * Uses plain Canvas API for charts (no external chart library needed).
 */

const API_BASE = "http://localhost:8000";

// ============================================================
//  Utilities
// ============================================================

async function apiFetch(path, options = {}) {
  const url = `${API_BASE}${path}`;
  const resp = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await resp.json().catch(() => ({}));
  return { ok: resp.ok, status: resp.status, data };
}

function fmtDateTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("en-IN", {
    timeZone: "Asia/Kolkata",
    year: "numeric", month: "short", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: true,
  });
}

function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata", year: "numeric", month: "short", day: "2-digit" });
}

function showResponse(boxId, ok, data) {
  const box = document.getElementById(boxId);
  box.className = `response-box ${ok ? "success" : "error"}`;
  box.textContent = JSON.stringify(data, null, 2);
}

function toLocalDatetimeInput(date) {
  const d = new Date(date);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
}

function localInputToUTC(localStr) {
  if (!localStr) return undefined;
  return new Date(localStr).toISOString();
}

// ============================================================
//  Clock
// ============================================================

function updateClock() {
  const el = document.getElementById("current-time");
  if (!el) return;
  const now = new Date();
  el.textContent = now.toLocaleString("en-IN", {
    timeZone: "Asia/Kolkata",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: true,
    weekday: "short", day: "2-digit", month: "short",
  });
}

setInterval(updateClock, 1000);
updateClock();

// ============================================================
//  API Health Check
// ============================================================

async function checkHealth() {
  const dot = document.querySelector(".status-dot");
  const txt = document.querySelector(".status-text");
  try {
    const { ok } = await apiFetch("/health");
    dot.className = `status-dot ${ok ? "ok" : "err"}`;
    txt.textContent = ok ? "API Connected" : "API Error";
  } catch {
    dot.className = "status-dot err";
    txt.textContent = "API Offline";
  }
}

checkHealth();
setInterval(checkHealth, 30000);

// ============================================================
//  Page Navigation
// ============================================================

const PAGE_META = {
  dashboard:   { title: "Dashboard",   subtitle: "Real-time attendance overview" },
  employees:   { title: "Employees",   subtitle: "Manage your workforce" },
  attendance:  { title: "Attendance",  subtitle: "Punch in / punch out operations" },
  analytics:   { title: "Analytics",   subtitle: "MongoDB-powered aggregation insights" },
  corrections: { title: "Corrections", subtitle: "Manual attendance corrections with full audit trail" },
};

function showPage(name) {
  document.querySelectorAll(".page").forEach(p => p.classList.add("hidden"));
  document.querySelectorAll(".nav-item").forEach(n => n.classList.remove("active"));
  document.getElementById(`page-${name}`).classList.remove("hidden");
  document.querySelector(`[data-page="${name}"]`).classList.add("active");
  const meta = PAGE_META[name] || {};
  document.getElementById("page-title").textContent = meta.title || name;
  document.getElementById("page-subtitle").textContent = meta.subtitle || "";
  if (name === "dashboard") loadDashboard();
  if (name === "employees") loadEmployees();
}

// ============================================================
//  DASHBOARD
// ============================================================

async function loadDashboard() {
  const today = new Date().toISOString().slice(0, 10);
  const [sumRes, empRes] = await Promise.all([
    apiFetch(`/analytics/summary?start_date=${today}T00:00:00Z&end_date=${today}T00:00:00Z`),
    apiFetch("/employees"),
  ]);

  const s = sumRes.data;
  document.getElementById("stat-employees").textContent =
    empRes.ok ? empRes.data.length : "?";
  document.getElementById("stat-present").textContent =
    sumRes.ok ? (s.present_records ?? 0) : "?";
  document.getElementById("stat-incomplete").textContent =
    sumRes.ok ? (s.incomplete_records ?? 0) : "?";
  document.getElementById("stat-avghours").textContent =
    sumRes.ok ? `${s.avg_hours_per_day ?? 0}h` : "?";

  loadTrend(7, document.querySelector(".pill.active"));
  loadDeptSummary();
}

async function loadDeptSummary() {
  const { ok, data } = await apiFetch("/analytics/department");
  const el = document.getElementById("dept-list");
  if (!ok || !data.length) {
    el.innerHTML = '<p class="muted">No department data yet.</p>';
    return;
  }
  const maxHours = Math.max(...data.map(d => d.total_hours || 0), 1);
  el.innerHTML = data.map(d => `
    <div class="dept-row">
      <div class="dept-row-top">
        <span class="dept-name">${d.department}</span>
        <span class="dept-meta">${d.total_hours}h · ${d.employee_count} emp</span>
      </div>
      <div class="dept-bar-bg">
        <div class="dept-bar-fill" style="width:${Math.round((d.total_hours / maxHours) * 100)}%"></div>
      </div>
    </div>
  `).join("");
}

// ============================================================
//  TREND CHART (pure Canvas)
// ============================================================

let _trendChart = null;

async function loadTrend(days, btn) {
  if (btn) {
    document.querySelectorAll(".date-range-pills .pill").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
  }
  const end = new Date();
  const start = new Date(end);
  start.setDate(start.getDate() - days + 1);
  const fmt = d => d.toISOString().slice(0, 10);
  const { ok, data } = await apiFetch(
    `/analytics/trend/daily?start_date=${fmt(start)}T00:00:00Z&end_date=${fmt(end)}T00:00:00Z`
  );
  drawTrendChart("trend-chart", ok ? data : []);
}

function drawTrendChart(canvasId, rows) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.parentElement.getBoundingClientRect();
  const W = rect.width - 48;
  const H = parseInt(canvas.getAttribute("height")) || 200;
  canvas.width = W * dpr;
  canvas.height = H * dpr;
  canvas.style.width = W + "px";
  canvas.style.height = H + "px";
  ctx.scale(dpr, dpr);

  ctx.clearRect(0, 0, W, H);

  if (!rows.length) {
    ctx.fillStyle = "#4f5b73";
    ctx.font = "14px Inter";
    ctx.textAlign = "center";
    ctx.fillText("No data for this period", W / 2, H / 2);
    return;
  }

  const PAD = { top: 16, right: 16, bottom: 36, left: 44 };
  const cW = W - PAD.left - PAD.right;
  const cH = H - PAD.top - PAD.bottom;
  const maxHours = Math.max(...rows.map(r => r.avg_hours || 0), 1);
  const maxPresent = Math.max(...rows.map(r => r.present_records || 0), 1);

  // Grid lines
  ctx.strokeStyle = "rgba(255,255,255,0.05)";
  ctx.lineWidth = 1;
  [0, 0.25, 0.5, 0.75, 1].forEach(frac => {
    const y = PAD.top + cH * (1 - frac);
    ctx.beginPath(); ctx.moveTo(PAD.left, y); ctx.lineTo(PAD.left + cW, y); ctx.stroke();
    ctx.fillStyle = "#4f5b73"; ctx.font = "10px Inter"; ctx.textAlign = "right";
    ctx.fillText((maxHours * frac).toFixed(1) + "h", PAD.left - 6, y + 4);
  });

  const step = cW / Math.max(rows.length - 1, 1);

  // Bar chart (present records count)
  rows.forEach((row, i) => {
    const x = PAD.left + i * step;
    const barH = (row.present_records / maxPresent) * cH * 0.5;
    const grad = ctx.createLinearGradient(0, PAD.top + cH - barH, 0, PAD.top + cH);
    grad.addColorStop(0, "rgba(79,142,247,0.3)");
    grad.addColorStop(1, "rgba(79,142,247,0.04)");
    ctx.fillStyle = grad;
    const barW = Math.min(step * 0.5, 24);
    ctx.fillRect(x - barW / 2, PAD.top + cH - barH, barW, barH);
  });

  // Line chart (avg hours)
  const gradient = ctx.createLinearGradient(PAD.left, PAD.top, PAD.left + cW, PAD.top);
  gradient.addColorStop(0, "#4f8ef7");
  gradient.addColorStop(1, "#8b5cf6");
  ctx.strokeStyle = gradient;
  ctx.lineWidth = 2.5;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.beginPath();
  rows.forEach((row, i) => {
    const x = PAD.left + i * step;
    const y = PAD.top + cH * (1 - (row.avg_hours || 0) / maxHours);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.stroke();

  // Area fill under line
  const areaGrad = ctx.createLinearGradient(0, PAD.top, 0, PAD.top + cH);
  areaGrad.addColorStop(0, "rgba(79,142,247,0.18)");
  areaGrad.addColorStop(1, "rgba(79,142,247,0)");
  ctx.fillStyle = areaGrad;
  ctx.beginPath();
  rows.forEach((row, i) => {
    const x = PAD.left + i * step;
    const y = PAD.top + cH * (1 - (row.avg_hours || 0) / maxHours);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.lineTo(PAD.left + (rows.length - 1) * step, PAD.top + cH);
  ctx.lineTo(PAD.left, PAD.top + cH);
  ctx.closePath(); ctx.fill();

  // Dots + labels
  rows.forEach((row, i) => {
    const x = PAD.left + i * step;
    const y = PAD.top + cH * (1 - (row.avg_hours || 0) / maxHours);
    ctx.beginPath();
    ctx.arc(x, y, 4, 0, Math.PI * 2);
    ctx.fillStyle = "#4f8ef7";
    ctx.fill();
    ctx.strokeStyle = "#0f1525";
    ctx.lineWidth = 2;
    ctx.stroke();

    // X-axis label
    if (i % Math.ceil(rows.length / 8) === 0 || i === rows.length - 1) {
      ctx.fillStyle = "#4f5b73";
      ctx.font = "10px Inter";
      ctx.textAlign = "center";
      const label = row.date ? row.date.slice(5) : "";
      ctx.fillText(label, x, PAD.top + cH + 20);
    }
  });
}

// ============================================================
//  EMPLOYEES PAGE
// ============================================================

let _allEmployees = [];

async function loadEmployees() {
  const { ok, data } = await apiFetch("/employees");
  _allEmployees = ok ? data : [];
  renderEmployeeTable(_allEmployees);
}

function renderEmployeeTable(employees) {
  const tbody = document.getElementById("emp-tbody");
  if (!employees.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="table-empty">No employees found. Add one to get started.</td></tr>';
    return;
  }
  tbody.innerHTML = employees.map(e => `
    <tr>
      <td><code style="color:var(--blue)">${e.employee_id}</code></td>
      <td><strong>${e.name}</strong></td>
      <td>${e.department}</td>
      <td style="color:var(--text-secondary)">${e.email}</td>
      <td style="color:var(--text-muted)">${fmtDate(e.created_at)}</td>
      <td>
        <button class="btn btn-ghost btn-sm" onclick="viewEmployee('${e.employee_id}')">View</button>
      </td>
    </tr>
  `).join("");
}

function filterEmployees() {
  const q = document.getElementById("emp-search").value.toLowerCase();
  const filtered = _allEmployees.filter(e =>
    e.employee_id.toLowerCase().includes(q) ||
    e.name.toLowerCase().includes(q) ||
    e.department.toLowerCase().includes(q)
  );
  renderEmployeeTable(filtered);
}

function viewEmployee(empId) {
  document.getElementById("hist-emp").value = empId;
  showPage("attendance");
  loadHistory();
}

// ============================================================
//  EMPLOYEE MODAL
// ============================================================

function openModal(id) {
  document.getElementById(id).classList.remove("hidden");
}

function closeModal(id) {
  document.getElementById(id).classList.add("hidden");
}

function closeModalOutside(evt, id) {
  if (evt.target === document.getElementById(id)) closeModal(id);
}

async function addEmployee() {
  const payload = {
    employee_id: document.getElementById("new-emp-id").value.trim(),
    name: document.getElementById("new-emp-name").value.trim(),
    department: document.getElementById("new-emp-dept").value.trim(),
    email: document.getElementById("new-emp-email").value.trim(),
  };
  const { ok, data } = await apiFetch("/employees", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  showResponse("emp-modal-response", ok, data);
  if (ok) {
    setTimeout(() => {
      closeModal("modal-add-employee");
      loadEmployees();
    }, 1200);
  }
}

// ============================================================
//  ATTENDANCE PAGE
// ============================================================

async function doPunchIn() {
  const empId = document.getElementById("pi-employee").value.trim();
  const ts = document.getElementById("pi-timestamp").value;
  const payload = { employee_id: empId };
  if (ts) payload.timestamp = localInputToUTC(ts);
  const { ok, data } = await apiFetch("/attendance/punch-in", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  showResponse("pi-response", ok, data);
}

async function doPunchOut() {
  const empId = document.getElementById("po-employee").value.trim();
  const ts = document.getElementById("po-timestamp").value;
  const payload = { employee_id: empId };
  if (ts) payload.timestamp = localInputToUTC(ts);
  const { ok, data } = await apiFetch("/attendance/punch-out", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  showResponse("po-response", ok, data);
}

async function loadHistory() {
  const empId = document.getElementById("hist-emp").value.trim();
  if (!empId) { alert("Enter an Employee ID"); return; }
  const start = document.getElementById("hist-start").value;
  const end = document.getElementById("hist-end").value;
  let url = `/attendance/${empId}`;
  const params = [];
  if (start) params.push(`start_date=${start}T00:00:00Z`);
  if (end)   params.push(`end_date=${end}T00:00:00Z`);
  if (params.length) url += "?" + params.join("&");

  const { ok, data } = await apiFetch(url);
  const tbody = document.getElementById("hist-tbody");
  if (!ok) {
    tbody.innerHTML = `<tr><td colspan="6" class="table-empty" style="color:var(--red)">${data.detail || "Error"}</td></tr>`;
    return;
  }
  if (!data.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="table-empty">No records found.</td></tr>';
    return;
  }
  tbody.innerHTML = data.map(r => `
    <tr>
      <td>${fmtDate(r.date)}</td>
      <td>${fmtDateTime(r.punch_in)}</td>
      <td>${r.punch_out ? fmtDateTime(r.punch_out) : '<span style="color:var(--text-muted)">Open</span>'}</td>
      <td>${r.duration_minutes !== null ? r.duration_minutes + " min" : "—"}</td>
      <td><span class="badge badge-${r.status}">${r.status}</span></td>
      <td>
        <span style="color:var(--text-muted);font-size:0.78rem">${r.corrections.length} correction(s)</span>
        <button class="btn btn-ghost btn-sm" style="margin-left:0.5rem"
          onclick="prefillCorrection('${r.id}')">Correct</button>
      </td>
    </tr>
  `).join("");
}

function prefillCorrection(attId) {
  document.getElementById("cor-id").value = attId;
  showPage("corrections");
}

// ============================================================
//  ANALYTICS PAGE
// ============================================================

async function loadAnalytics() {
  const start = document.getElementById("an-start").value;
  const end = document.getElementById("an-end").value;
  const empId = document.getElementById("an-emp").value.trim();

  const params = [];
  if (start) params.push(`start_date=${start}T00:00:00Z`);
  if (end)   params.push(`end_date=${end}T00:00:00Z`);
  if (empId) params.push(`employee_id=${empId}`);
  const qs = params.length ? "?" + params.join("&") : "";

  const [sumRes, deptRes, trendRes] = await Promise.all([
    apiFetch(`/analytics/summary${qs}`),
    apiFetch(`/analytics/department${qs}`),
    apiFetch(`/analytics/trend/daily${qs}`),
  ]);

  // Summary
  const s = sumRes.data;
  const summaryEl = document.getElementById("analytics-summary");
  if (!sumRes.ok) {
    summaryEl.innerHTML = `<p class="muted" style="color:var(--red)">${s.detail || "Error"}</p>`;
  } else {
    summaryEl.innerHTML = [
      { label: "Total Records",   value: s.total_records },
      { label: "Present",          value: s.present_records },
      { label: "Incomplete",       value: s.incomplete_records },
      { label: "Total Hours",      value: s.total_hours + "h" },
      { label: "Avg Hours / Day",  value: s.avg_hours_per_day + "h" },
      { label: "Employees",        value: s.unique_employees_count },
    ].map(item => `
      <div class="analytics-stat">
        <div class="analytics-stat-value">${item.value}</div>
        <div class="analytics-stat-label">${item.label}</div>
      </div>
    `).join("");
  }

  // Departments
  const deptEl = document.getElementById("analytics-dept");
  if (!deptRes.ok || !deptRes.data.length) {
    deptEl.innerHTML = '<p class="muted">No department data.</p>';
  } else {
    const maxH = Math.max(...deptRes.data.map(d => d.total_hours || 0), 1);
    deptEl.innerHTML = deptRes.data.map(d => `
      <div class="dept-row">
        <div class="dept-row-top">
          <span class="dept-name">${d.department}</span>
          <span class="dept-meta">${d.total_hours}h · ${d.employee_count} emp</span>
        </div>
        <div class="dept-bar-bg">
          <div class="dept-bar-fill" style="width:${Math.round((d.total_hours / maxH) * 100)}%"></div>
        </div>
      </div>
    `).join("");
  }

  // Trend chart
  drawTrendChart("analytics-trend-chart", trendRes.ok ? trendRes.data : []);
}

// ============================================================
//  CORRECTIONS PAGE
// ============================================================

async function doCorrection() {
  const attId = document.getElementById("cor-id").value.trim();
  const pi = document.getElementById("cor-punch-in").value;
  const po = document.getElementById("cor-punch-out").value;
  const reason = document.getElementById("cor-reason").value.trim();
  const by = document.getElementById("cor-by").value.trim();

  if (!attId || !pi || !reason || !by) {
    showResponse("cor-response", false, { detail: "Attendance ID, punch_in, reason and corrected_by are required." });
    return;
  }

  const payload = {
    punch_in: localInputToUTC(pi),
    reason,
    corrected_by: by,
  };
  if (po) payload.punch_out = localInputToUTC(po);

  const { ok, data } = await apiFetch(`/attendance/${attId}/correct`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
  showResponse("cor-response", ok, data);
}

// ============================================================
//  INIT
// ============================================================

window.addEventListener("load", () => {
  loadDashboard();
  // Set default dates for analytics to last 30 days
  const now = new Date();
  const past = new Date(now);
  past.setDate(past.getDate() - 30);
  document.getElementById("an-end").value = now.toISOString().slice(0, 10);
  document.getElementById("an-start").value = past.toISOString().slice(0, 10);
});
