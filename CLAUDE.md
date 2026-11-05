# Fakturama Image-to-Cash automation (TJM Labs take-home)

Turn one order image into a saved, verified Order + linked Invoice in Fakturama 2.2.0 (English, Windows), driven through Microsoft UI Automation. Brief: `docs/assessment_brief.pdf`.

## Read first

- `docs/DESIGN.md` — approved design (architecture, OrderSpec contract, extraction strategy, UIA grounding, workflow, decisions).
- `docs/PHASE2_PLAN.md` — the current phase's plan, scope and golden test data.
- `agent findings/fakturama-uia-findings.md` — observed UIA behavior of Fakturama from an earlier exploration. Only relevant from Phase 3 on, but its rules are binding then.

## Phases

1. Design — done.
2. **Extraction — current.** Image → validated `OrderSpec` JSON. No UIA code.
3. Order flow via UIA. 4. Linked Invoice + payment status. 5. README, screenshots/recording, written answers.

Work only on the current phase unless asked.

## Decisions (do not revisit without asking)

- Python 3.12, `uv`; pywinauto (UIA backend) later for Phase 3.
- Extraction = Gemini Flash vision with JSON-schema output + RapidOCR cross-check + arithmetic validation. The LLM copies values verbatim; code derives everything else.
- Money/percentages are `Decimal`, never float. Dates ISO `YYYY-MM-DD`.
- Paid status: anything other than PAID → UNPAID.
- Payment method enum is closed (Bank Transfer, Credit Card, SEPA Direct Debit); unknown → manual review.
- Billing ≠ delivery → second address with the Delivery role (Phase 3).
- Currency: locale-aware money parser set by preflight; never change the app's locale (Phase 3).
- When unsure, stop for manual review; never guess or fuzzy-match identifiers.

## Conventions

- `src/fakturama_agent/` package layout; tests in `tests/`, pytest.
- Secrets in `.env` (`GEMINI_API_KEY`), never committed. `.cache/` and `out/` are git-ignored.
- Offline tests must not hit the network; live tests are marked `-m live`.
- Commit in small steps with clear messages.
