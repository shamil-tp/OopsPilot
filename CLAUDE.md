# OPSPILOT — AI INCIDENT RESPONSE & DEVOPS COPILOT

You are the lead software architect and senior full-stack/AI engineer responsible for building a complete, working hackathon MVP called **OpsPilot**.

Do not create a toy demo or a collection of disconnected examples. Build a coherent, runnable, production-style MVP that can be demonstrated end-to-end.

The project is for a student hackathon with a 4-person team.

The problem domain is:

> **DevOps Copilot: The AI Incident Response Agent**

The system must investigate a simulated software incident, gather evidence from multiple sources, identify a likely root cause, recommend a safe remediation, request human approval before risky actions, execute the approved simulated remediation, verify recovery, and generate an incident report.

The MVP will use **Google Gemini API**.

A later/final version will support **Ollama + Qwen**, so the AI provider architecture MUST be designed so Gemini can later be replaced without rewriting the agent system.

---

# 1. PRIMARY OBJECTIVE

Build a complete system demonstrating:

```text
Incident Detection
        ↓
Incident Investigation
        ↓
Evidence Collection
        ↓
Root Cause Analysis
        ↓
Recommended Remediation
        ↓
Human Approval
        ↓
Safe Remediation
        ↓
Recovery Verification
        ↓
Incident Report
```

The system should clearly demonstrate that this is an **AI agent system**, not merely a chatbot.

The AI must be able to:

1. Understand an incident.
2. Decide what information it needs.
3. Call tools to retrieve information.
4. Analyze evidence from multiple sources.
5. Correlate logs, health metrics, deployments, and previous incidents.
6. Identify the most likely root cause.
7. Explain its reasoning using evidence.
8. Recommend an action.
9. Detect when human approval is required.
10. Wait for approval.
11. Execute a simulated remediation after approval.
12. Verify whether the service recovered.
13. Produce a structured incident report.

Do not expose hidden chain-of-thought. Store and display only concise, user-safe reasoning summaries and evidence.

---

# 2. TECHNOLOGY STACK

Use the following stack unless there is a strong technical reason to change something.

## Frontend

* Next.js
* React
* TypeScript
* Tailwind CSS
* Framer Motion
* Native WebSocket client or a small WebSocket utility
* Recharts or another lightweight charting library if charts are needed

## Backend

* Python
* FastAPI
* Pydantic
* SQLAlchemy
* Alembic
* PostgreSQL
* Uvicorn

## AI

MVP:

* Google Gemini API
* Gemini model selected for good tool/function calling and structured output

Future:

* Ollama
* Qwen

Create an AI provider abstraction:

```text
AIProvider
├── GeminiProvider
└── OllamaProvider
```

The current implementation must use Gemini.

Do NOT couple business logic directly to Gemini.

## Real-time communication

Use BOTH:

### REST API

For:

* creating incidents
* retrieving incidents
* approving actions
* rejecting actions
* retrieving logs
* retrieving reports
* service information
* historical incidents

### WebSocket

For:

* live incident investigation events
* agent status
* tool execution events
* remediation progress
* verification progress
* real-time dashboard updates

Do not use WebSockets for everything.

Use REST for persistent state and commands.

Use WebSocket for live event streaming.

## DevOps

* Docker
* Docker Compose
* GitHub Actions
* GitHub Webhooks
* health checks
* environment variables
* structured logging

---

# 3. PROJECT NAME

Use:

# OpsPilot

Subtitle:

> AI-Powered Autonomous Incident Response & DevOps Copilot

Use this branding consistently in the frontend and documentation.

---

# 4. HIGH-LEVEL ARCHITECTURE

Implement this architecture:

