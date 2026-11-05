# Fakturama Image-to-Cash Automation — Design Doc

Source of truth: https://claude.ai/artifact/8QTNmhh9EaNjGys6N6njaM (this file is an export, Nov 5, 2026).

## Goal and scope

The system turns one order image into a saved, verified Order and a linked Invoice in Fakturama. It runs in two decoupled halves: an **extractor** produces one validated `OrderSpec` JSON file, and a **UIA driver** consumes only that file to operate Fakturama. Nothing downstream ever looks at the image again.

**In scope:** extraction and normalization of every field the brief names; Debtor, payment method, VAT and Product resolve-or-create through the Order's own selectors; Order completion; linked Invoice via *Create a follow-up document*; paid status; read-back verification of every step.

**Non-goals:** Delivery, Correction or Dunning documents; batch processing of many images; headless or minimized operation; non-English Fakturama UIs (labels are configurable, but only English 2.2.0 is targeted).

**Guiding rule:** when the system is unsure, it stops for manual review instead of guessing. A wrong invoice is worse than an unfinished one.

## Architecture and the OrderSpec contract

The `OrderSpec` file is the only interface between extraction and automation. It is versioned, validated by a schema and can be hand-edited, so each half can be built, tested and re-run alone.

1. **Extract**: image → raw fields (LLM vision, cross-checked by OCR).
2. **Normalize and validate**: raw fields → `OrderSpec` with typed values, derived values and an arithmetic check. Any failure ends the run here with a review report.
3. **Preflight**: confirm one Fakturama main window, version 2.2.0, English labels, required preferences, a free desktop and no dirty editors.
4. **Drive**: a step-by-step state machine (below) acting through a UIA adapter, verifying after every step.
5. **Report**: run journal, per-step screenshots, final verification summary.

**Planned modules:** `extract/` (LLM client, OCR, normalizer, validator) · `spec/` (schema, derived values) · `uia/` (window binding, locator registry, input, waits, table reader, date input) · `flow/` (steps, state machine, journal) · `cli` (`extract`, `run`, `inspect`).

**OrderSpec fields.** Extracted values are kept exactly as printed. Derived values are computed in code, never asked of the model.

| Group | Extracted fields | Derived (code) |
| --- | --- | --- |
| order | external_reference, order_date, customer_id (info only), currency | — |
| debtor | company, contact first and last name, alias, email, phone | contact split, `billing_equals_delivery` |
| addresses | billing and delivery: name, street, ZIP, city, country | ISO country, Fakturama country label |
| payment | method, paid_status, payment_date | Fakturama payment code (Bank Transfer → Credit transfer, Credit Card → Credit card, SEPA Direct Debit → SEPA direct debit) |
| items[] | line no., SKU, description, qty, unit, unit net price, discount %, VAT %, source line net | VAT name (`VAT 19%`), product gross price (net × (1 + VAT/100), 2 dp), expected line price |
| totals | net, VAT, gross | recomputed totals and a match flag |
| provenance | image SHA-256, model id, per-field source and agreement | — |

Money and percentages are decimal strings, never floats. Dates are ISO `YYYY-MM-DD`. Enums are closed: an unknown payment method is a validation error, not a free-text value. Paid status normalizes to PAID or UNPAID; any value other than PAID becomes UNPAID.

## Image extraction strategy

A vision LLM does the reading; OCR and arithmetic check it. The LLM handles layout (which value belongs to which label, which column a number sits in). OCR is weak at layout but does not paraphrase, so it guards the characters that must be exact.

