const password = prompt("Admin password:");
const authHeader = "Basic " + btoa(`admin:${password || ""}`);

async function apiGet(path) {
  const res = await fetch(path, { headers: { Authorization: authHeader } });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status}: ${text}`);
  }
  return res.json();
}

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value ?? "";
  return div.innerHTML;
}

function renderAppointmentsTable(rows) {
  const container = document.getElementById("appointmentsTable");
  if (rows.length === 0) {
    container.innerHTML = '<p class="empty-state">No appointments match these filters.</p>';
    return;
  }
  const body = rows.map((r) => `
    <tr>
      <td>${escapeHtml(r.slot_date)}</td>
      <td>${escapeHtml(r.slot_time)}</td>
      <td>${escapeHtml(r.doctor_name)}</td>
      <td>${escapeHtml(r.patient_name)}</td>
      <td>${escapeHtml(r.phone_number)}</td>
      <td><span class="badge badge-${r.status}">${escapeHtml(r.status)}</span></td>
    </tr>
  `).join("");
  container.innerHTML = `
    <table>
      <thead><tr><th>Date</th><th>Time</th><th>Doctor</th><th>Patient</th><th>Phone</th><th>Status</th></tr></thead>
      <tbody>${body}</tbody>
    </table>
  `;
}

function renderCallsTable(rows) {
  const container = document.getElementById("callsTable");
  if (rows.length === 0) {
    container.innerHTML = '<p class="empty-state">No calls logged yet.</p>';
    return;
  }
  const body = rows.map((r) => `
    <tr>
      <td>${escapeHtml(new Date(r.started_at).toLocaleString())}</td>
      <td>${r.ended_at ? escapeHtml(new Date(r.ended_at).toLocaleString()) : "(in progress)"}</td>
      <td>${escapeHtml(r.outcome || "-")}</td>
    </tr>
  `).join("");
  container.innerHTML = `
    <table>
      <thead><tr><th>Started</th><th>Ended</th><th>Outcome</th></tr></thead>
      <tbody>${body}</tbody>
    </table>
  `;
}

async function loadDoctors() {
  const doctors = await apiGet("/api/doctors");
  const select = document.getElementById("doctorFilter");
  for (const d of doctors) {
    const opt = document.createElement("option");
    opt.value = d.id;
    opt.textContent = `${d.name} (${d.specialty})`;
    select.appendChild(opt);
  }
}

async function loadAppointments() {
  const date = document.getElementById("dateFilter").value;
  const doctorId = document.getElementById("doctorFilter").value;
  const status = document.getElementById("statusFilter").value;
  const params = new URLSearchParams();
  if (date) params.set("date", date);
  if (doctorId) params.set("doctor_id", doctorId);
  if (status) params.set("status", status);
  const rows = await apiGet(`/api/appointments?${params.toString()}`);
  renderAppointmentsTable(rows);
}

async function loadCalls() {
  const rows = await apiGet("/api/calls?limit=50");
  renderCallsTable(rows);
}

document.getElementById("filterForm").addEventListener("submit", (e) => {
  e.preventDefault();
  loadAppointments().catch(showError);
});

function showError(err) {
  document.getElementById("authError").innerHTML = `<p class="error-banner">${escapeHtml(err.message)}</p>`;
}

loadDoctors().catch(showError);
loadAppointments().catch(showError);
loadCalls().catch(showError);
