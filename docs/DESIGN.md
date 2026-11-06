# Fakturama Image-to-Cash Automation: Design

Written and approved before implementation (Nov 5, 2026); updated to match what was built. Where the build differs from the first design, the section says so and why. Everything observed live is recorded in the phase plans ([PHASE3](PHASE3_PLAN.md), [PHASE4](PHASE4_PLAN.md)).

## Goal and scope

One order image becomes a saved, verified Order and a linked, paid Invoice in Fakturama 2.2.0. The system has two decoupled halves: an **extractor** turns the image into one validated `OrderSpec` JSON file, and a **UIA driver** operates Fakturama from that file alone. Nothing downstream looks at the image again.

**In scope:** every field the brief names; Debtor, payment term, VAT rate and Product found or created; the Order; the linked Invoice via *Create a follow-up document*; paid status; a read-back check of every step.

**Out of scope:** Delivery, Correction and Dunning documents; batch processing; headless or minimized operation; non-English UIs (labels are data, but only English 2.2.0 is targeted).

**Guiding rule:** when unsure, stop for manual review instead of guessing. A wrong invoice is worse than an unfinished one.

## Architecture and the OrderSpec contract

The `OrderSpec` file is the only interface between the halves. It is schema-validated and can be hand-edited, so each half can be built, tested and rerun alone.

1. **Extract**: image → raw fields (vision LLM, cross-checked by OCR).
2. **Normalize and validate**: raw fields → `OrderSpec` with typed and derived values and an arithmetic check. Any failure ends here with `review.md`.
3. **Preflight**: one Fakturama main window, version 2.2.0, English labels, required preferences, currency format, no unsaved editors, Fakturama in front.
4. **Drive**: explicit steps through a UIA adapter, each verified by reading back.
5. **Report**: journal, before/after screenshots per step, `report.md`.

**Modules:** `extract/` (LLM client, OCR, cross-check, normalizer, validator) · `spec/` (models, derived values) · `uia/` (window binding, locator registry, input, table reader, date input, money format, preflight) · `flow/` (steps, run, journal, simulator) · `cli.py` (`extract`, `run`, `automate` = both, plus single-step commands).

**OrderSpec fields.** Extracted values are kept exactly as printed; derived values are computed in code, never asked of the model.

| Group | Extracted | Derived (code) |
| --- | --- | --- |
| order | external_reference, order_date, customer_id (info only), currency | — |
| debtor | company, contact name, alias, email, phone | first/last name split, `billing_equals_delivery` |
| addresses | billing and delivery: name, street, ZIP, city, country | ISO country, Fakturama country label |
| payment | method, paid_status, payment_date | Fakturama payment code (Bank Transfer → Credit transfer, Credit Card → Credit card, SEPA Direct Debit → SEPA direct debit) |
| items[] | line no., SKU, description, qty, unit, unit net price, discount %, VAT %, printed line net | VAT name (`VAT 19%`), product gross price (net × (1 + VAT/100), 2 dp), expected line net |
| totals | net, VAT, gross | recomputed totals |
| provenance | image SHA-256, model id, per-field OCR status | — |

Money and percentages are `Decimal`, never float. Dates are ISO `YYYY-MM-DD`. Enums are closed: an unknown payment method is a validation error. Paid status is PAID or UNPAID; anything other than PAID is UNPAID.

## Image extraction

A vision LLM reads; OCR and arithmetic check. The LLM handles layout (which value belongs to which label or column); OCR is weak at layout but never paraphrases, so it guards the characters that must be exact.