```text
                         ┌─────────────────────┐
                         │      Next.js        │
                         │     Dashboard       │
                         └──────────┬──────────┘
                                    │
                         REST + WebSocket
                                    │
                         ┌──────────▼──────────┐
                         │       FastAPI       │
                         │       Backend       │
                         └──────────┬──────────┘
                                    │
                    ┌───────────────▼───────────────┐
                    │       Agent Orchestrator      │
                    └───────────────┬───────────────┘
                                    │
                         ┌──────────▼──────────┐
                         │    AI Provider      │
                         │                     │
                         │ Gemini MVP          │
                         │ Ollama future       │
                         └──────────┬──────────┘
                                    │
             ┌──────────────────────┼──────────────────────┐
             │                      │                      │
             ▼                      ▼                      ▼
      Investigation Agent    Root Cause Agent      Remediation Agent
             │                      │                      │
             └──────────────────────┼──────────────────────┘
                                    │
                              Verification Agent
                                    │
                                    ▼
                              Utility Tools
                                    │
             ┌──────────────────────┼──────────────────────┐
             ▼                      ▼                      ▼
          Logs DB               Health DB            Deployments
             │                      │                      │
             └──────────────────────┼──────────────────────┘
                                    ▼
                                PostgreSQL
```

The agents can share the same Gemini model/provider.

Do not unnecessarily make four completely independent LLM processes.

Use a central orchestrator and logical agent roles.

---

# 5. FOUR LOGICAL AI AGENTS

Implement four logical agents.

## Agent 1 — Investigation Agent

Purpose:

Investigate an incident and gather evidence.

Responsibilities:

* inspect application logs
* inspect service health
* inspect recent deployments
* inspect previous incidents
* identify suspicious correlations
* request additional tools when needed

Available tools:

```text
get_application_logs
get_service_health
get_recent_deployments
get_previous_incidents
get_database_status
```

Output should contain:

```json
{
  "status": "investigation_complete",
  "findings": [],
  "evidence": [],
  "next_step": "root_cause_analysis"
}
```

---

# 6. AGENT 2 — ROOT CAUSE ANALYSIS AGENT

Purpose:

Analyze evidence collected by the investigation agent.

Inputs:

* logs
* health metrics
* deployments
* previous incidents
* database status

The agent should:

1. correlate timestamps
2. identify anomalies
3. compare current and previous state
4. identify possible causes
5. rank possible causes internally
6. produce the most likely cause
7. provide concise evidence supporting it
8. provide a confidence value

Do not expose chain-of-thought.

Return a concise reasoning summary.

Example:

```json
{
  "root_cause": {
    "description": "Database connection failure introduced after deployment v1.8.2",
    "confidence": 0.87
  },
  "evidence": [
    {
      "source": "deployment",
      "fact": "v1.8.2 deployed at 11:41"
    },
    {
      "source": "logs",
      "fact": "Database timeout errors began at 11:42"
    },
    {
      "source": "health",
      "fact": "500 error rate increased to 37%"
    }
  ],
  "reasoning_summary": "The failure began immediately after v1.8.2 and database errors appeared at the same time."
}
```

Never invent evidence.

---

# 7. AGENT 3 — REMEDIATION AGENT

Purpose:

Determine the safest corrective action.

Possible actions:

```text
ROLLBACK_DEPLOYMENT
RESTART_SERVICE
NO_ACTION
ESCALATE_TO_HUMAN
```

For MVP, the primary remediation is:

```text
ROLLBACK_DEPLOYMENT
```

The agent must classify the action.

Example:

```json
{
  "action": {
    "type": "ROLLBACK_DEPLOYMENT",
    "target": "v1.8.2",
    "risk": "MEDIUM",
    "requires_human_approval": true,
    "reason": "Deployment is temporally correlated with the incident and rollback is reversible."
  }
}
```

IMPORTANT:

The AI must NEVER directly execute a risky action.

The AI can recommend an action.

The backend controls actual execution.

---

# 8. AGENT 4 — VERIFICATION AGENT

After an approved remediation:

1. check service health
2. check error rate
3. check latency
4. check service status
5. compare before/after values
6. determine whether recovery occurred

Example:

```json
{
  "recovered": true,
  "before": {
    "error_rate": 37,
    "latency_ms": 2800
  },
  "after": {
    "error_rate": 0.8,
    "latency_ms": 180
  },
  "summary": "Service recovered after rollback."
}
```

