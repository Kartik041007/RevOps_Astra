# RevOps Astra — AI-Powered Revenue Operations

RevOps Astra is a revenue-operations workspace designed to help teams understand customer health, identify accounts that may need attention, review supporting evidence, and prioritize follow-up actions. The project combines a Flask-based dashboard and admin authentication with AI-assisted analysis and agent workflow modules.

> **Project status:** This repository is a development/hackathon prototype. Validate the merged setup locally before using it with real customer data. Some integrations may require additional configuration, and the AI chat requires the local model service described below.

## Project Highlights

### Dashboard and Web Application
- Flask application with dashboard routes and HTML templates.
- Admin registration and login pages, session handling, and logout support.
- Customer directory and customer-impact views.
- Dashboard summary, charts, risk queue, and account-level insights.
- ASTRA AI robot page and chat-widget UI.
- JSON/CSV outputs for dashboard metrics and account analysis.

### AI Agent and Analytics
- **LLM service:** `ai/llm_service.py` — model-backed analysis and draft-generation helpers.
- **Agent workflow:** `ai/agent_workflow.py` — coordinates agent workflow and proposed actions.
- **Risk engine:** `ai/risk_engine.py` — account-risk scoring logic.
- **Evidence builder:** `ai/evidence_builder.py` — builds supporting evidence for account insights.
- **Feature engineering:** `ai/feature_engineering.py` — prepares account features.
- **Priority queue:** `ai/priority_queue.py` — prioritizes accounts and follow-up work.
- **Revenue insights:** `ai/revenue_insights.py` — revenue-oriented insight helpers.
- **Dashboard data:** `ai/dashboard_data.py` and `ai/data_loader.py` — prepare/load data for analysis.

### Data and Outputs
The project includes example datasets and generated artifacts used by the dashboard and agent modules.

**Data files** in `data/` include:
- `accounts.csv`
- `contacts.csv`
- `interactions.csv`
- `followups.csv`
- `products.csv`
- `product_usage_snapshots.csv`
- `sales_transactions.csv`
- `support_tickets.csv`
- `demo_scenarios.csv`

**Generated outputs** in `outputs/` include:
- Account features and evidence
- Account risk insights
- Priority queue and customer priority list
- Dashboard summary and chart data
- Proposed actions and tasks
- LLM briefs and audit-log records

Output files may be generated from demo or synthetic data. Review their source and freshness before relying on them.

## Technology Stack

- Python
- Flask
- HTML, CSS, and JavaScript
- MySQL for the admin-authentication setup (configure credentials locally)
- CSV and JSON for project datasets and generated outputs
- Ollama with the `qwen2.5:1.5b` model for the local LLM service, as configured in the agent code

## Repository Structure

```text
RevOps_Astra/
├── app.py
├── routes/
│   └── dashboard.py
├── ai/
│   ├── agent_workflow.py
│   ├── chat_service.py
│   ├── dashboard_data.py
│   ├── data_loader.py
│   ├── evidence_builder.py
│   ├── feature_engineering.py
│   ├── llm_service.py
│   ├── priority_queue.py
│   ├── revenue_insights.py
│   ├── risk_engine.py
│   └── validate_analytics.py
├── templates/
│   ├── index.html
│   ├── login.html
│   ├── register.html
│   ├── customers.html
│   ├── customer_impact.html
│   ├── ai_agent_3d.html
│   └── agent_widget.html
├── static/
│   ├── css/
│   └── js/
├── data/
├── outputs/
├── requirements.txt
└── README.md
```

The exact files can vary between branches as the project evolves.

## Getting Started

### 1. Prerequisites

Install:
- Python 3.10 or newer (use the version supported by the project's dependencies).
- Git.
- MySQL, if using the MySQL-backed admin-authentication configuration.
- Ollama, if using the local LLM integration.

### 2. Clone the Repository

```bash
git clone <YOUR_REPOSITORY_URL>
cd RevOps_Astra
```

Replace `<YOUR_REPOSITORY_URL>` with the URL of your GitHub repository.

### 3. Create and Activate a Virtual Environment

**macOS / Linux**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

**Windows PowerShell**
```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
```

### 4. Install Dependencies

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 5. Configure Local Settings

Review `app.py`, `routes/dashboard.py`, and the AI modules for required configuration. Set your own local database credentials and Flask secret using environment variables where supported. Do not commit database passwords, secret keys, tokens, or `.env` files.

If the current code expects a local MySQL database named `RevOps_Agent` and an `admins` table, create the database/table using your project’s SQL setup and update the connection settings to match your local environment.

### 6. Set Up the Local LLM (Optional, for AI responses)

If `ai/llm_service.py` is configured to use Ollama and `qwen2.5:1.5b`, install/start Ollama and pull the model:

```bash
ollama pull qwen2.5:1.5b
```

Ensure Ollama is running before testing AI requests. The dashboard may load without the local model, but LLM-backed features will not work until the model service is available.

### 7. Run the Flask Application

From the project root:

```bash
python app.py
```

Open the local URL printed by Flask, commonly `http://127.0.0.1:5000`.

If the application fails to start, check the terminal traceback, dependency versions, database connection settings, and whether the configured port is already in use.

## Agent Integration

The intended request flow is:

1. A user opens ASTRA AI from the dashboard.
2. The chat UI sends a message to the Flask backend.
3. The backend passes the request to the agent/chat service.
4. The agent uses available account data and configured LLM service to prepare a response.
5. The backend returns the response to the chat UI.

The presence of an agent module or chat endpoint does not by itself guarantee the entire flow is configured and running. Test the endpoint and local model in your environment before presenting the feature as live.

## Data and Metric Notes

- Verify dataset provenance before using the included CSV or JSON data.
- Treat risk scores as the output of the implemented project logic, not as calibrated churn probabilities unless separately validated.
- Historical recorded revenue should not automatically be described as predicted revenue loss.
- AI-generated recommendations should be reviewed against their supporting account evidence.
- Keep human review/approval for consequential actions such as sending emails or changing CRM records.

## Security Notes

- Never commit API keys, MySQL passwords, production credentials, session secrets, or private customer data.
- Use environment variables or a local ignored `.env` file for secrets where the application supports them.
- Do not expose debug mode or development-only credentials in production.
- Restrict admin registration and protect authenticated routes before deploying publicly.
- Use fictional/demo data for public hackathon demonstrations unless data access and privacy requirements have been reviewed.

## Team Contributions

This project brings together two areas of work:

- **Dashboard and web application:** Flask dashboard, admin authentication pages, customer views, frontend styling, and ASTRA AI UI integration.
- **AI agent and analytics:** LLM service, agent workflow, risk and evidence modules, priority queue, revenue insights, datasets, and generated analysis outputs.

Update this section with each contributor's preferred name, GitHub username, and specific contribution before publishing.

## Troubleshooting

- **Database connection error:** Confirm MySQL is running, the database/table exist, and the configured credentials are correct.
- **`url_for` endpoint error:** Check the route function name and blueprint name in `routes/dashboard.py` against the endpoint referenced by the template.
- **AI response error:** Confirm the Flask chat endpoint is registered, the request/response fields match the frontend, and the agent service can be imported.
- **Ollama/model error:** Start Ollama and verify that `qwen2.5:1.5b` is installed.
- **Template not found:** Confirm the HTML file is inside `templates/` and the route uses the correct filename.
- **Static styles not updating:** Save the CSS and hard-refresh the browser.

## License

Add a license file before redistributing this project. Until a license is added, do not assume the repository grants permission for reuse.
