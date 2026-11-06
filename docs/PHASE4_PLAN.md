# Phase 4 — Linked Invoice and payment status

**Goal:** after `run` has saved and verified the Order, create the **linked** Invoice from the Order's follow-up area, confirm what it copied, set the payment method and paid status from the `OrderSpec`, Save once, and verify both the Invoice and the still-open source Order in Data > Documents (brief steps 4.6–5.7). The flow ends there: no Delivery, Correction or Dunning document.

**Approach:** as in Phase 3, small parts (A–E), each a committed command with offline tests and a live check. Parts A–C never save; D and E save one Invoice per run (back up the workspace first).

## Brief requirements (4.6–5.7)

- 4.6/4.7: from the saved Order's *Create a follow-up document* area click **Invoice** (not the toolbar's Invoice button: only the follow-up keeps the Order relationship); wait for the linked *New Invoice* editor.
- 5.1: leave the proposed Invoice **No.**, **Invoice Date** and **Service date** unchanged; confirm Cust.Ref., Invoice address, Delivery address, Order Date, VAT mode, item lines and totals were copied from the Order.
- 5.2: the Invoice payment method must equal the extracted method; if it is not available, stop for manual review.
- 5.3: PAID → tick **paid**, set the payment date to the extracted date and **Value** to the full Invoice Total. Not PAID → leave paid clear, invent no date or value.
- 5.4: toolbar Save, once.
- 5.5: Data > Documents: the Invoice row has the expected state and Total, and the source Order is still **open** with the same Cust.Ref. and Total.
- 5.6: reopen the Invoice only if needed to confirm the persisted method, paid state, date and Value.

## Recorded so far (2026-11-06, read-only; nothing saved)

On saved Order PO000006 the follow-up toolbar is **enabled** (it was disabled on the unsaved Order): Buttons `Confirmation`, `Invoice`, `Delivery`, `Proforma`, all with Invoke. Invoking `Invoice` opened an editor Pane **`New Invoice`** with its own window handle (tab `*New Invoice`, dirty at once). Recorded in `tests/fixtures/uia/invoice_editor.json` and `invoice_editor_paid.json`:

