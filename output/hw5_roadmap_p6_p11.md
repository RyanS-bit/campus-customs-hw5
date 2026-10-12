# Homework 5: roadmap for Problems 6–11 (plan only; nothing implemented)

**What this is based on:** the problem titles and requirements Dr. Ryan supplied (Problem 1, follow-up 1; Problem 2, follow-up 2; Problem 5, follow-ups 4–6). Items marked **[confirm]** need the official assignment text, which is not in `hw5/`. Nothing here has been started.

## Dependency overview
```
P5 (agents + MCP + audit)  ──►  P6 Plan the three tickets ──┐
P5  ──►  P7 Backend routes (FastAPI; human approval) ──► P8 Agent dashboard ──► P9 Resolve the tickets ──► P10 Reflection
                                                                                                └──► P11 Submit to GitHub
```

| Problem | Points | Known requirements (as supplied) | Must exist or be shown first | Main deliverables | Open questions |
|---|---|---|---|---|---|
| **6. Plan the three tickets** | 8 | Plan the handling of tickets 101, 102, and 103 | Problem 5 accepted; the facts and dependency map in `output/p5_ticket_workflow_map.md` | A written plan: order of work, agents and delegation, proposals expected, approvals needed, risks (the $160 vs $264 cash conflict; **no known hoodie supplier**; unknown delivery of invoice 501) | **[confirm]** format and location; whether the plan must be produced by the agents, by hand, or both |
| **7. Backend routes** | 7 | A FastAPI backend connecting the agents to the dashboard; **human approval routes are explicitly required** | Problem 5 runtime (`backend/runner.py`); the approved design for approval and execution (architecture §4–5, §13–14; spec §7) | Routes to list tickets, proposals, drafts, cash, and audit events; **approve / reject** (human, attributed); **execute** with an atomic transaction (R1 checked at write time; R4 updates; D4 reservation; D6 conventions as proposed policy); **ticket-status update** by a human, audited (spec §7.7); start an agent run (authorization-gated); tests for all of these | **[confirm]** required route list and response formats; approver identity (a typed name is attribution only) |
| **8. Agent dashboard** | 12 | React + Vite + TypeScript; shows tickets, agent activity, approvals, and cash balances | Problem 7 routes | A UI that reads the routes; agent activity from `audit_trail.json`; approve/reject controls calling the human routes; nothing sent automatically (R7); no agent-side writes | **[confirm]** required views; build and run instructions |
| **9. Resolve the tickets** | 10 | Run the real workflow on tickets 101–103 with the live team; reset first (R8) | Problems 6–8; Dr. Ryan's authorization for the reset and the live run (budget, `live_authorization`); a definition of "resolved" for each ticket | An authorized reset (recorded); a live run on `gpt-6-luna` (recorded in `output/audit_trail.json`); human approvals or rejections; executed payments only where approved and R1 holds; honest ticket outcomes; reproducibility instructions | **[confirm]** what counts as "resolved" when outside events (vendor quote, delivery, customer acceptance, a hoodie supplier) are unavailable. Results must not be fabricated. |
| **10. Reflection** | 10 | A written reflection | Problems 5–9 evidence | A reflection drawing on the evidence: design choices, controls, failures found (REPLACE gap, concurrency and lock bugs), limits of agentic systems | **[confirm]** length, prompts, location |
| **11. Submit to GitHub** | 5 | Submit the repository; **both database files** (`data/campus_customs.db`, `data/campus_customs_new.db`) included; works without machine-specific configuration | All problems complete; a final regression run | A clean repository: no `.env` or secrets (`.gitignore` checked); `output/audit_trail.json` committed; README reproduction steps; requirements pinned | **[confirm]** which repository (the existing private `RyanS-bit/ai-foundations` or a separate one); access for Professor Zaman |

## Cross-cutting conditions for every remaining problem
- **One problem at a time,** each with Dr. Ryan's explicit approval. Every prompt is logged verbatim.
- **Model:** `gpt-6-luna` through Portkey only. Every live call is separately authorized.
- **Data:** the original database is never modified. The working copy is reset only when authorized, and every change is recorded.
- **Integrity:** no fabricated business facts, outcomes, or evidence. Simulated activity never enters the real audit trail.
