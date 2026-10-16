function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value ?? "";
  return div.innerHTML;
}

document.getElementById("lookupForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const phone = document.getElementById("phoneInput").value;
  const results = document.getElementById("results");
  results.innerHTML = "Checking...";

  try {
    const res = await fetch(`/api/lookup?phone=${encodeURIComponent(phone)}`);
    if (!res.ok) {
      const text = await res.text();
      let detail = text;
      try { detail = JSON.parse(text).detail || text; } catch {}
      results.innerHTML = `<p class="error-banner">${escapeHtml(detail)}</p>`;
      return;
    }
    const rows = await res.json();
    if (rows.length === 0) {
      results.innerHTML = '<p class="empty-state">No appointments found for that number.</p>';
      return;
    }
    results.innerHTML = rows.map((r) => `
      <div class="appt-card">
        <div class="doctor">${escapeHtml(r.doctor_name)}</div>
        <div class="when">${escapeHtml(r.slot_date)} at ${escapeHtml(r.slot_time)}</div>
        <span class="badge badge-${r.status}">${escapeHtml(r.status)}</span>
      </div>
    `).join("");
  } catch (err) {
    results.innerHTML = `<p class="error-banner">${escapeHtml(err.message)}</p>`;
  }
});
