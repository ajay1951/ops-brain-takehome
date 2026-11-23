# PRD 2 — Publishing & Insights Hub | One-Page Build Plan

**Target System:** System 2 — Publishing & Insights Hub  
**Role:** Central Multi-Channel Dispatch, Scheduling, Human Approval, and Analytics Feedback Loop  
**Core Build Principle Adherence:** Notion is single source of truth; Slack is the approval/alert channel; Zero custom frontend; All content engines hand off here; Human approval required for public publishing; Run logging & cost awareness.

---

## 1. What We Ship First (v1) & Why

### The v1 Scope
1. **Unified Content Ingestion Schema:** A single Notion database ("Content Pipeline") where all upstream creators and engines (PRDs 3–7: Text, Carousels, Video, Motion Graphics) deposit draft posts with media attachments and target platforms.
2. **Human Approval Gate (Slack Block Kit):** When a post status becomes `Ready for Review`, an interactive card is sent to the brand lead's Slack with preview media, copy, platform tags, and two buttons: `[Approve & Schedule]` and `[Reject with Feedback]`.
3. **Multi-Platform Automated Publishing:** Approved posts are handed off to a managed multi-platform publishing API (Ayrshare) for scheduled distribution to LinkedIn and X (Twitter), expanding to YouTube/Instagram.
4. **Performance Insights Feedback Loop:** A daily 24h/7d cron collects reach, impressions, likes, and shares, writing them back to Notion. This allows upstream prompt configs to automatically learn what content works.

### Why This First?
Systems 3 through 7 (Text Engine, Carousels, Yap Video, Motion Graphics, Character Content) **all depend on System 2**. Building scheduling and API integrations inside individual content engines creates duplicate maintenance nightmares and token refresh failures. Shipping this hub first gives every content system an immediate, robust destination.

---

## 2. Architecture Diagram

```
[ Upstream Content Engines (PRDs 3-7) ] 
  • Text Content Engine (Brand/Founder)
  • AI Carousels & Video Generators
           │
           ▼
[ Notion "Content Pipeline" DB ] ──(Status: Ready for Review)
           │
           ▼
[ Hub Dispatcher (Serverless Worker / Cloudflare / Modal) ]
           │
           ├──────────────────────────────┐
           ▼                              ▼
[ Slack Approval Gate ]          [ Execution Audit Log ]
  • DM to Brand Lead:              • logs/runs.jsonl / Notion
    [ Approve & Schedule ]         • Model, cost, approver, status
    [ Request Revision ]
           │ (Upon Approval)
           ▼
[ Managed Social API Layer (Ayrshare / Buffer) ]
           │
    ┌──────┴──────────────┬──────────────────┐
    ▼                     ▼                  ▼
[ LinkedIn ]         [ X / Twitter ]   [ YouTube / IG ]
    │                     │                  │
    └──────┬──────────────┴──────────────────┘
           │ (T+24h & T+7d Engagement Metrics)
           ▼
[ Daily Insights Sync Engine ] ──► Updates Notion: Impressions, Clicks, Top 10% Tag
```

---

## 3. Data Model

### `ContentItem` (Notion "Content Pipeline" DB)
* `id`: UUID / Notion Page ID
* `title`: String (Internal working title)
* `source_system`: Enum (`"Text Engine"`, `"AI Carousel"`, `"Yap Editor"`, `"Motion Graphics"`, `"Human"`)
* `target_platforms`: Multi-Select (`"LinkedIn"`, `"X"`, `"Instagram"`, `"YouTube"`)
* `copy_text`: Rich Text (Post body text, hashtags)
* `media_urls`: Files & Media (Images, carousel PDFs, video MP4s)
* `status`: Enum (`"Draft"`, `"Ready for Review"`, `"Scheduled"`, `"Published"`, `"Failed"`)
* `scheduled_time`: DateTime (ISO 8601 UTC)
* `human_approver`: User / String (Mandatory gate before dispatch)
* `rejection_reason`: Text (Feedback loop to upstream AI generators)
* `external_post_ids`: JSON / Text (Platform tracking IDs from Ayrshare)

