# Phase 5 — Deliverables

The brief's deliverables and where each one is:

| Deliverable | Where | State |
|---|---|---|
| Source code with a clear structure in a Git repo | `src/fakturama_agent/` (`extract/`, `spec/`, `uia/`, `flow/`, `cli.py`), `tests/` | done |
| Setup instructions: dependencies and how to run against Fakturama | `README.md` › Setup, Run it | done |
| Annotated screenshots or a short recording | `docs/screenshots/` (3 images from the clean run's evidence, made by `tools/annotate_screenshots.py`) | done; a recording is optional |
| README | `README.md` | done |
| Written question: "If you had 3 more hours, what would you do?" | `README.md` › Written questions | done |
| Skipped items noted | `README.md` › Deviations and limits | done |

## Recorded while preparing (2026-11-06)

- The clean end-to-end test took three runs (out/runs/…T135134, …T135305, …T135747): the first two were stopped by the table reader refusing a grid whose bottom rows were covered. The evidence screenshot shows a desktop notification ("friend is playing …") over the lower right of the screen. The reader now waits up to 10 s for such an overlay and saves a full-screen picture of it to `out/overlays/`.
- Currency check added to `run`: Fakturama has one currency per workspace, which the user sets (the tool never changes it). The order's currency must match it, else review; `--accept-currency` enters the amounts under the workspace currency. The sample's earlier live runs (US-dollar workspace, EUR order) predate the check.
- Screenshots in the repo blur the window title and the start page URL (both show the workspace path) and crop the notification corner.

## Optional before submitting

- A short screen recording of `run` on a cleaned workspace (turn off desktop notifications first; delete INV/PO/Debtor/products/term as in the Phase 4 test, keep VAT 19%).
- Read the README once as a reviewer would; check the commit history reads well (`git log --oneline`).
