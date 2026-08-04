# Enterprise Expert Agent Architecture

## Role Model

The platform separates orchestration, expert judgment, and controlled execution.
The Supervisor has no business tool permissions. It selects Workers, enforces
handoff order, and aggregates artifacts.

| Role | Responsibility | Output | Boundary |
| --- | --- | --- | --- |
| Legal Supervisor | Route legal service requests and coordinate expert handoffs | Worker plan and aggregate conclusion | Cannot call business tools |
| Legal Knowledge Agent | Retrieve and cite legal knowledge | Cited answer or evidence list | Read-only; no external actions |
| Consultation Agent | Extract case facts, legal issues, and consultation outcomes | Structured consultation output | Cannot create legal opinions |
| Contract Analysis Agent | Analyze contract clauses and risk patterns | Structured risk analysis report | No contract modification or non-whitelisted data |
| Document Expert | Assess document completeness, clause coverage, and legal risk | Document status and risk assessment draft | Cannot sign, edit, or send legal documents |
| Contract Review Expert | Review contracts, supplementary agreements, and clause conflicts | Risk list with evidence and review notes | Cannot sign, edit, or send contracts |
| Document Draft Expert | Convert verified findings into legal document drafts | Legal opinion or document draft | Draft only; no external send or filing |
| Review Workflow Executor | Apply approved review and audit actions | Auditable review execution result | Cannot make legal judgments |

## Collaboration Rules

1. Experts use only their ACL-scoped MCP tools.
2. The Supervisor passes structured handoff context between Workers.
3. A Worker requesting a tool owned by a later planned role is closed and
   handed off by the Supervisor; the request is never executed under the wrong
   role.
4. Evidence-bearing findings are verified before gated write operations.
5. Legal opinion creation, review actions, and sensitive data operations retain the
   existing approval workflow.

## Demo Flow

`Review contract clause risks, compare with supplementary agreement terms, and
draft a review opinion` routes to Contract Review Expert, Consultation Agent, and
Document Draft Expert. The contract and consultation branches are read-only; the
document draft role receives the verified upstream evidence as a structured
handoff and can only create a draft.
