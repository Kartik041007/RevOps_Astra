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
    const levels = kpis.accounts_by_risk_level || {};

    const urgent = Number(
        kpis.urgent_customers ?? kpis.urgent_accounts ?? levels.Urgent ?? 0
    );

    const review = Number(
        kpis.customers_needing_review ??
        kpis.accounts_needing_review ??
        levels["Needs Review"] ?? 0
    );

    const healthy = Number(
        kpis.healthy_customers ?? kpis.healthy_accounts ?? levels.Healthy ?? 0
    );

    $("total-customers").textContent = number(
        kpis.total_customers ?? kpis.total_accounts ?? 0
    );

    $("urgent-customers").textContent = number(urgent);
    $("review-customers").textContent = number(review);
    $("healthy-customers").textContent = number(healthy);

    const revenue =
        kpis.recorded_revenue_90d_associated_with_flagged_accounts ??
        kpis.recorded_revenue_90d ??
        kpis.recorded_revenue;

    $("recorded-revenue").textContent =
        revenue == null ? "—" : money(revenue);
}




function renderPortfolioSnapshot(data) {
    const kpis = data.kpis || data.summary || data;
    const levels = kpis.accounts_by_risk_level || {};

    const urgent = Number(
        kpis.urgent_customers ?? kpis.urgent_accounts ?? levels.Urgent ?? 0
    );

    const review = Number(
        kpis.customers_needing_review ??
        kpis.accounts_needing_review ??
        levels["Needs Review"] ?? 0
    );

    const healthy = Number(
        kpis.healthy_customers ?? kpis.healthy_accounts ?? levels.Healthy ?? 0
    );

    const total = Number(
        kpis.total_customers ?? kpis.total_accounts ??
        (urgent + review + healthy)
    );

    const setText = (id, value) => {
        const el = document.getElementById(id);
        if (el) el.textContent = value;
    };

    setText("ring-value", number(healthy));

    setText(
        "snapshot-text",
        `${number(total)} accounts classified · ${number(urgent)} urgent · ${number(review)} need review · ${number(healthy)} healthy`
    );

    const setWidth = (id, count) => {
        const el = document.getElementById(id);
        if (el) {
            el.style.width = `${total > 0 ? (count / total) * 100 : 0}%`;
        }
    };

    setWidth("seg-urgent", urgent);
    setWidth("seg-review", review);
    setWidth("seg-healthy", healthy);

    const ring = document.querySelector(".ring");
    if (ring) {
        ring.style.setProperty(
            "--healthy-share",
            `${total > 0 ? (healthy / total) * 100 : 0}%`
        );
    }
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
    if (Array.isArray(data.top_priority_accounts)) return data.top_priority_accounts;
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

/* Replays the chart's pop-up animation when it scrolls into view */
let replayObserver = null;
function replayOnView(chart, canvas) {
    if (!window.IntersectionObserver) return;
    if (replayObserver) replayObserver.disconnect();
    let first = true;
    replayObserver = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
            if (!entry.isIntersecting) return;
            if (first) { first = false; }   // skip the initial callback if it's already animating
            chart.reset();
            chart.update();
        });
    }, { threshold: 0.45 });
    replayObserver.observe(canvas);
}


function renderPortfolioSnapshot(data) {
    const kpis = data.kpis || data.summary || data;
    const levels = kpis.accounts_by_risk_level || {};

    const urgent = Number(
        kpis.urgent_customers ?? kpis.urgent_accounts ?? levels.Urgent ?? 0
    );
    const review = Number(
        kpis.customers_needing_review ??
        kpis.accounts_needing_review ??
        levels["Needs Review"] ?? 0
    );
    const healthy = Number(
        kpis.healthy_customers ?? kpis.healthy_accounts ?? levels.Healthy ?? 0
    );
    const total = Number(
        kpis.total_customers ?? kpis.total_accounts ??
        urgent + review + healthy
    );

    const setText = (id, value) => {
        const el = document.getElementById(id);
        if (el) el.textContent = value;
    };

    setText("ring-value", number(healthy));
    setText(
        "snapshot-text",
        `${number(total)} accounts classified · ${number(urgent)} urgent · ${number(review)} need review · ${number(healthy)} healthy`
    );

    const setWidth = (id, count) => {
        const el = document.getElementById(id);
        if (el) el.style.width = `${total > 0 ? count / total * 100 : 0}%`;
    };

    setWidth("seg-urgent", urgent);
    setWidth("seg-review", review);
    setWidth("seg-healthy", healthy);

    const ring = document.querySelector(".ring");
    if (ring) {
        ring.style.setProperty(
            "--healthy-share",
            `${total > 0 ? healthy / total * 100 : 0}%`
        );
    }

    setText("legend-urgent", number(urgent));
    setText("legend-review", number(review));
    setText("legend-healthy", number(healthy));
}