---

# 9. CENTRAL AGENT ORCHESTRATOR

Create a central orchestrator.

Example conceptual flow:

```text
Incident
   ↓
Create investigation context
   ↓
Investigation Agent
   ↓
Evidence
   ↓
Root Cause Agent
   ↓
Remediation Agent
   ↓
Human Approval Gate
   ↓
Execute Tool
   ↓
Verification Agent
   ↓
Incident Report
```

The orchestrator controls:

* agent transitions
* state
* tool permissions
* approval gates
* retries
* error handling
* event broadcasting
* final report generation

---

# 10. GEMINI API KEY MANAGEMENT

We have four Gemini API keys.

Do NOT hardcode keys.

Use:

```env
GEMINI_API_KEY_1=
GEMINI_API_KEY_2=
GEMINI_API_KEY_3=
GEMINI_API_KEY_4=
```

Create a key manager.

Conceptually:

```text
GeminiKeyPool
├── key 1
├── key 2
├── key 3
└── key 4
```

Implement:

* round-robin selection
* request counting
* error tracking
* temporary cooldown
* fallback to another key when one key fails
* basic per-key usage statistics
* no logging of actual API keys

Example:

```text
Request 1 → Key 1
Request 2 → Key 2
Request 3 → Key 3
Request 4 → Key 4
Request 5 → Key 1
```

If Key 1 fails due to quota/rate limit:

```text
Key 1 → cooldown
Key 2 → selected
```

Do not create four independent agents each permanently tied to one key.

The four agents should share the key pool.

---

# 11. EFFICIENT TOKEN USAGE

Token efficiency is a major requirement.

Do NOT send the entire database, entire log history, or entire incident history to Gemini on every request.

Implement the following.

## A. Small prompts

Use concise system prompts.

## B. Structured evidence

Only send relevant evidence.

Example:

Instead of:

```text
5000 log lines
```

send:

```text
20 relevant ERROR/WARN lines
```

## C. Time-window filtering

For example:

```text
incident time = 11:42
search logs from 11:32 → 11:52
```

## D. Limit previous incidents

Retrieve only the most relevant 3–5 similar incidents.

## E. Separate tool calls

Do not send everything at once if the agent does not need it.

## F. Cache tool results

Within one incident investigation, cache:

```text
logs
health
deployments
previous incidents
```

Do not request identical information twice.

## G. Use structured JSON outputs

Avoid long natural-language responses from agents.

## H. Maximum iteration count

Set:

```text
MAX_AGENT_STEPS = 8
```

Prevent infinite tool loops.

## I. Retry policy

Use limited retries:

```text
max_retries = 2
```

## J. Key rotation

Use the key pool when needed, not to artificially spam requests.

Respect Gemini API quotas and terms.

---

# 12. AI PROVIDER ABSTRACTION

Create:

```text
backend/ai/
├── base.py
├── gemini_provider.py
├── ollama_provider.py
└── factory.py
```

Base interface:

```python
class AIProvider:
    async def generate(...)
    async def generate_structured(...)
    async def execute_tool_loop(...)
```

Implement Gemini now.

Create an Ollama provider interface/stub for later.

Do NOT require Ollama for the MVP.

---

# 13. SIMULATED APPLICATION

Create a simulated production environment.

Application:

# Payment API

Services:

```text
payment-api
auth-api
database
```

For MVP, payment-api is the primary service.

The simulated system must contain:

## Normal state

```json
{
  "status": "healthy",
  "error_rate": 0.2,
  "latency_ms": 180,
  "cpu_usage": 43,
  "memory_usage": 68
}
```

## Incident state

```json
{
  "status": "degraded",
  "error_rate": 37,
  "latency_ms": 2800,
  "cpu_usage": 43,
  "memory_usage": 68
}
```

---

# 14. SIMULATED INCIDENT

Create a deterministic scenario.

Incident:

```text
Payment API suddenly returns 500 Internal Server Error.
```

Timeline:

```text
11:40
Payment service healthy.

11:41
Deployment v1.8.2 occurs.

11:42
Database connection failures begin.

11:42
500 errors increase.

11:43
Service health becomes degraded.
```

Logs should include:

```text
INFO Payment processed
INFO Deployment v1.8.2 started
ERROR Database connection failed
ERROR Database connection timeout
ERROR POST /payment 500
ERROR POST /payment 500
ERROR POST /payment 500
```

Deployment history:

```text
v1.8.0
v1.8.1
v1.8.2
```

v1.8.2 is the problematic deployment.

---

# 15. SIMULATE INCIDENT BUTTON

The frontend must have:

```text
[ SIMULATE INCIDENT ]
```

When clicked:

```text
healthy
   ↓
deployment v1.8.2
   ↓
database configuration failure
   ↓
500 errors
   ↓
degraded
```

Then automatically create an incident.

The system should start the agent investigation.

---

# 16. BACKEND TOOLS

Implement tools as isolated Python functions/classes.

## Read tools

```text
get_application_logs()
get_service_health()
get_recent_deployments()
get_previous_incidents()
get_database_status()
```

## Action tools

```text
rollback_deployment(version)
restart_service(service)
```

## Verification

```text
verify_service_health()
get_current_error_rate()
get_current_latency()
```

Action tools must be protected by backend authorization/approval logic.

Never allow the LLM to directly execute them.

---

# 17. TOOL PERMISSION MODEL

Create:

```text
READ_ONLY
```

tools:

```text
get_application_logs
get_service_health
get_recent_deployments
get_previous_incidents
get_database_status
```

and:

```text
REQUIRES_HUMAN_APPROVAL
```

tools:

```text
rollback_deployment
restart_service
```

The orchestrator should enforce this.

Even if the LLM requests:

```text
rollback_deployment
```

the backend must intercept it and create an approval request.

---

# 18. HUMAN APPROVAL FLOW

Frontend:

```text
Recommended Action

Rollback v1.8.2

Risk: MEDIUM

Reason:
Errors started immediately after deployment.

[ APPROVE ]
[ REJECT ]
```

If APPROVE:

```text
POST /incidents/{id}/approve
```

Backend executes:

```text
rollback_deployment("v1.8.2")
```

If REJECT:

```text
POST /incidents/{id}/reject
```

Incident becomes:

```text
ESCALATED / ACTION_REJECTED
```

Do not automatically execute risky operations.

---

# 19. DATABASE DESIGN

Use PostgreSQL.

Create models/tables:

## incidents

```text
id
title
description
severity
status
service_name
created_at
updated_at
```

## logs

```text
id
service_name
timestamp
level
message
metadata
```

## deployments

```text
id
service_name
version
timestamp
status
commit_sha
```

## service_health

```text
id
service_name
timestamp
status
error_rate
latency_ms
cpu_usage
memory_usage
```

## agent_runs

```text
id
incident_id
agent_name
status
started_at
completed_at
summary
```

## agent_events

```text
id
incident_id
agent_name
event_type
message
metadata
timestamp
```

## approvals

```text
id
incident_id
action_type
target
status
requested_at
approved_at
```

## incident_reports

```text
id
incident_id
root_cause
confidence
evidence
action_taken
recovery_status
report
created_at
```

---

# 20. REST API

Implement:

```text
POST   /api/incidents/simulate
GET    /api/incidents
GET    /api/incidents/{id}

GET    /api/services
GET    /api/services/{name}/health
GET    /api/services/{name}/logs
GET    /api/services/{name}/deployments

POST   /api/incidents/{id}/approve
POST   /api/incidents/{id}/reject

GET    /api/incidents/{id}/events
GET    /api/incidents/{id}/report

GET    /api/system/health
```

Add proper Pydantic request/response schemas.

---

# 21. WEBSOCKET

Create:

```text
WS /ws/incidents/{incident_id}
```

Events should look like:

```json
{
  "type": "agent_started",
  "agent": "investigation",
  "message": "Starting investigation"
}
```