### `PostAnalytics` (Notion "Insights" Rollup)
* `content_id`: Relation $\to$ `ContentItem.id`
* `impressions_24h`: Integer
* `impressions_7d`: Integer
* `engagement_rate`: Float (`(likes + comments + shares) / impressions`)
* `top_performer`: Boolean (Computed flag: top 10% engagement for that format)

### `RunLog` (Telemetry & Cost)
* `run_id`: UUID
* `timestamp`: DateTime
* `action`: Enum (`"Ingest"`, `"Approval"`, `"Publish"`, `"AnalyticsSync"`)
* `provider_cost_usd`: Float
* `status`: Enum (`"Success"`, `"Retrying"`, `"Failed"`)

---

## 4. What to Buy vs. Build

| Component | Decision | Justification |
| :--- | :--- | :--- |
| **Front-End & Content Queue** | **Buy (Notion)** | Adheres to core build principle. Non-technical marketers manage content in Notion boards; zero custom React UI. |
| **Human Review Interface** | **Buy (Slack Block Kit)** | Approvers can review previews and click "Approve" from their phone without logging into social accounts. |
| **Social Media Network APIs** | **Buy (Ayrshare API)** | **Crucial Buy:** Managing direct APIs for LinkedIn, X, Meta, and YouTube requires maintaining OAuth2 refresh tokens, handling rate limits, and constant breaking API changes. Ayrshare handles multi-network publishing via a single unified REST API for ~$29/mo. |
| **Hub Orchestrator & State Sync** | **Build (Custom Python Serverless)** | Handles status transitions (`Ready for Review` $\to$ `Approved` $\to$ `Scheduled`), Slack button webhooks, and writes analytics back to Notion. |
| **Insights & Feedback Aggregator** | **Build (Lightweight Cron)** | Polls post metrics at 24h and 7d post-publish, flagging high-performing content so upstream generators learn what hooks work. |

---

## 5. How Failures Are Handled

1. **Social Platform API Rejection / Token Expiry:**
   - If Ayrshare / social platforms reject a post (e.g., video format mismatch or expired token), status automatically flips to `Failed`.
   - An immediate high-priority alert posts to `#ops-alerts` in plain English:  
     `"⚠️ LinkedIn rejected post 'Q3 Strategy'. Reason: Media file exceeds 200MB. [Click here to review runbook]"`.
2. **Missing Approvals Before Scheduled Slot:**
   - If a post's `scheduled_time` arrives but status is still `Ready for Review`, the system **never auto-publishes** (strictly enforcing human approval principles).
   - An escalation ping is sent to the backup brand editor in Slack 2 hours prior to the slot.
3. **Webhook Retries & Dead-Letter Queue:**
   - Webhook deliveries use exponential backoff with jitter (max 3 retries over 15 minutes).
   - Poison payloads are archived into a Notion `Dead-Letter Queue` view with the raw error response.

---

## 6. Estimated Monthly Running Cost (200 Posts / Month)

Assuming **200 social media posts published across LinkedIn, X, and Instagram monthly**:

| Service / Resource | Usage / Month | Unit Cost | Monthly Cost |
| :--- | :--- | :--- | :--- |
| **Social API Layer (Ayrshare)** | Up to 1,000 posts across 10 social profiles | Flat SaaS tier | **$29.00 / mo** |
| **Hub Orchestrator (Cloudflare Workers / Modal / AWS Lambda)** | ~2,000 webhook invocations | Free tier | **$0.00 / mo** |
| **Notion & Slack APIs** | Standard Webhooks & REST endpoints | Included in existing plans | **$0.00 / mo** |
| **Media Storage (Cloudflare R2 / AWS S3 for videos)** | 200 posts $\times$ 50MB avg = 10 GB | $0.015 / GB | **$0.15 / mo** |
| **Total Estimated Monthly Running Cost** | | | **~$29.15 / month** |

---

## 7. Submission Talking Points (Loom Script Track)

* **The Problem:** 5 separate content generators (text, carousels, video) cannot each build and maintain their own social schedulers and OAuth tokens.
* **The Solution:** A unified Hub where content systems end at `Ready for Review`. Non-technical editors approve in Slack, Ayrshare handles the multi-network API complexity, and analytics flow back into Notion to guide future AI content generation.
* **Cost Efficiency:** Under $30/month for 200 posts, completely eliminating manual social media management overhead.