function renderCharts(summaryData, chartsData) {
    const kpis = summaryData.kpis || summaryData.summary || summaryData;
    let labels = [];
    let values = [];
    const riskRows =
    chartsData.risk_distribution ||
    chartsData.revenue_by_risk_level ||
    chartsData.riskDistribution ||
    chartsData.risk_levels ||
    [];

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

        // legend shows the count next to each name
        const legendLabels = labels.map((label, i) => `${label} · ${values[i]}`);
        const gridLine = gridColor();

        riskChart = new Chart($("risk-chart"), {
            type: "polarArea",
            data: {
                labels: legendLabels,
                datasets: [{
                    data: values,
                    backgroundColor: sliceColors.map((c) => c + "cc"),
                    borderColor: sliceColors,
                    borderWidth: 2,
                    hoverBackgroundColor: sliceColors,
                    hoverBorderWidth: 3,
                    hoverOffset: 10
                }]
            },
            options: {
                ...chartBaseOptions(),
                // pop-up animation: each slice grows from the centre with a bounce, one after another
                animation: {
                    animateRotate: true,
                    animateScale: true,
                    duration: 1300,
                    easing: "easeOutBack",
                    delay: (ctx) =>
                        ctx.type === "data" && ctx.mode === "default" ? ctx.dataIndex * 160 : 0
                },
                scales: {
                    r: {
                        beginAtZero: true,
                        ticks: { display: false, backdropColor: "transparent" },
                        grid: { color: gridLine },
                        angleLines: { color: gridLine },
                        border: { display: false }
                    }
                },
                plugins: {
                    ...chartBaseOptions().plugins,
                    tooltip: {
                        callbacks: {
                            label: (ctx) => {
                                const pct = total ? ((ctx.raw / total) * 100).toFixed(0) : 0;
                                return ` ${labels[ctx.dataIndex]}: ${ctx.raw} accounts (${pct}%)`;
                            }
                        }
                    }
                }
            }
        });
        replayOnView(riskChart, $("risk-chart"));
    }

    
const revenueRows = summaryData.revenue_by_risk_level || [];

const healthLabels = ["Urgent", "Needs Review", "Healthy"];

const revenueValues = healthLabels.map((level) => {
    const row = revenueRows.find((item) => item.risk_level === level);
    return Number(row?.recorded_revenue_90d ?? 0);
});

const accountValues = healthLabels.map((level) => {
    const row = revenueRows.find((item) => item.risk_level === level);
    return Number(
        row?.account_count ??
        summaryData.accounts_by_risk_level?.[level] ??
        0
    );
});

const hasHealthData = revenueValues.some(v => v > 0) ||
    accountValues.some(v => v > 0);

showChartEmpty("health-chart", "health-empty", !hasHealthData);

if (hasHealthData && window.Chart) {
    if (healthChart) healthChart.destroy();

    healthChart = new Chart($("health-chart"), {
        type: "bar",
        data: {
            labels: healthLabels,
            datasets: [
                {
                    type: "bar",
                    label: "Recorded Revenue (90d)",
                    data: revenueValues,
                    backgroundColor: "#3978ffcc",
                    borderColor: "#3978ff",
                    borderRadius: 8,
                    yAxisID: "yRevenue"
                },
                {
                    type: "line",
                    label: "Account Count",
                    data: accountValues,
                    borderColor: "#ff5268",
                    backgroundColor: "#ff5268",
                    pointRadius: 5,
                    tension: 0.3,
                    yAxisID: "yAccounts"
                }
            ]
        },
        options: {
            ...chartBaseOptions(),
            interaction: { mode: "index", intersect: false },
            scales: {
                yRevenue: {
                    type: "linear",
                    position: "left",
                    beginAtZero: true,
                    title: { display: true, text: "Revenue (₹)" },
                    ticks: {
                        callback: value =>
                            "₹" + Number(value).toLocaleString("en-IN")
                    },
                    grid: { color: gridColor() }
                },
                yAccounts: {
                    type: "linear",
                    position: "right",
                    beginAtZero: true,
                    max: 60,
                    title: { display: true, text: "Accounts" },
                    ticks: { stepSize: 10, precision: 0 },
                    grid: { drawOnChartArea: false }
                },
                x: { grid: { display: false } }
            },
            plugins: {
                ...chartBaseOptions().plugins,
                tooltip: {
                    callbacks: {
                        label: context =>
                            context.dataset.yAxisID === "yRevenue"
                                ? " Revenue: " + money(context.raw)
                                : " Accounts: " + context.raw
                    }
                }
            }
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
        renderPortfolioSnapshot(summaryData);
        console.log("SUMMARY DATA:", summaryData);
        console.log("CHARTS DATA:", chartsData);
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