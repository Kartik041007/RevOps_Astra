
const API = "/api/customers";

const $ = id => document.getElementById(id);
let usageChart = null;
let supportChart = null;

const ACTIONS = {
  R1_USAGE_DECLINE: ["Investigate usage decline", "Confirm the reason for the usage drop with the customer.", "High"],
  R1_USAGE_DROP_MODERATE: ["Investigate usage decline", "Review recent usage and investigate the change.", "High"],
  R2_HIGH_PRIORITY_TICKET: ["Escalate high-priority ticket", "Coordinate with support and confirm the resolution plan.", "Urgent"],
  R3_SUPPORT_BACKLOG: ["Resolve support backlog", "Review open tickets and assign resolution owners.", "High"],
  R4_OVERDUE_FOLLOWUP: ["Complete overdue follow-up", "Contact the customer and record the next steps.", "High"],
  R5_NEGATIVE_INTERACTION: ["Schedule customer success check-in", "Understand the customer's concern and agree on follow-up.", "High"]
};

function esc(value) {
  return String(value ?? "—").replace(/[&<>"']/g, ch => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[ch]);
}

function getValue(obj, keys) {
  for (const key of keys) {
    if (obj && obj[key] !== undefined && obj[key] !== null && obj[key] !== "") {
      return obj[key];
    }
  }
  return null;
}

function num(value) {
  if (value === null || value === undefined || value === "") return null;
  const result = Number(value);
  return Number.isFinite(result) ? result : null;
}

function formatMoney(value) {
  const valueNum = num(value);
  return valueNum === null ? "—" : valueNum.toLocaleString("en-IN", {
    style: "currency", currency: "INR", maximumFractionDigits: 0
  });
}

function formatNumber(value, digits = 1) {
  const valueNum = num(value);
  return valueNum === null ? "—" : valueNum.toLocaleString("en-IN", {
    maximumFractionDigits: digits
  });
}

function isRisk(row) {
  return ["risk", "negative", "neg", "-1"].includes(
    String(row.polarity || "").trim().toLowerCase()
  );
}

function isPositive(row) {
  return ["positive", "pos", "+1"].includes(
    String(row.polarity || "").trim().toLowerCase()
  );
}

function signalName(row) {
  return row.signal_code || row.source_type || "Observed signal";
}

function renderHeader(account, id) {
  $("customer-name").textContent =
    account.company_name || account.account_name || id;

  $("customer-meta").textContent = [
    id,
    account.industry,
    account.region,
    account.account_owner ? `Owner: ${account.account_owner}` : null
  ].filter(Boolean).join(" · ");

  const risk = account.risk_level || account.risk_category || "Unclassified";
  $("customer-risk-badge").textContent = risk;
  $("customer-risk-badge").className = "ci-risk-badge";

  if (risk === "Healthy") {
    $("customer-risk-badge").style.background = "#d9f8e9";
    $("customer-risk-badge").style.color = "#218967";
  } else if (risk === "Needs Review") {
    $("customer-risk-badge").style.background = "#fff0d5";
    $("customer-risk-badge").style.color = "#ad731c";
  }
}

function renderKPIs(account) {
  const risk = getValue(account, ["risk_index", "risk_score"]);
  const revenue = getValue(account, ["recorded_revenue_90d"]);
  const users = getValue(account, ["active_users_avg_30d"]);
  const tickets = getValue(account, ["open_tickets"]);

  const metrics = [
    ["Risk Index", risk == null ? "—" : `${formatNumber(risk, 0)}/100`, "Prototype score, not churn probability"],
    ["90-Day Revenue", formatMoney(revenue), "Recorded revenue, not predicted loss"],
    ["Active Users · 30d", formatNumber(users), "Average active users"],
    ["Open Tickets", formatNumber(tickets, 0), "Recorded open support tickets"]
  ];

  $("account-summary").innerHTML = metrics.map(item => `
    <article class="ci-kpi">
      <span>${esc(item[0])}</span>
      <strong>${esc(item[1])}</strong>
      <small>${esc(item[2])}</small>
    </article>
  `).join("");
}

function renderCRMDetails(account) {
  const panel = $("crm-details-panel");
  const grid = $("crm-details-grid");
  const badge = $("crm-provider-badge");
  if (!panel || !grid) return;

  const crmRecord = account.crm_record || {};
  const provider = (account.crm_provider || "").toLowerCase();
  const providerLabel = provider === "mysql" ? "MySQL CRM" : (provider === "mock" ? "Mock CRM" : "CRM Profile");
  if (badge) {
    badge.textContent = providerLabel;
  }

  const items = [];

  // 1. CRM Status
  const statusVal = crmRecord.customer_status || crmRecord.status || account.customer_status || account.status;
  if (statusVal) {
    items.push(`
      <div class="ci-crm-item">
        <span>Lifecycle Status</span>
        <strong class="ci-crm-badge-val">${esc(statusVal)}</strong>
      </div>
    `);
  }

  // 2. Assigned Owner & Email
  const ownerVal = crmRecord.account_owner || account.account_owner;
  const emailVal = crmRecord.owner_email || account.owner_email;
  if (ownerVal || emailVal) {
    items.push(`
      <div class="ci-crm-item">
        <span>Assigned Owner</span>
        <strong>${esc(ownerVal || "Unassigned")}</strong>
        ${emailVal ? `<small class="ci-muted">${esc(emailVal)}</small>` : ""}
      </div>
    `);
  }

  // 3. Last Contact Date
  const contactDate = crmRecord.last_contact_date || account.last_contact_date;
  if (contactDate) {
    items.push(`
      <div class="ci-crm-item">
        <span>Last Contact Date</span>
        <strong>${esc(contactDate)}</strong>
      </div>
    `);
  }

  // 4. CRM Notes
  const notesVal = crmRecord.notes || account.notes;
  if (notesVal) {
    items.push(`
      <div class="ci-crm-item full-width">
        <span>Account CRM Notes</span>
        <strong>${esc(notesVal)}</strong>
      </div>
    `);
  }

  if (items.length === 0) {
    grid.innerHTML = `<div class="ci-crm-empty full-width"><p>No CRM profile data recorded for this account.</p></div>`;
  } else {
    grid.innerHTML = items.join("");
  }
}

function renderVerifiedCRM(account) {
  const panel = $("verified-crm-panel");
  const grid = $("verified-crm-grid");
  const syncBadge = $("crm-sync-badge");
  if (!panel || !grid) return;

  const verifiedUpdate = account.verified_update || null;
  const hasVerified = Boolean(account.has_verified_mutation && verifiedUpdate);

  if (!hasVerified) {
    if (syncBadge) {
      syncBadge.textContent = "No verified CRM updates yet";
      syncBadge.className = "ci-count unverified";
    }
    grid.innerHTML = `
      <div class="ci-crm-empty full-width">
        <p>No verified CRM updates yet for this account. Proposed outreach actions must be reviewed and approved in the Action Queue, then executed via MCP with read-back verification before execution receipts appear here.</p>
      </div>
    `;
    return;
  }

  if (syncBadge) {
    const provider = (verifiedUpdate.mode || account.crm_provider || "").toLowerCase();
    const providerName = provider === "mysql" ? "MySQL" : "Mock";
    syncBadge.textContent = `✓ Verified via MCP (${providerName})`;
    syncBadge.className = "ci-count verified";
  }

  const items = [];

  // 1. Action ID / Status
  items.push(`
    <div class="ci-crm-item">
      <span>Action ID / Status</span>
      <strong>${esc(verifiedUpdate.action_id || "—")}</strong>
      <small class="ci-muted">Status: <strong style="color: #15803d;">${esc(verifiedUpdate.status || "COMPLETED")}</strong></small>
    </div>
  `);

  // 2. Execution / Verification Timestamp
  items.push(`
    <div class="ci-crm-item">
      <span>Verification Timestamp</span>
      <strong>${esc(verifiedUpdate.executed_at ? String(verifiedUpdate.executed_at).replace("T", " ").slice(0, 19) : "Verified via MCP")}</strong>
      <small class="ci-muted">${esc(verifiedUpdate.integration || "mcp_mysql_crm")} · Read-back verified</small>
    </div>
  `);

  // 3. Audit trail (if available from MySQL audit_logs)
  if (verifiedUpdate.audit_event) {
    const auditEv = verifiedUpdate.audit_event;
    items.push(`
      <div class="ci-crm-item">
        <span>MySQL Audit Record</span>
        <strong>Log #${esc(auditEv.log_id)} · ${esc(auditEv.event_type || "CRM_RECORD_UPDATED")}</strong>
        <small class="ci-muted">Actor: ${esc(auditEv.actor || "MCP_SERVER")}</small>
      </div>
    `);
  }

  // 4. Applied updates details (full width)
  const applied = verifiedUpdate.applied_updates || {};
  const appliedStr = Object.entries(applied)
    .map(([k, v]) => `${k}: "${v}"`)
    .join(", ");
  const message = verifiedUpdate.message || "Executed and read-back verified against CRM via MCP.";

  items.push(`
    <div class="ci-crm-item full-width verified-highlight">
      <span>Verified CRM Mutation Receipt</span>
      <strong>${esc(appliedStr || message)}</strong>
      <small class="ci-muted">${esc(message)}</small>
    </div>
  `);

  grid.innerHTML = items.join("");
}

function renderUsageMetrics(account) {
  const metrics = [
    ["Usage events · 30d", account.usage_events_30d],
    ["Usage change", account.usage_change_pct == null ? null : `${formatNumber(account.usage_change_pct)}%`],
    ["Feature adoption", account.feature_adoption_avg_30d == null ? null : `${formatNumber(account.feature_adoption_avg_30d)}%`],
    ["Error rate", account.error_rate_30d_pct == null ? null : `${formatNumber(account.error_rate_30d_pct)}%`],
    ["Total events · 30d", account.total_events_30d],
    ["Days since usage", account.days_since_last_usage],
    ["Usage data gap", account.usage_data_gap],
    ["Last usage date", account.latest_usage_date]
  ];

  $("usage-metrics").innerHTML = metrics.map(([label, value]) => `
    <div class="ci-usage-metric">
      <span>${esc(label)}</span>
      <strong>${esc(value == null || value === "" ? "—" : value)}</strong>
    </div>
  `).join("");
}

function renderCharts(account) {
  if (typeof Chart === "undefined") {
    document.querySelectorAll(".ci-chart-wrap").forEach(el => {
      el.innerHTML = '<p class="ci-muted">Chart library did not load.</p>';
    });
    return;
  }

  if (usageChart) usageChart.destroy();
  if (supportChart) supportChart.destroy();

  const usageLabels = ["Usage events", "Active users", "Total events", "Error events"];
  const usageValues = [
    num(account.usage_events_30d),
    num(account.active_users_avg_30d),
    num(account.total_events_30d),
    num(account.error_events_30d)
  ];

  const validUsage = usageLabels
    .map((label, i) => ({ label, value: usageValues[i] }))
    .filter(item => item.value !== null);

  if (validUsage.length) {
    usageChart = new Chart($("usage-chart"), {
      type: "bar",
      data: {
        labels: validUsage.map(item => item.label),
        datasets: [{
          label: "Recorded values",
          data: validUsage.map(item => item.value),
          backgroundColor: ["#7c91f7", "#65b8c7", "#9a83ed", "#f18b9a"],
          borderRadius: 7
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { ticks: { color: "#71809b" }, grid: { display: false } },
          y: { beginAtZero: true, ticks: { color: "#71809b" } }
        }
      }
    });
  } else {
    $("usage-chart").parentElement.innerHTML =
      '<p class="ci-muted">No usage metrics are available.</p>';
  }

  const supportLabels = ["Open tickets", "High-priority open", "Pending follow-ups", "Overdue follow-ups"];
  const supportValues = [
    num(account.open_tickets),
    num(account.high_priority_open_tickets),
    num(account.pending_followups),
    num(account.overdue_followups)
  ];

  const validSupport = supportLabels
    .map((label, i) => ({ label, value: supportValues[i] }))
    .filter(item => item.value !== null);

  if (validSupport.length) {
    supportChart = new Chart($("support-chart"), {
      type: "bar",
      data: {
        labels: validSupport.map(item => item.label),
        datasets: [{
          label: "Recorded count",
          data: validSupport.map(item => item.value),
          backgroundColor: "#f1a46f",
          borderRadius: 7
        }]
      },
      options: {
        indexAxis: "y",
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { beginAtZero: true, ticks: { precision: 0, color: "#71809b" } },
          y: { ticks: { color: "#71809b" }, grid: { display: false } }
        }
      }
    });
  } else {
    $("support-chart").parentElement.innerHTML =
      '<p class="ci-muted">No support metrics are available.</p>';
  }
}

function renderEvidence(evidence) {
  const riskRows = evidence.filter(isRisk);

  $("evidence-count").textContent = `${riskRows.length} risk records`;

  if (!riskRows.length) {
    $("risk-pathway").innerHTML =
      '<p class="ci-muted">No risk-polarity evidence records were returned.</p>';
    return;
  }

  $("risk-pathway").innerHTML = riskRows.map((row, index) => `
    <article class="ci-evidence-item">
      <span class="ci-evidence-marker">${index + 1}</span>
      <div>
        <strong>${esc(signalName(row))}</strong>
        <p>${esc(row.evidence_detail || "No evidence description supplied.")}</p>
        <small>
          Source: ${esc(row.source_type || "Not specified")}
          · Record: ${esc(row.source_record_id || "Not supplied")}
          · Date: ${esc(row.source_date || "Not supplied")}
        </small>
      </div>
    </article>
  `).join("");
}

function renderActions(evidence) {
  const found = new Map();

  evidence.filter(isRisk).forEach(row => {
    const code = String(row.signal_code || "").trim().toUpperCase();
    const rule = ACTIONS[code];

    if (rule && !found.has(code)) {
      found.set(code, { code, rule });
    }
  });

  $("action-count").textContent = `${found.size} suggestions`;

  if (!found.size) {
    $("action-board").innerHTML =
      '<p class="ci-muted">No predefined actions matched the returned risk signals. Review the evidence manually.</p>';
    return;
  }

  $("action-board").innerHTML = [...found.values()].map(({ code, rule }) => `
    <article class="ci-action-item">
      <span class="ci-evidence-marker">↗</span>
      <div>
        <strong>${esc(rule[0])}</strong>
        <p>${esc(rule[1])}</p>
        <small class="ci-muted">Evidence: ${esc(code)} · Suggested, not saved</small>
      </div>
      <span class="ci-action-priority">${esc(rule[2])}</span>
    </article>
  `).join("");
}

function renderImpact(account, evidence) {
  const positive = evidence.filter(isPositive);
  const negative = evidence.filter(isRisk);
  const positiveItems = [];

  if (account.recorded_revenue_90d != null) {
    positiveItems.push(`Recorded 90-day revenue: ${formatMoney(account.recorded_revenue_90d)}`);
  }

  if (num(account.expansion_intents_30d) > 0) {
    positiveItems.push(`Recorded expansion intents: ${account.expansion_intents_30d}`);
  }

  positive.forEach(row => {
    positiveItems.push(`${signalName(row)}: ${row.evidence_detail || "Positive evidence recorded"}`);
  });

  $("positive-impact").innerHTML = positiveItems.length
    ? positiveItems.map(item => `<p class="ci-impact-item">${esc(item)}</p>`).join("")
    : '<p class="ci-impact-item">No positive indicators were available in the returned data.</p>';

  $("negative-impact").innerHTML = negative.length
    ? negative.map(row => `
        <p class="ci-impact-item">
          <strong>${esc(signalName(row))}</strong>:
          ${esc(row.evidence_detail || "Risk signal recorded")}
        </p>
      `).join("")
    : '<p class="ci-impact-item">No negative-polarity evidence was returned.</p>';
}

async function loadCustomer() {
  const parts = window.location.pathname.split("/").filter(Boolean);
  const customerId = parts[0] === "customers" ? parts[1] : null;

  if (!customerId || customerId === "customers") {
    $("ci-error").textContent = "Customer ID is missing from the URL.";
    $("ci-error").hidden = false;
    return;
  }

  try {
    const response = await fetch(
      `${API}/${encodeURIComponent(customerId)}/impact`
    );

    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.error || `API request failed (${response.status})`);
    }

    const account = data.account || {};
    const evidence = Array.isArray(data.evidence) ? data.evidence : [];

    renderHeader(account, customerId);
    renderKPIs(account);
    renderCRMDetails(account);
    renderVerifiedCRM(account);
    renderUsageMetrics(account);
    renderCharts(account);
    renderEvidence(evidence);
    renderActions(evidence);
    renderImpact(account, evidence);
  } catch (error) {
    console.error("Customer dashboard error:", error);
    $("ci-error").textContent = `Unable to load customer: ${error.message}`;
    $("ci-error").hidden = false;
  }
}

loadCustomer();