Examples:

```text
incident_created
agent_started
tool_started
tool_completed
evidence_found
root_cause_analysis_started
root_cause_identified
remediation_recommended
approval_required
approval_received
remediation_started
remediation_completed
verification_started
verification_completed
incident_resolved
report_generated
error
```

The frontend should display these live.

---

# 22. FRONTEND DESIGN

Create a professional hackathon-quality dashboard.

Do not make it look like a basic CRUD application.

Use a dark DevOps/SRE dashboard aesthetic.

Main sections:

## Dashboard

Show:

```text
System Status
Active Incidents
Services
Error Rate
Latency
Recent Deployments
```

Main button:

```text
🚨 Simulate Incident
```

---

# 23. INCIDENT PAGE

Display:

```text
Incident Header
Severity
Service
Status
Created time
```

Then:

### Investigation Timeline

Example:

```text
✓ Incident detected

✓ Investigation agent started

✓ Application logs checked

✓ Service health checked

✓ Deployment history checked

✓ Previous incidents checked

✓ Root cause identified

⚠ Human approval required

○ Remediation

○ Verification
```

Use Framer Motion animations.

---

# 24. ROOT CAUSE CARD

Display:

```text
Likely Root Cause

Database connection failure introduced
after deployment v1.8.2

Confidence: 87%

Evidence

• v1.8.2 deployed at 11:41
• DB errors began at 11:42
• 500 errors increased at 11:42
```

Do not display hidden chain-of-thought.

---

# 25. ACTION CARD

Display:

```text
Recommended Action

ROLLBACK v1.8.2

Risk: MEDIUM

Human approval required

[ APPROVE ]
[ REJECT ]
```

---

# 26. RECOVERY CARD

Before:

```text
Error Rate: 37%
Latency: 2800ms
Status: DEGRADED
```

After:

```text
Error Rate: 0.8%
Latency: 180ms
Status: HEALTHY
```

Show a visual comparison.

---

# 27. INCIDENT REPORT

Generate a clean report containing:

```text
Incident
Severity
Service
Timeline
Root Cause
Evidence
Recommended Action
Human Decision
Action Taken
Verification
Final Status
```

Add:

```text
Download Report
```

if practical.

---

# 28. GITHUB WEBHOOKS

Implement a webhook endpoint:

```text
POST /api/webhooks/github
```

The purpose is to demonstrate that deployment events can enter the system automatically.

Handle GitHub webhook events such as:

```text
push
deployment
deployment_status
```

For MVP:

* validate webhook signature when secret is configured
* parse deployment information
* store deployment record
* optionally trigger a deployment-related event in the dashboard

Do NOT depend on the webhook for the core demo.

The "Simulate Incident" flow must work without GitHub.

---

# 29. CI/CD — GITHUB ACTIONS

Create:

```text
.github/
└── workflows/
    ├── backend-ci.yml
    ├── frontend-ci.yml
    └── docker.yml
```

## Backend CI

Run:

```text
Python setup
Install dependencies
Lint
Run tests
```

## Frontend CI

Run:

```text
Node setup
npm ci
Lint
Build
```

## Docker CI

Build:

```text
backend image
frontend image
```

Do not expose API keys in GitHub Actions logs.

Use GitHub Secrets.

Example:

```text
GEMINI_API_KEY_1
GEMINI_API_KEY_2
GEMINI_API_KEY_3
GEMINI_API_KEY_4
DATABASE_URL
GITHUB_WEBHOOK_SECRET
```

---

# 30. DOCKER COMPOSE

Create:

```text
docker-compose.yml
```

Services:

```text
frontend
backend
postgres
```

Optional:

```text
nginx
```

Do not make the architecture unnecessarily complicated.

The project should start with:

```bash
docker compose up
```

or a clearly documented local development command.

---

# 31. SECURITY REQUIREMENTS

Implement basic security.

## Never commit:

```text
.env
API keys
database passwords
GitHub webhook secrets
```

Create:

```text
.env.example
```

Use environment variables.

Add:

```text
.gitignore
```

with appropriate entries.

Validate webhook signatures.

Validate action permissions.

Do not trust LLM output as executable code.

Use allowlisted actions.

---

# 32. ERROR HANDLING

Handle:

* Gemini API failure
* invalid Gemini JSON
* tool failure
* database failure
* WebSocket disconnect
* approval timeout
* rollback failure
* verification failure
* missing data
* rate limit
* API key exhaustion

The UI should display meaningful errors.

The agent should not silently fail.

---

# 33. RETRIES

Use:

```text
LLM retry: max 2
Tool retry: max 2
```

Do not create infinite loops.

Maximum agent iterations:

```text
8
```

---

# 34. OBSERVABILITY

Backend should produce structured logs.

Example:

```json
{
  "timestamp": "...",
  "level": "INFO",
  "event": "tool_call",
  "agent": "investigation",
  "tool": "get_application_logs",
  "incident_id": "..."
}
```

Do not log API keys.

---

# 35. TESTING

Create backend tests for:

```text
incident creation
simulation
logs tool
health tool
deployment tool
root cause output validation
approval flow
rejection flow
rollback
verification
webhook
```

Create frontend tests for critical UI behavior if practical.

Most importantly, create one end-to-end test:

```text
simulate incident
→ investigation
→ root cause
→ recommendation
→ approval
→ rollback
→ verification
→ report
```

---

# 36. MVP SUCCESS CRITERIA

The MVP is considered complete ONLY when this entire flow works:

```text
1. User opens OpsPilot.

2. User clicks:
   "Simulate Incident"

3. Payment API becomes degraded.

4. Incident appears.

5. Investigation Agent starts.

6. Agent retrieves:
   - logs
   - health
   - deployments
   - previous incidents

7. Root Cause Agent identifies:
   database connection failure after v1.8.2

8. UI shows evidence.

9. Remediation Agent recommends:
   rollback v1.8.2

10. UI asks:
    "Approve rollback?"

11. User clicks APPROVE.

12. Backend performs simulated rollback.

13. Verification Agent checks service.

14. Error rate changes:
    37% → 0.8%

15. Latency changes:
    2800ms → 180ms

16. Service becomes HEALTHY.

17. Incident report is generated.

18. Dashboard shows:
    RESOLVED.
```

This must work reliably multiple times.

---

# 37. DEMO MODE

Create a dedicated demo mode.

A reset endpoint/button should exist:

```text
POST /api/demo/reset
```

This resets:

```text
service state
incident state
logs
deployment state
approval state
```

so the team can repeat the demo.

Add:

```text
RESET DEMO
```

to the UI, but make it visually secondary.

---

# 38. PROJECT STRUCTURE

Use a clean monorepo:

```text
opspilot/
│
├── frontend/
│   ├── app/
│   ├── components/
│   ├── hooks/
│   ├── lib/
│   ├── types/
│   └── package.json
│
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── agents/
│   │   ├── ai/
│   │   ├── core/
│   │   ├── db/
│   │   ├── models/
│   │   ├── schemas/
│   │   ├── services/
│   │   ├── tools/
│   │   ├── websocket/
│   │   └── main.py
│   │
│   ├── tests/
│   ├── requirements.txt
│   └── Dockerfile
│
├── .github/
│   └── workflows/
│
├── docs/
│   ├── architecture.md
│   ├── api.md
│   ├── agent-design.md
│   └── demo.md
│
├── docker-compose.yml
├── .env.example
├── .gitignore
└── README.md
```

---

# 39. API CONTRACT FIRST

Before implementing frontend/backend integration, create API documentation.

Use OpenAPI automatically through FastAPI.

Frontend should use typed API models.

Do not make the frontend depend on undocumented backend behavior.

---

# 40. CODE QUALITY

Follow these rules:

* TypeScript strict mode
* Python type hints
* Pydantic validation
* async FastAPI where appropriate
* clean separation of concerns
* reusable services
* no giant files
* no duplicated logic
* no hardcoded secrets
* no hardcoded API keys
* no LLM-generated arbitrary shell execution
* no direct database calls from frontend
* meaningful error responses

