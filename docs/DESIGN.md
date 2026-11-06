# Fakturama Image-to-Cash Automation: Design

## 1. Goal

The input is a single order image, such as a scanned or photographed purchase order. The output is a saved, verified **Order** and its **linked Invoice** in Fakturama 2.2.0, carrying the payment status taken from the image. Along the way the system resolves or creates the **Debtor**, the **payment method**, each **Product** and its **VAT rate**. It verifies every step before moving to the next and does not rely on hardcoded coordinates or a fixed UI layout.

**Guiding principle: when unsure, stop.** A wrong invoice is worse than an unfinished one. Ambiguous, conflicting or unreadable data never leads to a guess. The run stops for manual review, leaves Fakturama as it is, and explains why.

**Out of scope:** Delivery, Correction and Dunning documents, and Fakturama UIs in languages other than English.

## 2. Architecture

The system has two halves joined by one file:

```
order image ──▶ Extraction ──▶ OrderSpec (validated JSON) ──▶ UI driver ──▶ Fakturama
                 LLM + OCR + arithmetic                         Microsoft UI Automation
```

- **Extraction** reads the image and writes an `OrderSpec`, or a review report if anything fails a check.
- **The UI driver** reads only the `OrderSpec` and operates Fakturama through Microsoft UI Automation (UIA). It never looks at the image.

The `OrderSpec` is the contract between the halves. It is schema-validated and human-readable, so a person can inspect or correct it. Each half can be built, tested and rerun on its own. A single command runs both halves in a row, and the driver starts only if extraction passed every check.

**OrderSpec content.** Values are copied exactly as printed; everything derived is computed in code, never asked of the model.

| Group | Extracted from the image | Derived in code |
| --- | --- | --- |
| Order | external reference, order date, currency | — |
| Debtor | company, contact name, alias, e-mail, phone | first and last name |
| Addresses | billing and delivery: street, ZIP, city, country | whether they are identical, Fakturama's country name |
| Payment | method, paid status, payment date | Fakturama payment code (Bank Transfer → Credit transfer, Credit Card → Credit card, SEPA Direct Debit → SEPA direct debit) |
| Items | SKU, description, quantity, unit, unit net price, discount, VAT %, line total | VAT name (`VAT 19%`), product gross price = net × (1 + VAT/100), expected line price |
| Totals | net, VAT, gross | recomputed totals |

Money and percentages are exact decimals, never floating point. Dates are ISO (`YYYY-MM-DD`). Payment method is a closed list; an unknown method is an error, not free text. Paid status is PAID or UNPAID, and anything other than PAID counts as UNPAID.

## 3. Image-extraction strategy

A vision LLM reads; OCR and arithmetic check it. The LLM is good at layout (which value belongs to which label or column) but can silently "correct" a character. OCR is poor at layout but never paraphrases. Using both, plus arithmetic, gives three independent signals.

1. **Prepare** the image: fix rotation, upscale small images, record a hash for traceability.
2. **LLM pass:** one call to Gemini Flash at temperature 0, with structured JSON output that mirrors the extracted half of the `OrderSpec`. The prompt says: copy values verbatim, compute nothing, and return null for anything absent.
3. **OCR pass:** RapidOCR, running locally, returns the words on the page. No layout logic is built on it.
4. **Cross-check:** every critical value (reference, SKUs, ZIPs, e-mail, dates, every number) must be found in the OCR text character for character. A conflicting near-match such as `0`/`O` or `1`/`l` fails the order.
5. **Normalize:** dates to ISO, numbers to decimals, the contact name split into first and last, payment method and status mapped to their closed lists.
6. **Validate the arithmetic:**
   - every line: quantity × unit net price × (1 − discount) = printed line total;
   - the lines add up to the net total;
   - VAT per rate adds up to the VAT total;
   - net + VAT = gross.

   On the sample: 450.00 + 120.00 = 570.00; 19% of that is 108.30; the gross is 678.30. Required fields must be present, SKUs unique, and a payment date given when the order is PAID.
7. **Result:** either a validated `OrderSpec` or a review report listing each failed check with the expected and observed values.

