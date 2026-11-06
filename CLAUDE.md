# Fakturama Image-to-Cash automation (TJM Labs take-home)

Turn one order image into a saved, verified Order + linked Invoice in Fakturama 2.2.0 (English, Windows), driven through Microsoft UI Automation. Brief: `docs/assessment_brief.pdf`.

## Read first

- `docs/DESIGN.md` — approved design (architecture, OrderSpec contract, extraction strategy, UIA grounding, workflow, decisions).
- `docs/PHASE5_PLAN.md` — the finished deliverables phase: where each deliverable is.
- `docs/PHASE4_PLAN.md` — the finished Invoice phase: parts A–E, brief steps 4.6–5.7, recordings, decisions.
- `docs/PHASE3_PLAN.md` — the finished Order phase: parts A–J, decisions, known Fakturama behaviours.
- `docs/PHASE2_PLAN.md` — the finished extraction phase (golden test data).
- `agent findings/fakturama-uia-findings.md` — observed UIA behavior of Fakturama from an earlier exploration. Its rules are binding.

## Phases

1. Design — done.
2. Extraction — done. Image → validated `OrderSpec` JSON.
3. Order flow via UIA — done (`run`, see `docs/PHASE3_PLAN.md`).
4. Linked Invoice + payment status — done (`run` ends with it, see `docs/PHASE4_PLAN.md`).
5. README, screenshots/recording, written answers — done (see `docs/PHASE5_PLAN.md`).

Work only on the current phase unless asked.

## Decisions (do not revisit without asking)

- Python 3.12, `uv`; pywinauto (UIA backend) later for Phase 3.
- Extraction = Gemini Flash vision with JSON-schema output + RapidOCR cross-check + arithmetic validation. The LLM copies values verbatim; code derives everything else.
- Money/percentages are `Decimal`, never float. Dates ISO `YYYY-MM-DD`.
- Paid status: anything other than PAID → UNPAID.
- Payment method enum is closed (Bank Transfer, Credit Card, SEPA Direct Debit); unknown → manual review.
- Billing ≠ delivery → second address with the Delivery role (Phase 3).
- Currency: locale-aware money parser set by preflight; never change the app's locale (Phase 3). The user sets the workspace currency; `run` stops for review on a mismatch unless `--accept-currency`.
- When unsure, stop for manual review; never guess or fuzzy-match identifiers.

## Conventions

- `src/fakturama_agent/` package layout; tests in `tests/`, pytest.
- Secrets in `.env` (`GEMINI_API_KEY`), never committed. `.cache/` and `out/` are git-ignored.
- Offline tests must not hit the network; live tests are marked `-m live`.
- Commit in small steps with clear messages.