---

# 41. DO NOT OVERENGINEER

This is a hackathon MVP.

Do NOT implement:

* real AWS production management
* real Kubernetes cluster management
* arbitrary shell execution
* autonomous production deployments
* complex distributed microservices
* unnecessary agent frameworks
* complicated vector databases
* unnecessary RAG
* unnecessary authentication systems
* expensive infrastructure

The simulated environment is intentional.

The goal is to demonstrate the agent workflow clearly.

---

# 42. FUTURE VERSION — OLLAMA + QWEN

Prepare the codebase for:

```text
Gemini
   ↓
Ollama
   ↓
Qwen
```

Future capabilities:

* local inference
* offline demo
* model selection
* configurable model
* local embeddings if needed
* local incident memory

But DO NOT allow future work to break the Gemini MVP.

---

# 43. FUTURE VERSION — MORE REALISTIC DEVOPS

After MVP is stable, optionally add:

```text
Docker container health
Prometheus-style metrics
GitHub deployments
real application logs
synthetic traffic
multiple simultaneous incidents
multiple services
incident severity scoring
past incident retrieval
automatic anomaly detection
```

These are NOT MVP requirements.

---

# 44. MULTI-INCIDENT FUTURE EXTENSION

The architecture should allow:

```text
Incident A
Incident B
Incident C
```

simultaneously.

The orchestrator should associate every event with:

```text
incident_id
```

so investigations do not mix evidence.

---

# 45. AGENT EVENT MODEL

Every agent action should create an event.

Example:

```json
{
  "incident_id": "INC-001",
  "agent": "investigation",
  "event_type": "tool_completed",
  "tool": "get_application_logs",
  "message": "Retrieved 14 relevant log entries",
  "timestamp": "..."
}
```

The frontend consumes these through WebSocket.

This gives the demo a visible live agent workflow.

---

# 46. AGENT STATES

Implement clear states:

```text
DETECTED
INVESTIGATING
ANALYZING
AWAITING_APPROVAL
REMEDIATING
VERIFYING
RESOLVED
FAILED
ESCALATED
```

Use these states consistently across backend and frontend.

---

# 47. INCIDENT SEVERITY

Use:

```text
LOW
MEDIUM
HIGH
CRITICAL
```

For the demo incident:

```text
HIGH
```

Severity can initially be determined using deterministic rules and/or Gemini.

Do not make severity depend entirely on an LLM.

---

# 48. SAFETY DESIGN

The AI should never have unrestricted system access.

Never allow Gemini to:

```text
execute arbitrary shell commands
run arbitrary Python
delete files
modify arbitrary database records
execute arbitrary SQL
```

Use explicit allowlisted tools.

The AI proposes.

The backend validates.

Human approves risky actions.

Backend executes the allowed action.

This distinction should be visible in the architecture documentation.

---

# 49. README

Create a comprehensive README containing:

* project overview
* problem statement
* solution
* architecture
* agent architecture
* tech stack
* setup
* environment variables
* Gemini setup
* database setup
* Docker setup
* running locally
* testing
* GitHub Actions
* GitHub webhook setup
* demo instructions
* API overview
* future Ollama/Qwen architecture
* team responsibilities

---

# 50. TEAM RESPONSIBILITIES

Design the repository so four people can work independently.

## Person 1 — AI/Agents

Own:

```text
backend/app/agents/
backend/app/ai/
backend/app/prompts/
```

Tasks:

* Gemini provider
* key pool
* agent orchestration
* investigation agent
* RCA agent
* remediation agent
* verification agent
* structured outputs
* token optimization

---

## Person 2 — Backend/DevOps Simulation

Own:

```text
backend/app/api/
backend/app/tools/
backend/app/services/
backend/app/models/
backend/app/db/
```

Tasks:

* FastAPI
* PostgreSQL
* database models
* simulated payment service
* logs
* health
* deployments
* rollback
* verification
* approval endpoints
* webhook endpoint