**Why not OCR alone?** Field mapping by templates breaks on any layout change. **Why not the LLM alone?** A wrong SKU with correct totals passes every arithmetic check, so only an independent reading of the characters catches it.

## 4. Control discovery and grounding

Fakturama is a Java SWT/Eclipse application. Most controls are visible to UIA, but its tables (NatTable) and date fields expose little or nothing. The driver therefore uses three ways of finding a control, in order of preference, and never fixed screen coordinates:

| Tier | Used when | How |
| --- | --- | --- |
| 1. Named control | the control has a usable name and type | e.g. the Edit named `Cust.Ref.`, the Button `Save the current contents` |
| 2. Anchored to a label | the control itself is unnamed | the element that follows a known label, e.g. the field after `Date`, the selector icon next to `Addresses` |
| 3. Inside the control's own rectangle | the content is not exposed (table rows, date segments) | read the table through the clipboard and its pixels; act at a point computed from the control's current rectangle |

**Scoping.** The driver binds Fakturama's main window (exactly one) and then the specific editor tab it opened. Every lookup is relative to that editor, never a global index, so other open tabs cannot be confused with it. Dialogs are found among the windows of the Fakturama process.

**One locator registry.** Every label and structural rule lives in a single data file, keyed by a logical name such as `order.cust_ref` or `invoice.paid_value`. A locator that matches no element, or more than one, is an error and is never resolved by guessing. A Fakturama update or another language changes data, not code.

**Interaction rules:**
- **Buttons and menus:** UIA's Invoke action. If its outcome is uncertain, it is never retried.
- **Text:** focus the field and send real keystrokes. Setting the value directly through UIA can change what is displayed without updating Fakturama's internal model.
- **Dropdowns:** open the list and click the exact entry.
- **Icons and tabs that do not respond to UIA actions:** click the centre of the element's current rectangle, after checking that the element really is under the pointer.
- **Every value is read back** after focus leaves the field.

**Numbers and currency.** Fakturama has one currency per workspace; a document has no currency of its own. The workspace currency is the user's setting, and the driver never changes it. The driver reads its format (for example `1.234,57 €` or `$1,234.57`) once at start-up and uses it to parse every amount Fakturama displays and to type money fields. If the workspace currency differs from the order's, the run stops for review.

**Tables.** Fakturama's tables expose no rows to UIA, so they are read with Select All + Copy. Copy on an empty table, or with nothing selected, crashes Fakturama 2.2.0, so the table's pixels first decide whether it has rows and which are highlighted. The column header is checked by OCR. The clipboard text is accepted only if Fakturama has just written it and two copies agree. A table that cannot be read is treated as manual review, never as "no rows". All matching is exact and case-sensitive.

**Waiting.** No fixed sleeps. Every action waits for a condition (a dialog appears, an editor opens, a list stops changing) with a timeout.

**Preflight.** Before anything is touched, the driver checks:
- exactly one Fakturama window, version 2.2.0, English UI;
- the preference *immediately take over a clearly found item number* is off;
- the currency format can be read;
- no editor has unsaved changes;
- Fakturama is in front.

## 5. Workflow

The run is a sequence of explicit steps. Each has a precondition, one action and a read-back check, and ends with one of four outcomes:

- **ok:** the record exists and was verified;
- **create:** a record was saved and verified;
- **review:** stop, a person must decide;
- **stop:** stop, the UI did not behave as expected.

The steps follow the brief's procedure:

1. **Gates.** The extraction passed; no earlier run left a Save of unknown outcome; preflight passed; the order's currency equals the workspace currency; no Order or Invoice already carries this external reference.
2. **Payment method** (brief 2.10). An exact row in Data > terms of payment is reused; if none exists, the method is created with the mapped payment code. A conflicting definition is review.
3. **VAT rate** (brief 3.4–3.6). One exact row with the right name, value and code S is reused; otherwise it is created. A conflict is review.
4. **Products** (brief 3.7–3.11). Each product is identified by its exact SKU. A missing product is created with the gross price derived above; its VAT is chosen before the price, because changing the VAT recalculates the price.
5. **Debtor** (brief 2.5–2.11). A Debtor matches only on the exact Company, First Name, Name, ZIP and City. A missing Debtor is created with the billing address in the Invoice role. If the delivery address is the same, that address also gets the Delivery role. If it differs, it is added as a second address in the Delivery role. The alias, Net mode and payment method are also set.
6. **Order** (brief 1.3–1.7, 2.1–2.4, 3.2–3.3, 3.13–4.5):
   - Open a New Order, keep the proposed number, and set the date, Cust.Ref., Net and With VAT.
   - Select the Debtor and each Product through the Order's own selector dialogs; these remain the final existence check.
   - Set quantity, unit price and discount on every line and confirm each line price.
   - Confirm both addresses, the 0% overall discount, free shipping and the three totals.
   - Save once, then confirm the Order's row in Data > Documents.
