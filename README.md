# Ops Brain (PRD 1) — Spike & 1-Page Build Plan

This repository contains the working prototype spike and submission documentation for **PRD 1 — Ops Brain** (Product Engineer Take-Home).

---

## 1. What We Ship First (v1) & Why

### The v1 Scope
- **Ingestion**: Meeting notes / transcripts (from Zoom, Fireflies, or pasted into Slack/Notion) parsed into structured Goals & Tasks.
- **Human Approval Gate**: Newly extracted tasks land in Notion as `Status: Proposed`. The meeting organizer receives a Slack approval button (`Approve All` or edit assignments) before work is formally assigned.
- **Deterministic DAG Prioritization**: A transparent, mathematical score (Goal Weight 40% + Urgency 35% + Descendants/Blockers 25%) computed via `networkx`, accompanied by an explicit explainability string (`G1 weight 3/3 | Due in 2d | Blocks 4 tasks`).
- **Slack "My Day" Digest**: Individual team members receive a clean morning summary in Slack showing their top priority, what blocks them, and whom they are blocking.
- **Cascading Slip Alerts**: When an upstream task slips, all downstream assignees and the founder/lead receive immediate proactive warnings before the final milestone is compromised.

### Why This First?
It solves the core problem: **decisions trapped in meeting notes and silent upstream task slips breaking company goals**—without introducing the complexity of full bi-directional project management tooling upfront.

---

## 2. Architecture Diagram

```
[ Meeting Audio / Notes ] 
           │
           ▼
[ LLM Extraction Layer ] ──(versioned prompt: prompts/extraction_v1.txt)──► Groq / Gemini Flash
           │
           ▼
[ Human Approval Gate ] ──(Slack / Notion: Proposed -> Active)
           │
           ▼
[ Ops Brain DAG Engine ] ──(networkx: cycle detection, topological sort, diff snapshot)
     │            │
     ▼            ▼
[ Notion DBs ]  [ Slack Webhooks / Block Kit ]
  • Goals DB      • Daily "My Day" DM to assignees
  • Tasks DB      • 48h automated nudges & 2-strike escalation
  • Run Logs DB   • Cascading slip warnings to downstream owners & founders
```

---

## 3. Data Model

### `Goal`
- `id`: String (e.g., `G1`)
- `title`: String
- `owner`: String (e.g., `Shahaan`)
- `target_date`: Date (ISO 8601)
- `priority_weight`: Integer (`1` = Low, `2` = Medium, `3` = High)
- `health_status`: Enum (`"On Track"`, `"At Risk"`, `"Off Track"`)
- `health_reason`: String (e.g., `"'T1' slipped 10d, pushing G1 4d past Dec 15"`)

### `Task`
- `id`: String (e.g., `T1`)
- `title`: String
- `owner`: String (e.g., `Riya`)
- `due_date`: Date
- `status`: Enum (`"Proposed"`, `"Active"`, `"Blocked"`, `"Done"`)
- `goal_id`: Relation $\to$ `Goal.id`
- `blocked_by`: Array of Task IDs (relations to upstream prerequisites)
- `priority_score`: Float ($0.0 - 100.0$)
- `priority_reason`: String (Human-readable breakdown of score components)
- `nudge_count`: Integer (tracks automated follow-ups)

### `RunLog` (Audit & Observability)
- `run_id`: UUID
- `timestamp`: DateTime
- `model`: String (e.g., `openai/gpt-oss-120b`)
- `input_tokens` / `output_tokens`: Integer
- `cost_usd`: Float
- `approved_by`: String (e.g., `Shahaan (Meeting Organizer)`)

---

## 4. What to Buy vs. Build

| Component | Decision | Justification |
| :--- | :--- | :--- |
| **Front-End / UI** | **Buy (Notion + Slack)** | Principle adherence: Notion is the team's single source of truth; Slack is the communication channel. Zero custom React/web UI needed. |
| **Transcription** | **Buy (Deepgram / Whisper API)** | Commodity infrastructure; ~$0.0043/min. Building speech-to-text is unjustified. |
| **AI Extraction Layer** | **Buy (Groq / Gemini Flash)** | Ultralow latency (<1s) and low cost (<$0.001 per run). |
| **DAG & Cascade Engine** | **Build (Custom Python / NetworkX)** | LLMs fail at multi-hop deterministic graph traversal and date arithmetic. A 100-line deterministic graph engine guarantees mathematical consistency, cycle prevention, and transparent reasoning. |
| **Run & Cost Logging** | **Build (Simple append-only JSONL / DB table)** | Captures run telemetry and cost tracking without heavyweight vendor lock-in. |

---

## 5. How Failures Are Handled

1. **Circular Dependencies (Graph Cycles):**
   - The engine checks `nx.is_directed_acyclic_graph()`.
   - If a cycle is detected (e.g., `T1 <-> T2`), synchronization is safely blocked, and a high-priority alert is sent to `#ops-alerts` with the exact cyclic node list.
2. **LLM Ingestion / JSON Parsing Failures:**
   - Enforced Pydantic schema validation.
   - If the transcript is malformed or missing key parameters (e.g., missing owners), the raw text is archived into a Dead-Letter Queue (DLQ) and posted to the meeting organizer with an inline Slack edit button.
3. **Slack Notification / Rate Limits:**
   - Webhook calls implement exponential backoff with jitter.
4. **Offline / Network Outage:**
   - The extraction layer gracefully falls back to pre-cached templates or offline queues, logging errors to `logs/runs.jsonl`.

---

## 6. Estimated Monthly Running Cost (Team of 10)

Assuming **50 meetings / brainstorm sessions per month**:

| Service / Item | Usage / Month | Unit Cost | Total Monthly Cost |
| :--- | :--- | :--- | :--- |
| **LLM Extraction (Groq / Gemini Flash)** | 50 meetings $\times$ ~2,000 tokens | ~$0.15–$0.60 per 1M tokens | **$0.05 / mo** |
| **Audio Transcription (Deepgram)** | 50 meetings $\times$ 30 mins = 1,500 mins | $0.0043 / min | **$6.45 / mo** |
| **Engine Compute (Modal / Cloudflare Worker / AWS Lambda)** | 50 cron executions + webhooks | Free tier | **$0.00 / mo** |
| **Notion & Slack APIs** | Standard API tier | Included in existing plans | **$0.00 / mo** |
| **Total Running Cost** | | | **~$6.50 / month** |

---

## 7. How to Run the Spike Prototype

```powershell
# 1. Activate Virtual Environment
.\.venv\Scripts\Activate.ps1

# 2. Run the Engine Demo
python main.py
```

### What the demo proves:
1. **Dynamic Prompt Loading**: Uses versioned prompt in `prompts/extraction_v1.txt`.
2. **Human Approval Gate**: Tasks start in `Proposed` state and require approval before assignment.
3. **Deterministic Prioritization**: Real-time scoring with human-readable explanations.
4. **Day-to-Day Stability**: State diff check prevents duplicate alerts when nothing changes.
5. **Automated Follow-ups**: Nudges tasks due $\le 48\text{h}$ with Slack action buttons; escalates after 2 misses.
6. **Cascading Slip Simulation**: Propagates 10-day delay through downstream tasks and updates Founder Goal Health to `Off Track`.
7. **Cycle Detection Guardrail**: Blocks invalid loops (`T1 <-> T2`).
8. **Run Telemetry**: Logs model, token usage, and cost to `logs/runs.jsonl`.
