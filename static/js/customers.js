
const tbody = document.getElementById("customers-table-body");
const message = document.getElementById("customer-message");
const search = document.getElementById("customer-search");
const filter = document.getElementById("risk-filter");

let customers = [];

function escapeHTML(value) {
  return String(value ?? "—").replace(/[&<>"']/g, ch => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;",
    '"': "&quot;", "'": "&#39;"
  })[ch]);
}

function numberValue(value) {
  if (value === null || value === undefined || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function money(value) {
  const n = numberValue(value);
  return n === null
    ? "—"
    : n.toLocaleString("en-IN", {
        style: "currency",
        currency: "INR",
        maximumFractionDigits: 0
      });
}

function getRiskLevel(customer) {
  const level = customer.risk_level || customer.risk_category;
  if (level) return level;

  // Never infer a risk category from revenue or usage alone.
  return "Unclassified";
}

function getRiskScore(customer) {
  return numberValue(customer.risk_index ?? customer.risk_score);
}

function renderStats(riskCounts) {
  document.getElementById("total-customers").textContent = customers.length;
  document.getElementById("urgent-count").textContent =
    riskCounts?.Urgent ?? "—";
  document.getElementById("review-count").textContent =
    riskCounts?.["Needs Review"] ?? "—";
  document.getElementById("healthy-count").textContent =
    riskCounts?.Healthy ?? "—";
}

function renderCustomers() {
  const query = search.value.trim().toLowerCase();
  const selectedRisk = filter.value;

  const visible = customers.filter(customer => {
    const text = [
      customer.account_id,
      customer.company_name,
      customer.industry,
      customer.region,
      customer.account_owner
    ].join(" ").toLowerCase();

    const matchesSearch = text.includes(query);
    const matchesRisk =
      selectedRisk === "all" || getRiskLevel(customer) === selectedRisk;

    return matchesSearch && matchesRisk;
  });

  message.textContent =
    `Showing ${visible.length} of ${customers.length} customers`;

  if (!visible.length) {
    tbody.innerHTML = `
      <tr><td colspan="6">No matching customers found.</td></tr>
    `;
    return;
  }

  tbody.innerHTML = visible.map(customer => {
    const id = customer.account_id;
    const risk = getRiskLevel(customer);
    const score = getRiskScore(customer);
    const riskClass = risk === "Urgent"
      ? "risk-urgent"
      : risk === "Needs Review"
        ? "risk-review"
        : risk === "Healthy"
          ? "risk-healthy"
          : "risk-unknown";

    return `
      <tr>
        <td>
          <strong>${escapeHTML(customer.company_name)}</strong>
          <div class="customer-subtext">
            ${escapeHTML(customer.industry || "Industry unavailable")}
          </div>
        </td>
        <td>${escapeHTML(id)}</td>
        <td>${score === null ? "—" : escapeHTML(score)}</td>
        <td>
          <span class="risk-label ${riskClass}">
            ${escapeHTML(risk)}
          </span>
        </td>
        <td>${money(customer.recorded_revenue_90d)}</td>
        <td>
          <a class="customer-open-link"
             href="/customers/${encodeURIComponent(id)}">
            View details →
          </a>
        </td>
      </tr>
    `;
  }).join("");
}

async function loadCustomers() {
  message.textContent = "Loading customers…";

  try {
    const response = await fetch("/api/customers");
    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.error || `HTTP ${response.status}`);
    }

    customers = Array.isArray(data.customers) ? data.customers : [];
    renderStats(data.risk_counts || {});
    renderCustomers();
  } catch (error) {
    console.error(error);
    message.textContent = `Could not load customers: ${error.message}`;
    tbody.innerHTML = "";
  }
}

search.addEventListener("input", renderCustomers);
filter.addEventListener("change", renderCustomers);

loadCustomers();