| Field | Shown | Note |
|---|---|---|
| No. | `INV000001` | proposed; first Invoice of the workspace |
| Date | `Nov 6, 2026` | today, segmented date; left unchanged |
| price mode | `Net` | ComboBox after Date, as in the Order |
| Cust.Ref. | `WEB-2026-0714-A17` | copied |
| Service date | `Nov 6, 2026` | today; left unchanged |
| Order Date | `Jul 14, 2026` | copied from the Order |
| VAT | `With VAT` | copied |
| Invoice address | `Northstar Office GmbH / Marta Klein / Friedrichstrasse 88 / DE-10117 Berlin / Germany` | tab `Delivery address` present |
| Items | grid like the Order's (five icons beside `Items`) | lines to be read with the table reader |
| Total Net / VAT / Total | `$570.00` / `$108.30` / `$678.30` | copied |
| Discount / Shipping | `0%` / `Free of shipping costs` `$0.00` | |
| payment | unnamed ComboBox `Bank Transfer` | prefilled from the Debtor; lists term descriptions (as the Debtor's Payment does) |
| `paid` | CheckBox, Toggle, off | |
| unpaid area | `Due Days` Spinner `0`, `Pay Until` date | shown while paid is off |
| paid area | `at` segmented date (default today), Edit `Value` `$678.30` | shown once paid is ticked (UIA Toggle works) |
| follow-up (Invoice) | `Delivery`, `Invoice Correction`, `Dunning`, disabled | never used (5.7) |

Closing the unsaved Invoice through `Save Parts` (untick, OK) discarded it; Documents still has no Invoice category (`This transaction`, the contact, `Orders`).

## Parts

### A. Locators and recordings (read-only)

Invoice editor locators from the recordings: `invoice.no`, `invoice.date`, `invoice.service_date`, `invoice.order_date`, `invoice.cust_ref`, `invoice.vat_mode`, `invoice.price_mode`, `invoice.payment` (the unnamed ComboBox in the payment area), `invoice.paid`, `invoice.paid_date` (after `at`), `invoice.paid_value` (`Value`), `invoice.due_days`, `invoice.pay_until`; `order.followup.invoice` (Button `Invoice` inside Group `Create a follow-up document`; never the toolbar's). Totals/addresses reuse the `order.*` locators (same layout). Tests resolve every locator on both recordings.

### B. Open the linked Invoice and confirm the copy (unsaved)

Find the saved Order by Cust.Ref. in Documents (exactly one, verified as in `check-order`); open its editor, or bind it when its tab is already open (seen: PO000006 was still open from the run); invoke the follow-up `Invoice`; bind the new `New Invoice` pane by handle. Read and compare (5.1): No. matches `INV\d+` (kept), Date and Service date not touched, Cust.Ref., Order Date = spec date, VAT `With VAT`, Net, invoice address, delivery address (Delivery tab, then the Invoice tab again: the Order's Delivery-tab Save crash may apply to Invoices too), lines (table reader, `lines_problems`), footer (`footer_problems`). Command `invoice-open SPEC` leaves the editor unsaved.

**Live check:** `invoice-open out/order.json` reports every copied field ok; nothing is saved; you discard the New Invoice.

### C. Payment method and paid status (unsaved)

- Method (5.2): the dropdown lists term descriptions; select the description of the verified term (part F's check, which also reads the description). Already showing it → untouched. Not listed → review.
- PAID (5.3): tick `paid` (Toggle, read back), enter the payment date in the `at` field with the segmented-date routine (part D of Phase 3), check `Value` equals the Invoice Total and the spec's gross (type it only if it differs).
- UNPAID: `paid` must be off; `at`/`Value` are not touched; nothing is typed.
- Everything read back. `invoice-open SPEC` gains these steps, still unsaved.

**Live check:** `invoice-open` on the sample shows paid ticked, at `Jul 18, 2026`, Value `$678.30`, Bank Transfer; with a test spec whose status is UNPAID, paid stays clear.

### D. Save once and verify (persists the Invoice)

Select the Invoice address tab; journal `attempting invoice`; toolbar Save once (any dialog stops, never retried); the editor is renamed to its number. Then Documents: find the Invoice row (category to be recorded on the first Save; `This transaction` may show Order and Invoice together), check number, Cust.Ref., Total and state; check the source Order row is still `open` with the same Cust.Ref. and Total. Reopen the Invoice (5.6) and read method, paid, at, Value. Commands `create-invoice SPEC [--no-save]` and read-only `check-invoice SPEC`.

**Live check:** INV00000N in Documents with total 678.30 and the paid state; PO00000N still open; the reopened Invoice shows Bank Transfer, paid, Jul 18 2026, $678.30.

### E. Into `run`

A step `invoice` after the Order (journal, evidence, Error.log watch), the simulator extended (Invoice created / exists / paid vs unpaid / failure after the Invoice Save), and resume rules (decision 4).

**Live check:** on a clean workspace `run out/order.json --without-email` ends with the Order and its paid Invoice; a second run stops at the duplicate guard.

## Decisions (proposed; confirm before part D)

1. **Value = the Invoice's own Total**, which must also equal the spec's gross; any difference is review (never typed to force a match).
2. **Payment method by description**: the dropdown shows term descriptions; the description comes from the verified term, so a renamed or missing term is review.
3. **UNPAID touches nothing** in the payment area beyond confirming the method and that paid is clear.
4. **Resume**: if the Order for the Cust.Ref. exists (verified, exactly one) but no Invoice is linked to it, `run` continues with the Invoice only instead of stopping at the duplicate guard; if an Invoice for the Cust.Ref. exists, that is the duplicate guard (review).
5. **Dates**: Invoice Date and Service date stay as proposed (today), per 5.1; only the paid date is entered.

## Open points to record live

- Documents category and columns for Invoices; the state text of a paid and of an unpaid Invoice (one earlier note says an unpaid Invoice may show `COMMAND_ERROR`).
- Whether saving the Invoice changes the Order's state (the brief expects it to stay open).
- The payment dropdown's full list; whether choosing an entry changes `Due Days`/`Pay Until`.
- Any dialog on Invoice Save.