---

## Person 3 — Frontend

Own:

```text
frontend/
```

Tasks:

* dashboard
* incident screen
* live timeline
* root cause card
* evidence UI
* approval UI
* recovery UI
* reports
* WebSocket integration
* responsive design
* animations

---

## Person 4 — Integration / CI/CD / QA

Own:

```text
.github/
docs/
docker-compose.yml
tests/integration/
```

Tasks:

* GitHub setup
* branch strategy
* GitHub Actions
* Docker
* webhook testing
* integration testing
* end-to-end test
* environment configuration
* demo reset
* documentation
* final demo workflow

---

# 51. DEVELOPMENT ORDER

Do not attempt to build everything simultaneously.

Follow this order.

## Phase 1

Create repository and folder structure.

## Phase 2

Create PostgreSQL models and migrations.

## Phase 3

Create simulated payment service.

## Phase 4

Create FastAPI tools.

## Phase 5

Create Gemini provider.

## Phase 6

Create agent orchestrator.

## Phase 7

Connect tools to agents.

## Phase 8

Implement approval flow.

## Phase 9

Implement verification.

## Phase 10

Implement WebSocket events.

## Phase 11

Build frontend.

## Phase 12

Connect frontend/backend.

## Phase 13

Add Docker.

## Phase 14

Add GitHub Actions.

## Phase 15

Add GitHub webhook.

## Phase 16

Create E2E test.

## Phase 17

Polish demo.

---

# 52. IMPORTANT IMPLEMENTATION RULE

Do not simply generate the entire project blindly and assume it works.

Work incrementally.

At every phase:

1. implement
2. run
3. test
4. fix
5. continue

Before moving to the next major subsystem, verify that the previous subsystem actually works.

---

# 53. FIRST TASK

Start by inspecting the current directory.

If an existing project/repository exists, understand it before modifying it.

If the directory is empty, initialize the OpsPilot monorepo.

Then implement Phase 1:

```text
repository
folder structure
environment configuration
Docker Compose skeleton
FastAPI skeleton
Next.js skeleton
PostgreSQL connection
basic health endpoint
README
GitHub Actions skeleton
```

Then run the project.

Verify:

```text
Frontend → works
Backend → works
PostgreSQL → works
Docker → works
CI → works
```

After that, proceed incrementally through the implementation.

Do not stop after creating files.

Actually run the code and fix errors.

---

# 54. FINAL ACCEPTANCE TEST

Before declaring the project complete, execute this exact test:

```text
START SYSTEM

↓

Open Dashboard

↓

Click "Simulate Incident"

↓

Payment API becomes DEGRADED

↓

Incident INC-001 created

↓

Investigation Agent starts

↓

get_application_logs()

↓

get_service_health()

↓

get_recent_deployments()

↓

get_previous_incidents()

↓

Root Cause Agent

↓

Likely Cause:
Database connection failure after v1.8.2

↓

Evidence displayed

↓

Remediation Agent

↓

Rollback v1.8.2 recommended

↓

Human Approval screen

↓

Click APPROVE

↓

Rollback executes

↓

Verification Agent

↓

Error rate:
37% → 0.8%

↓

Latency:
2800ms → 180ms

↓

Service:
HEALTHY

↓

Incident:
RESOLVED

↓

Incident Report generated

↓

RESET DEMO

↓

Run again successfully
```

If any step fails, debug it before considering the MVP complete.

---

# 55. FINAL PRIORITY

Prioritize in this order:

```text
1. Working end-to-end incident workflow
2. Reliable AI tool calling
3. Human approval safety
4. Correct evidence-based RCA
5. Recovery verification
6. Live WebSocket agent events
7. Professional frontend
8. Tests
9. CI/CD
10. GitHub webhook
11. Documentation
12. Future Ollama/Qwen abstraction
```

Do not sacrifice the core workflow to add unnecessary features.

The final MVP must be:

**Reliable, explainable, demonstrable, safe, and easy for a judge to understand within 5 minutes.**