1. **Pre-process**: fix EXIF rotation, upscale small images, record the SHA-256.
2. **LLM pass**: one Gemini Flash call, temperature 0, with JSON-schema output mirroring the extracted half of `OrderSpec`. The prompt: copy verbatim, do not compute, null for anything absent.
3. **OCR pass**: RapidOCR returns words with boxes; no layout logic is built on it.
4. **Cross-check**: every critical token (reference, SKUs, ZIPs, email, dates, every number) must appear in the OCR text. Unconfirmed is flagged; a conflicting near-match (`0`/`O`, `1`/`l`) fails.
5. **Normalize**: ISO dates, decimals, name split, enums, country names.
6. **Validate**: qty × unit net × (1 − discount/100) = printed line net; lines sum to net; VAT per rate sums to the VAT total; net + VAT = gross (sample: 450.00 + 120.00 = 570.00; × 19% = 108.30; 678.30). Also unique SKUs, required fields, a payment date when PAID.
7. **Emit** `order.json` and a short summary, or `review.md`; the driver never starts on a failed extraction.

OCR alone breaks on any layout change; the LLM alone can silently "fix" a SKU or round a number, which totals cannot catch. LLM, OCR and arithmetic are three independent signals.

## Finding controls (Microsoft UIA)

Every control is found in the UIA tree, scoped to a bound parent, without fixed coordinates. The earlier exploration of Fakturama 2.2.0 (SWT/Eclipse RCP, `agent findings/fakturama-uia-findings.md`) showed where UIA is reliable, so locators come in three tiers:

| Tier | When | How |
| --- | --- | --- |
| 1. Named control | usable Name + ControlType | Edit `Cust.Ref.`, Button `Save the current contents`, ComboBox `VAT` |
| 2. Label-anchored structure | unnamed Edit or icon | the sibling after a Text label: Date after `Date`, the first Image after `Addresses` (address selector) |
| 3. Inside a UIA rectangle | content not exposed (NatTable rows, CDateTime segments) | grid pixels for rows and selection, clipboard for content, OCR only to check a table header |

**Scoping.** The main window is bound by its handle (title `^Fakturama`, class `SWT_Window0`, exactly one), then the editor pane. Lookups are relative to that pane. Dialogs are searched among all windows of the process.

**Locator registry.** Labels and structural rules live in one file, `uia/locators.yaml`, keyed by logical name (`order.cust_ref`, `invoice.paid_value`). A locator matching zero or several elements is an error. A version or language change edits data, not flow code.

**Input rules** (from observed SWT behaviour):

- Buttons, menus, toolbar items: InvokePattern. An Invoke with an uncertain outcome is never retried.
- Text: focus the Edit, then real keystrokes. ValuePattern.SetValue is not used: it can change the display without updating SWT's model.
- Dropdowns: expand, click the exact ListItem (including oddities such as a trailing space).
- Pattern-less images and tabs: click the centre of the UIA rectangle after a hit test confirms the element is under the pointer; read the state back.
- Dates: CDateTime segments, verified after blur.
- Numbers: money fields (product gross price, Invoice paid Value) are typed in the workspace's format (`297,50` in Germany); NatTable cells and the VAT value always take a dot.

**Tables.** Data views and selector dialogs expose no rows. The reader decides emptiness and highlighted rows from the grid's pixels (Copy on an empty grid or without a selection crashes Fakturama), checks the header by OCR, sends Select All + Copy, and accepts the clipboard only if Fakturama wrote it fresh and two copies agree. Unreadable is manual review, never "zero rows". Matching is exact and case-sensitive. *(The first design read tables by screenshot OCR; Select All + Copy proved exact.)*

**Waits.** Every action waits on a condition (dialog present, editor bound, list stable across two captures) with a timeout.

**Money.** Preflight reads Preferences' currency example (`$-1,234.57`, `-1.234,57 €`) and builds the format every displayed amount is parsed with. The locale is never changed.

## Workflow

Steps with outcomes **ok**, **create** (a record was saved), **review** or **stop** (both end the run and leave Fakturama as it is):