7. **Invoice** (brief 4.6–5.6):
   - Create the Invoice from the Order's *Create a follow-up document* area, which keeps the link to the Order.
   - Confirm the copied reference, addresses, lines and totals.
   - Set the payment method. If PAID, tick *paid* and set the payment date and Value = Total; if UNPAID, leave all three untouched.
   - Save once. Then confirm the Invoice row, that the Order is still open, and the reopened Invoice's payment fields.

After every step, Fakturama's own error log must not have grown.

**Sequence.** The brief keeps the New Order open while missing master data is created, then returns to it. This design settles master data first, using the same exact-match rules, and then builds the Order, still selecting every record through the Order's own selectors. There are two reasons:
- **Fresh lists.** Each editor reads its dropdown lists when it opens. For example, a Debtor editor opened before a new payment method was saved will not offer that method.
- **No half-built Order.** An Order never sits open while other editors save.

The result in Fakturama is the same, and every existence check and creation rule of the brief still applies.

## 6. Failure handling and evidence

Fakturama has no transaction across editors: a Debtor saved before a later failure stays saved. The design makes reruns safe instead of trying to undo.

- **Check before create.** Every step looks for an exact match first, so a rerun reuses what an earlier run created. An Order that has no Invoice yet is resumed with the Invoice step only. An existing Invoice with the same reference means the order is already done.
- **Never repeat a Save blindly.** A journal records each Save attempt and its outcome. If a run ends with a Save of unknown outcome (a crash, or a stop right after a Save), the next run for that order refuses to start until a person has checked Fakturama and confirms it.
- **Manual review is a normal outcome.** Fakturama is left exactly as it is: no discarding, no auto-dismissed dialogs. The report names the step, the expected and observed values, and the evidence.
- **Unexpected UI** (an unknown dialog, a warning, a covered table) leads to review. A transient overlay such as a desktop notification is waited out briefly.
- **Evidence per run:** a step-by-step report, before-and-after screenshots of every step, and a dump of the UI tree when a run stops.
- **Testing without Fakturama.** A simulator implements the same step interface with injectable failures, so the whole decision logic, including crashes after a Save, is tested offline.

## 7. Tradeoffs

| Decision | Chosen | Alternative | Why |
| --- | --- | --- | --- |
| Extraction | vision LLM + OCR cross-check + arithmetic | OCR with layout templates | robust to new layouts, while critical characters are still confirmed independently |
| UI control | deterministic UIA; pixels and clipboard where UIA is blind | a screenshot-driven vision agent | repeatable, auditable, fast and cheap |
| Text input | real keystrokes | setting values through UIA | directly set values are not always taken over by Fakturama |
| Tables | clipboard copy, guarded by pixels | OCR of the table | exact text instead of recognized text |
| Ambiguity | stop for manual review | best-guess match | a wrong Debtor or Product on an invoice is costly to undo |
| Sequence | master data first, then the Order | Order-first with creation branches | every editor sees fresh lists; no half-built Order |
| Stack | Python + pywinauto (UIA) | other languages with raw UIA | mature UIA bindings and fast iteration |

**Risks and limits.**
- **Fakturama must stay visible and in front.** Anything that covers it pauses or stops the run, so a dedicated machine or virtual desktop is the natural deployment.
- **The clipboard is overwritten** while tables are read.
- **Labels are tied to English Fakturama 2.2.0.** Another version or language needs updated locator data.
- **Long orders are not scrolled.** An order with more lines than the item table shows stops for review.
