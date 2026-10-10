const $ = (id) => document.getElementById(id);

let riskChart = null;
let healthChart = null;
let allAccounts = [];

/* ---- Theme colors (match style.css) ---- */
const COLORS = {
    Urgent: "#ff6b7d",
    High: "#ffb84d",
    "Needs Review": "#b58cff",
    Healthy: "#4eeaa8",
    Unknown: "#93a0bd"
};
const FALLBACK_COLORS = ["#6f86ff", "#4cc9ff", "#b58cff", "#4eeaa8", "#ffb84d", "#ff6b7d"];

const isLight = () =>
    window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches;
const gridColor = () => (isLight() ? "rgba(20,30,70,.08)" : "rgba(255,255,255,.08)");

const number = (value) =>
    Number(value || 0).toLocaleString("en-IN", { maximumFractionDigits: 2 });

const money = (value) => {
    const amount = Number(value || 0);
    return amount.toLocaleString("en-IN", {
        style: "currency",
        currency: "INR",
        maximumFractionDigits: 0
    });
};

function showToast(message) {
    const toast = $("toast");
    toast.textContent = message;
    toast.classList.add("show");
    setTimeout(() => toast.classList.remove("show"), 3000);
}

function setConnection(connected, message) {
    $("connection-status").textContent = message;
    const dot = document.querySelector(".sidebar-bottom .status-dot");
    const color = connected ? "#4eeaa8" : "#ff6b7d";
    dot.style.background = color;
    dot.style.boxShadow = `0 0 10px ${color}`;
}

/* Changes only the text of the refresh button, so its icon is kept */
function setRefreshLabel(text) {
    const button = $("refresh-btn");
    const last = button.lastChild;
    if (last && last.nodeType === Node.TEXT_NODE) {
        last.textContent = ` ${text}`;
    } else {
        button.append(` ${text}`);
    }
}

async function getJSON(url) {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) {
        throw new Error(`${url} returned HTTP ${response.status}`);
    }
    return response.json();
}

function renderKPIs(data) {
    const kpis = data.kpis || data.summary || data;

    $("total-customers").textContent =
        number(kpis.total_customers ?? kpis.total_accounts);

    $("urgent-customers").textContent =
        number(kpis.urgent_customers ?? kpis.urgent_accounts);

    $("review-customers").textContent =
        number(kpis.customers_needing_review ?? kpis.accounts_needing_review);

    $("healthy-customers").textContent =
        number(kpis.healthy_customers ?? kpis.healthy_accounts);

    const revenue =
        kpis.recorded_revenue_90d_associated_with_flagged_accounts ??
        kpis.recorded_revenue_90d ??
        kpis.recorded_revenue;

    $("recorded-revenue").textContent =
        revenue == null ? "—" : money(revenue);
}

function normalizeLevel(value) {
    const text = String(value || "Unknown").toLowerCase();

    if (text.includes("urgent") || text.includes("critical")) return "Urgent";
    if (text.includes("high")) return "High";
    if (text.includes("review") || text.includes("medium")) return "Needs Review";
    if (text.includes("healthy") || text.includes("low")) return "Healthy";
    return String(value || "Unknown");
}

function getAccounts(data) {
    if (Array.isArray(data.priority_accounts)) return data.priority_accounts;
    if (Array.isArray(data.risk_queue)) return data.risk_queue;
    if (Array.isArray(data.accounts)) return data.accounts;
    return [];
}

function renderRiskQueue(accounts) {
    allAccounts = accounts;
    applySearch();
}

function applySearch() {
    const query = $("search-input").value.trim().toLowerCase();

    const filtered = allAccounts.filter((account) => {
        const text = [
            account.company_name,
            account.account_name,
            account.account_id,
            account.risk_level,
            account.risk_driver_codes
        ].join(" ").toLowerCase();

        return text.includes(query);
    });

    const tbody = $("risk-table-body");
    tbody.replaceChildren();

    if (!filtered.length) {
        const row = tbody.insertRow();
        const cell = row.insertCell();
        cell.colSpan = 6;
        cell.className = "empty-message";
        cell.textContent = allAccounts.length
            ? "No accounts match your search."
            : "No priority accounts found in the API response.";
        $("table-count").textContent = `Showing 0 of ${allAccounts.length} accounts`;
        return;
    }

    filtered.forEach((account) => {
        const row = tbody.insertRow();
        const company = account.company_name || account.account_name || "Unnamed account";
        const id = account.account_id || account.customer_id || "—";
        const level = normalizeLevel(account.risk_level || account.risk_status);
        const score = Number(account.risk_index ?? account.risk_score ?? 0);
        const revenue = account.recorded_revenue_90d ?? account.revenue_90d;
        const tickets = account.open_tickets ?? "—";
        const drivers = account.risk_driver_codes || account.risk_drivers || "No evidence listed";

        const accountCell = row.insertCell();
        const name = document.createElement("div");
        name.className = "account-name";
        name.textContent = company;
        const accountId = document.createElement("div");
        accountId.className = "account-id";
        accountId.textContent = id;
        accountCell.append(name, accountId);

        const levelCell = row.insertCell();
        const pill = document.createElement("span");
        pill.className = "risk-pill " + (
            level === "Urgent" ? "risk-urgent" :
                level === "High" ? "risk-high" :
                    level === "Needs Review" ? "risk-medium" :
                        level === "Healthy" ? "risk-healthy" : "risk-other"
        );
        pill.textContent = level;
        levelCell.appendChild(pill);

        const scoreCell = row.insertCell();
        const scoreLabel = document.createElement("div");
        scoreLabel.className = "score";
        scoreLabel.textContent = number(score);
        const track = document.createElement("div");
        track.className = "score-track";
        const fill = document.createElement("div");
        fill.className = "score-fill";
        fill.style.width = `${Math.max(0, Math.min(100, score))}%`;
        track.appendChild(fill);
        scoreCell.append(scoreLabel, track);

        row.insertCell().textContent = revenue == null ? "—" : money(revenue);
        row.insertCell().textContent = tickets;
        const driverCell = row.insertCell();
        driverCell.className = "drivers";
        driverCell.textContent = String(drivers).replaceAll("; ", " · ");
    });

    $("table-count").textContent =
        `Showing ${filtered.length} of ${allAccounts.length} priority accounts`;
}