1. **Pre-process**: load the image, fix EXIF rotation, upscale small images, record the SHA-256.
2. **LLM pass**: one Gemini Flash vision call with a strict JSON-schema structured output that mirrors the extracted half of `OrderSpec`. Temperature 0. The prompt says: copy values verbatim, do not compute, return null for anything absent, never infer a value.
3. **OCR pass**: a local OCR engine (RapidOCR) returns words with boxes. No layout logic is built on it.
4. **Cross-check**: every identity-critical token (external reference, SKUs, ZIPs, email, dates, every number) must appear in the OCR text after whitespace normalization. A token the OCR cannot confirm is flagged; one with a conflicting near-match (e.g. `0`/`O`, `1`/`l`) fails the run.
5. **Normalize**: parse dates to ISO, numbers to decimals (`250.00`, `10%` → `10`), split the contact name into first and last, map payment method and paid status to their enums, map country names.
6. **Validate**: for every line, qty × unit net × (1 − discount/100) equals the printed line net; lines sum to net total; per-rate VAT sums to VAT total; net + VAT equals gross. On the sample: 450.00 + 120.00 = 570.00, 570.00 × 19% = 108.30, gross 678.30. Also: SKUs unique, required fields present, payment date present when status is PAID.
7. **Emit** `order.json` plus a short human-readable summary. Any failure in steps 4–6 emits a review report instead and the driver never starts.

Why not OCR alone: rule-based field mapping breaks on any layout change. Why not the LLM alone: a model can silently "fix" a SKU or round a number, and the totals check alone cannot catch a wrong SKU. The two together, plus arithmetic, give three independent signals.

## Control discovery and grounding (Microsoft UIA)

Every control is found through the UIA tree, scoped to a bound parent, with no fixed coordinates. The earlier exploration of Fakturama 2.2.0 (SWT/Eclipse RCP, see `agent findings/fakturama-uia-findings.md`) shows where UIA is reliable and where it is not, so the adapter uses three locator tiers in order:

| Tier | When | How |
| --- | --- | --- |
| 1. Named control | Control has a usable Name + ControlType | e.g. Edit `Cust.Ref.`, Button `Save the current contents`, TabItem `New Order`, ComboBox `VAT` |
| 2. Label-anchored structure | Edit or icon is unnamed | Find Text label, then its sibling by position in the parent: Date next to `Date`; first Image after Text `Addresses` = address selector; first Image after Text `Items` = product selector |
| 3. Visual fallback inside a UIA rectangle | Content not exposed (NatTable rows, CDateTime segments) | Screenshot only that control's current UIA rectangle, OCR it, act at a point computed from that rectangle |

**Scoping.** Bind the main window (title `^Fakturama`, class `SWT_Window0`, exactly one) and then the specific editor tab instance. Every lookup is relative to that editor pane, never global indexes. Dialogs (`Select the address`, `Select a product`) may be nested under the main window, so the search covers both top-level and descendant windows of the process.

**Locator registry.** All labels and structural rules live in one data file (`locators.yaml`) keyed by logical name (`order.cust_ref`, `order.addresses.select_icon`). Version or language changes edit data, not flow code.

**Input rules** (from the observed SWT behavior):

- Prefer InvokePattern for buttons, menus and toolbar items. Never mouse-retry an Invoke whose outcome is uncertain.
- Text: focus the UIA-located Edit, then real keystrokes (Ctrl+A, Backspace, type). Do not trust ValuePattern.SetValue; it can change the display without updating SWT's model.
- Dropdowns: expand, then click the exact ListItem. Check for exact text, including oddities like the trailing space in `Credit transfer `.
- Images and tabs that reject patterns: click the centre of their current UIA rectangle after checking structure, then read the selected state back.
- Dates: segmented CDateTime; fill month, day and year segments and verify the displayed value after blur.

**Tables.** Selector and Documents tables expose only a pane. The table reader screenshots that pane, detects the ruled grid, OCRs the required columns and returns rows only if the list is complete: unscrolled, a blank terminal row visible, every cell legible. Otherwise the result is "unreadable" (manual review), never "zero matches". Matching is exact and case-sensitive.

**Waits.** No fixed sleeps. Each action waits on a condition (dialog present, tab title gains `*`, list stable across two reads) with a timeout.