1. **Gates**: spec (every critical value OCR-confirmed), journal (no Save of unknown outcome left by the previous run), preflight, currency (the order's currency equals the workspace's), Error.log baseline, duplicate guard on Cust.Ref.
2. **Master data**, check first, create only when missing: payment term, VAT rates, products (VAT chosen before the gross price, which it recalculates), Debtor (billing address with the Invoice role, delivery address with the Delivery role, alias, Net, payment term).
3. **Order**: header (Date, Cust.Ref., Net, With VAT); Debtor and products picked in the Order's own selector dialogs by exact Company / First Name / Name / ZIP / City and Item No.; lines (Qty, U.Price, Discount); totals and addresses verified; Save once; Documents row checked.
4. **Invoice**: follow-up Invoice from the Order; copied fields confirmed; payment term; paid + payment date + Value = Total (UNPAID: left clear); Save once; Invoice row, Order still open, Invoice reopened and read back.
5. After every step, Fakturama's Error.log must not have grown.

**Changed from the first design.** The first design followed the brief literally: open the New Order and branch into creating a missing Debtor or Product from its selectors, then return to the same Order. The build settles master data first and then builds the Order. Reasons: each editor sees fresh lists (the Debtor editor reads the payment list only once), and no Order sits half-built while other editors save. The Order's selectors still act as the final existence check, and the match rules and create branches are the brief's.

## Failure handling, idempotency and evidence

Fakturama has no transaction across editors: a record saved before a later failure stays saved. Reruns are made safe instead.

- **Check before create.** Every step looks for an exact match first, so a rerun reuses what an earlier run created. An Order without an Invoice is resumed with the Invoice only; an existing Invoice for the Cust.Ref. means the order is done.
- **Journal.** Before a step that may Save: `attempting`; after: `done` with the outcome. A run that ends with an unknown Save outcome blocks the next run for that Cust.Ref. until a person checks Fakturama and passes `--checked`; rerunning without it keeps the block.
- **Manual review is a terminal state.** Fakturama is left as it is (no discard, no auto-dismissed dialogs); the report gives the step, expected vs observed, screenshots and a UIA dump.
- **Unexpected UI** (unknown dialog, duplicate warning, Save Parts prompt, covered grid) is review. A transient overlay such as a desktop notification is waited out (up to 10 s) and recorded.
- **Simulator.** `FakeFakturama` implements the step interface with injectable failures, so the whole decision tree is tested offline.

## Tradeoffs and risks

| Decision | Chosen | Alternative | Why |
| --- | --- | --- | --- |
| Extraction | Vision LLM + OCR cross-check + arithmetic | OCR + templates | layout-robust, exact tokens independently confirmed |
| UI control | Deterministic UIA, pixels/clipboard where UIA is blind | screenshot-driven vision agent | repeatable, auditable, cheap |
| Text input | Real keystrokes | ValuePattern.SetValue | SetValue does not persist in SWT |
| Ambiguity | Stop for review | best-guess match | a wrong Debtor or Product is costly to undo |
| Sequence | Master data, then Order | Order-first with create branches | fresh lists in every editor, no half-built Order |
| Language | Python + pywinauto (UIA) | Go + raw UIA COM | the exploration had proved this stack |

**Risks.** Fakturama must stay visible and in front; anything covering it pauses or stops the run. The table reader overwrites the clipboard. Labels are tied to English 2.2.0. Long orders need item-grid scrolling, which is not implemented (the run stops).

## Decisions

- Language: **Python 3.12** with uv.
- Billing ≠ delivery: a **second address with the Delivery role**.
- Paid status: anything other than PAID is **UNPAID** (paid left clear, no date or value).
- Currency: the workspace currency is **set by the user**; the tool reads it, parses and types amounts in its format, and stops for review when it differs from the order's (`--accept-currency` waives that). The locale is never changed.
- LLM: **Gemini Flash**, behind a small provider interface.

## Phases

| Phase | Output | State |
| --- | --- | --- |
| 1. Design | this document | done |
| 2. Extraction | `OrderSpec` schema, `extract`, validator, golden tests | done |
| 3. Order flow | preflight, UIA adapter, table reader, master data, saved Order, `run` | done |
| 4. Invoice and payment | follow-up Invoice, payment term, paid status, final verification | done |
| 5. Deliverables | README, screenshots, written answer, `automate` | done |