function countLevels(accounts) {
    const counts = {};
    accounts.forEach((account) => {
        const level = normalizeLevel(account.risk_level || account.risk_status);
        counts[level] = (counts[level] || 0) + 1;
    });
    return counts;
}

function chartBaseOptions() {
    return {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
            legend: {
                position: "bottom",
                labels: {
                    usePointStyle: true,
                    pointStyle: "circle",
                    padding: 18,
                    font: { family: "Inter", size: 11 }
                }
            }
        }
    };
}

function showChartEmpty(canvasId, emptyId, empty) {
    $(canvasId).hidden = empty;
    $(emptyId).hidden = !empty;
}

/* Vertical gradient fill for the bars */
function barGradient(ctx, chartArea, color) {
    if (!chartArea) return color;
    const g = ctx.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
    g.addColorStop(0, color);
    g.addColorStop(1, color + "33");
    return g;
}

function renderCharts(summaryData, chartsData) {
    const kpis = summaryData.kpis || {};
    const riskRows = chartsData.risk_distribution ||
        chartsData.riskDistribution ||
        chartsData.risk_levels;

    let labels = [];
    let values = [];

    if (Array.isArray(riskRows) && riskRows.length) {
        labels = riskRows.map((row) => row.risk_level || row.label || row.name || "Unknown");
        values = riskRows.map((row) => Number(row.customer_count ?? row.account_count ?? row.count ?? row.value ?? 0));
    } else {
        const counts = countLevels(getAccounts(summaryData));
        labels = Object.keys(counts);
        values = Object.values(counts);
    }

    showChartEmpty("risk-chart", "risk-empty", !labels.length);

    if (labels.length && window.Chart) {
        if (riskChart) riskChart.destroy();

        // colour each slice by its risk level instead of by position
        const sliceColors = labels.map(
            (label, i) => COLORS[normalizeLevel(label)] || FALLBACK_COLORS[i % FALLBACK_COLORS.length]
        );
        const total = values.reduce((a, b) => a + b, 0);

        riskChart = new Chart($("risk-chart"), {
            type: "doughnut",
            data: {
                labels,
                datasets: [{
                    data: values,
                    backgroundColor: sliceColors,
                    borderWidth: 0,
                    borderRadius: 8,
                    spacing: 3,
                    hoverOffset: 8
                }]
            },
            options: {
                ...chartBaseOptions(),
                cutout: "72%",
                plugins: {
                    ...chartBaseOptions().plugins,
                    tooltip: {
                        callbacks: {
                            label: (ctx) => {
                                const pct = total ? ((ctx.raw / total) * 100).toFixed(0) : 0;
                                return ` ${ctx.label}: ${ctx.raw} accounts (${pct}%)`;
                            }
                        }
                    }
                }
            }
        });
    }

    const healthLabels = ["Urgent", "Needs Review", "Healthy"];
    const healthValues = [
        Number(kpis.urgent_customers || 0),
        Number(kpis.customers_needing_review || 0),
        Number(kpis.healthy_customers || 0)
    ];
    const healthColors = [COLORS.Urgent, COLORS.High, COLORS.Healthy];
    const hasHealthData = healthValues.some((value) => value > 0);

    showChartEmpty("health-chart", "health-empty", !hasHealthData);

    if (hasHealthData && window.Chart) {
        if (healthChart) healthChart.destroy();
        healthChart = new Chart($("health-chart"), {
            type: "bar",
            data: {
                labels: healthLabels,
                datasets: [{
                    label: "Accounts",
                    data: healthValues,
                    backgroundColor: (context) =>
                        barGradient(context.chart.ctx, context.chart.chartArea, healthColors[context.dataIndex]),
                    borderColor: healthColors,
                    borderWidth: { top: 2, right: 0, bottom: 0, left: 0 },
                    borderRadius: 12,
                    borderSkipped: false,
                    maxBarThickness: 56
                }]
            },
            options: {
                ...chartBaseOptions(),
                scales: {
                    y: {
                        beginAtZero: true,
                        ticks: { precision: 0 },
                        grid: { color: gridColor() },
                        border: { display: false }
                    },
                    x: { grid: { display: false }, border: { display: false } }
                },
                plugins: { legend: { display: false } }
            }
        });
    }
}

async function loadDashboard() {
    const button = $("refresh-btn");
    button.disabled = true;
    setRefreshLabel("Loading...");

    try {
        const [summaryData, chartsData] = await Promise.all([
            getJSON("/api/summary"),
            getJSON("/api/charts")
        ]);

        renderKPIs(summaryData);
        renderRiskQueue(getAccounts(summaryData));
        renderCharts(summaryData, chartsData);

        setConnection(true, "Connected to Flask API");
        $("last-updated").textContent =
            "Updated " + new Date().toLocaleTimeString("en-IN");
    } catch (error) {
        console.error("Dashboard loading failed:", error);
        setConnection(false, "API connection error");
        showToast("Could not load dashboard data. Check Flask terminal and API routes.");
    } finally {
        button.disabled = false;
        setRefreshLabel("Refresh data");
    }
}

$("refresh-btn").addEventListener("click", loadDashboard);
$("search-input").addEventListener("input", applySearch);

loadDashboard();