**Verification.** Every write is read back after blur, and again after Save or reselection where it matters (e.g. a new Debtor is proven saved only when the Order's selector finds it).

## Workflow state machine

The driver runs the brief's Order-first flow as explicit steps. Each step has a precondition (the right editor or dialog is bound), one action, and a read-back check. Its result is one of three outcomes: **ok** (go on), **create** (enter the branch, then return to the same Order tab), or **review** (stop).

1. Preflight — version, preferences, idle desktop
2. Open New Order, set header — Date, Cust.Ref., Net, With VAT
3. Find Debtor in *Select the address* — exact Company, First Name, Name, ZIP, City
   - no exact row → **Create Debtor** (addresses, alias, Net mode, payment method; add the payment method if missing) → save → reselect from the Order
4. For each item: find SKU in *Select a product* — exact Item No.
   - no exact SKU → **Create Product** (ensure the exact VAT row exists first; gross = net × (1 + VAT/100)) → save → reselect from the Order
5. Complete the item line — Qty, U.Price, Discount; check line price. Loop to 4 for the next item.
6. Verify totals, save Order — check its row in Data > Documents
7. Create linked Invoice — follow-up Invoice, payment method, paid status, save, verify

Any ambiguous, conflicting or unreadable result at any step → **Manual review (stop)**: UI left as is, report written.

The Order tab stays open throughout. Each create branch ends by searching the Order's selector again, which also proves the new record was saved.

## Failure handling, idempotency and evidence

Fakturama has no transaction across editors: a Debtor or Product saved before a later failure stays saved. The design accepts this and makes re-runs safe.

- **Selectors as existence checks make re-runs idempotent.** A second run finds the Debtor, VAT or Product created by the first and selects it instead of creating a duplicate.
- **Run journal.** Before each Save the journal records `attempting <step>`, and after verification `done <step>`. A Save whose outcome is uncertain is never repeated blindly; the run stops and the next run starts by checking Data views for what exists.
- **Manual review is a terminal state, not an exception.** It leaves Fakturama exactly as it is (no discard, no auto-dismissed dialogs) and writes the reason, the step, expected vs observed values and a screenshot.
- **Unexpected UI** (unknown dialog, duplicate-contact warning, `Save Parts` prompt, focus stolen by another app) triggers manual review. Restoring focus is allowed; repeating an uncertain input is not.
- **Evidence.** Per step: before/after screenshots of the bound editor, the values read back, and a UIA subtree dump on failure.

## Tradeoffs and risks

| Decision | Chosen | Alternative | Why |
| --- | --- | --- | --- |
| Extraction | Vision LLM + OCR cross-check + arithmetic | OCR + regex templates | Layout-robust, yet exact tokens are independently confirmed |
| UI control | Deterministic UIA with scoped OCR fallback | Screenshot-driven vision agent | Repeatable, auditable, cheap |
| Text input | Real keystrokes into focused Edits | ValuePattern.SetValue | SetValue was observed not to persist in SWT |
| Ambiguity | Stop for manual review | Best-guess match | A wrong Debtor or Product on an invoice is costly to undo |
| Language | Python + pywinauto (UIA backend) | Go + raw UIA COM | Exploration already proved this stack |

**Risks.** The app must stay visible and in the foreground. Table reading depends on OCR of a rendered grid and on the currency locale. Labels are tied to English 2.2.0. Invoice, paid state and payment date were not explored yet; Phase 4 starts with a fresh UIA tree inspection.

## Decisions (resolved open questions)

- Language: **Python** (confirmed).
- Billing ≠ delivery address: create a **second address with the Delivery role**.
- Paid status: anything other than PAID is treated as **UNPAID** (paid left clear, no date or value).
- Currency: a **locale-aware money parser** set by preflight; the app's locale setting is left unchanged.
- LLM: **Gemini Flash** (free tier for testing), behind a small provider interface so the model can be swapped.

## Phased delivery plan

| Phase | Output | Done when |
| --- | --- | --- |
| 1. Design | This doc | Reviewed and open questions answered — **done** |
| 2. Extraction | `OrderSpec` schema, `extract` CLI, validator, unit tests | Sample image yields a valid `order.json` matching a hand-written golden file; corrupted variants are rejected |
| 3. Order flow (UIA) | Preflight, UIA adapter, table reader, steps up to a saved Order | Order saved and verified in Data > Documents |
| 4. Invoice and payment | Follow-up Invoice, payment method, paid status, final verification | Invoice row shows the expected state and total; source Order still open |
| 5. Packaging | README, setup steps, annotated screenshots or recording, written answers | A fresh machine can run it from the README |
