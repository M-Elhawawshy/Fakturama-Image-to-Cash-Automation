# Fakturama Image-to-Cash automation

One order image in, a saved and verified **Order** plus its **linked, paid Invoice** out, in Fakturama 2.2.0 on Windows. The image is read by a vision LLM with an OCR cross-check; Fakturama is driven through **Microsoft UI Automation** (pywinauto), with no hardcoded coordinates. Every write is read back, every Save happens once, and anything ambiguous stops the run for manual review instead of guessing.

```
samples/order_001.png ──extract──▶ out/order_001/order.json (validated OrderSpec) ──run──▶ Fakturama: master data, Order, Invoice

automate IMAGE = extract, then run (run is skipped if extraction needs review)
```

**Status:** the whole brief is implemented and has run end to end on the sample: payment term, products and Debtor created when missing, Order PO000007 saved (lines 450.00 / 120.00, totals 570.00 / 108.30 / 678.30), linked Invoice INV000004 saved as paid on 2026-07-18 with Value 678.30, all verified in Data > Documents. That run was made before the currency check existed, in a workspace set to US dollars, so Fakturama shows these EUR amounts with a $ sign (see [Currency](#currency-set-by-you-not-by-the-tool)). 753 offline tests. What differs from the brief and what is not covered is listed under [Deviations and limits](#deviations-and-limits).

## Setup

**Fakturama**
- Fakturama **2.2.0**, **English** UI, Windows, display scaling 100%. Start it yourself and open the workspace; the tool never launches it.
- File > Preferences > Documents: *immediately take over a clearly found item number* must be **off** (preflight checks it).
- **Currency: set it yourself.** Fakturama 2.2.0 has one currency per workspace (File > Preferences > General > *Currency locale*); documents have no currency of their own. The tool only reads this setting and never changes it. For the sample, which is in EUR, choose a euro country (e.g. *Germany*) before `automate`/`run`, or pass `--accept-currency` (see below).
- Back up the workspace folder before a run that creates records (copy it; restore by copying back).

**Python**
- Python 3.12 and [uv](https://docs.astral.sh/uv/).
- `uv sync` installs the dependencies (pywinauto 0.6.9, google-genai, RapidOCR, pydantic, PyYAML).
- Copy `.env.example` to `.env` and set `GEMINI_API_KEY` (only needed for `extract`/`automate`; responses are cached in `.cache/`, so the sample does not call the API again).

**While a run is in progress:** Fakturama maximized and in front, hands off mouse and keyboard, no other editor tabs with unsaved changes. Desktop notifications that pop up over Fakturama (seen: a game launcher's "friend is playing" toast) make the table reader wait until they are gone.

## Run it

One command does the whole task, image to paid Invoice:

```
uv run python -m fakturama_agent automate samples/order_001.png --without-email
```

`automate` runs the two stages below in a row. Fakturama is only touched if extraction passed every check; otherwise it stops after writing `review.md` (exit 2). The OrderSpec goes to `out/<image name>/order.json` (`-o` to change it).

The stages can also be run separately, e.g. to look at or correct the OrderSpec in between:

```
uv run python -m fakturama_agent extract samples/order_001.png -o out/order.json
uv run python -m fakturama_agent run out/order.json --without-email
```

`extract` writes the `OrderSpec`, or a `review.md` and exit code 2 when a check fails. `run` prints one line per step and writes `out/runs/<time>/` with `report.md`, `journal.jsonl`, before/after screenshots of every step and a UIA dump of the window when it stops. Exit codes (`run` and `automate`): 0 done, 2 stopped for review, 1 usage or operational error.

The flags below apply to `automate` and `run` alike.

`--without-email`: Fakturama rejects the sample's `marta.klein@example.test` (*Please enter a valid e-mail address*). Without the flag the run stops for review before saving the Debtor; with it the Debtor is saved with an empty E-Mail and the report says so.

`--accept-currency`: `run` compares the order's currency (EUR in the sample) with the workspace currency and stops for review when they differ. With the flag it goes on and enters the amounts as plain numbers; Fakturama then shows them in the workspace currency (e.g. `$678.30` for an EUR order). The report records that the mismatch was accepted.

Other flags: `--allow-duplicate` (create another Order and Invoice although one carries the Cust.Ref.), `--checked` (you inspected Fakturama after an earlier run stopped right after a Save).

**Single steps** (each runs preflight first; most are read-only):

| Command | What it does |
|---|---|
| `preflight` | window, version, English UI, no unsaved editors, preferences, currency format |
| `read-table documents\|invoices\|debtors\|products\|vats\|payments` | a Data view as a table (read-only) |
| `check-master SPEC` | VAT rates and payment term reusable or missing (read-only) |
| `create-master SPEC [--without-email]` | payment term, VAT, products, Debtor; each only if missing |
| `create-order SPEC [--no-save]` | the Order: header, selections, lines, totals, Save once, Documents |
| `invoice-open SPEC` | the linked Invoice with payment set, left unsaved |
| `create-invoice SPEC [--no-save]` / `check-invoice SPEC` | save and verify the Invoice / verify only |
| `inspect`, `locate` | dump the UIA tree, resolve the locators (for development) |

**Tests:** `uv run pytest` runs the offline suite (no network, no Fakturama). `-m live` calls Gemini; `-m uia` needs a running Fakturama.

## How it works

**Extraction** (`extract/`, `spec/`). Gemini Flash reads the image into a fixed JSON schema and copies values verbatim; code normalizes them (ISO dates, `Decimal` money, closed enums for payment method and paid status) and derives everything else (gross product price, VAT name, payment code, expected line nets). RapidOCR re-reads the image, and every critical value must be found character for character; the arithmetic must hold (lines, net, VAT, gross). Any failure produces `review.md` instead of an `OrderSpec`.

**Finding controls** (`uia/`). Controls are found in the UIA tree by name and structure from one registry, `uia/locators.yaml` (e.g. "the Edit after the label *ZIP - City*", "the Invoice button inside *Create a follow-up document*"), scoped to the window or editor the run has bound by its native handle. A locator that matches zero or several elements is an error, never a guess. Clicks go to the centre of a UIA rectangle after a hit test confirms the element is really under the pointer. Text is typed with real key events and read back after focus leaves the field; dropdowns are opened and the exact entry clicked.

**Tables** (NatTable exposes no rows to UIA). The grid's pixels decide whether it is empty and which rows are highlighted; the header is checked by OCR; the rows are copied with Select All + Copy and accepted only if the clipboard was freshly written by Fakturama and two copies agree. Copy is only sent when exactly the expected rows are highlighted, because Copy without a selection crashes Fakturama 2.2.0. Line cells are edited through NatTable's inline editors, located from the header separators.

**The run** (`flow/`). Explicit steps with outcomes ok / create / review / stop:
1. gates: spec checks, journal, preflight, currency (order vs workspace), Error.log baseline, duplicate guard on Cust.Ref.;
2. master data, check first and create only when missing, in a safe order: payment term (the Debtor editor reads the list once), VAT, products (VAT chosen before the gross price, which it recalculates), Debtor (billing address with the Invoice role, delivery address with the Delivery role, alias, Net, payment);
3. the Order: header, Debtor and products picked in the Order's own selector dialogs (exact match on Company/First Name/Name/ZIP/City and Item No.), lines, totals, addresses, Save once, Documents row;
4. the linked Invoice from the Order's follow-up area: copied fields confirmed, payment method, paid + payment date + Value = Total (unpaid: nothing touched), Save once, Invoice row, Order still open, Invoice reopened and read back.

Every saved record is read back (for the Debtor, all 25 fields). A Save is never repeated: the journal records `attempting`/`done`, and a run that stopped right after a Save blocks the next run for that Cust.Ref. until `--checked`. Reruns are idempotent because every step checks first; an Order without an Invoice is resumed with the Invoice only. A `FakeFakturama` simulator implements the same step interface, so the decision tree is tested offline.

Design and decisions: [docs/DESIGN.md](docs/DESIGN.md); per-phase plans with everything observed live: [docs/PHASE3_PLAN.md](docs/PHASE3_PLAN.md), [docs/PHASE4_PLAN.md](docs/PHASE4_PLAN.md).

## Evidence

Annotated screenshots from the runner's own evidence (personal paths blurred), produced with `tools/annotate_screenshots.py`:

1. [Product created](docs/screenshots/1-product-created.png): CHR-ERG-01, gross 297.50 = 250.00 × 1.19, VAT 19%.
2. [Debtor created](docs/screenshots/2-debtor-created.png): Northstar Office GmbH / Marta Klein.
3. [Order and paid Invoice](docs/screenshots/3-order-and-invoice.png): PO000007 with both lines and totals; INV000004 paid, 678.30.

![Order and its paid Invoice](docs/screenshots/3-order-and-invoice.png)

On the cleaned workspace the full result took three runs: the first two stopped on a desktop notification that covered the bottom rows of a Data view (the reader refused the picture; the second run had already created the payment term, both products and the Debtor, which the third run then reused before saving the Order and Invoice). The reader now waits for such an overlay to disappear (up to 10 s) and keeps a screenshot of it.

## Deviations and limits

- **Order of operations.** The brief keeps the New Order open and creates missing master data from there, returning to the same Order. `run` settles master data first (checking the Data views), then opens the Order and selects everything through the Order's own selector dialogs, which still serve as the final existence check. Reason: every editor then sees fresh lists (the Debtor editor reads the payment list only once), and an Order is never left half-built while other editors save. The existence checks, exact-match rules and create branches are the brief's; only the moment differs.
- **E-mail.** Fakturama rejects the `.test` domain; the Debtor is saved without E-Mail only with `--without-email`, otherwise the run stops for review.
- <a id="currency-set-by-you-not-by-the-tool"></a>**Currency: set by you, not by the tool.** Fakturama 2.2.0 keeps one currency for the whole workspace, so an Order cannot be "in EUR" on its own. The automation never changes that setting: choosing the workspace currency is the user's job. What the automation does:
  - preflight reads the currency format, and every amount read back from Fakturama is parsed in that format (`$-1,234.57`, `-1.234,57 €`, ...);
  - `run` works out the workspace currency from the locale and its symbol, and stops for review if it differs from the order's currency or cannot be determined;
  - with `--accept-currency` it enters the amounts as plain numbers anyway, and Fakturama labels them with the workspace currency.

  The sample's live runs used a US-dollar workspace (before this check existed), so the EUR amounts appear with `$`. With the workspace set to *Germany*, the check reads EUR live. The first euro run then stopped before saving the first product: the gross price was typed as `297.50`, which Fakturama reads in that locale as 29.750,00 €, and the read-back caught it. Money fields (a product's gross price, the Invoice's paid Value) are now typed in the workspace's format (`297,50`); line cells and the VAT value take a dot in every locale. Each field was checked live in unsaved editors; a full euro run has not been recorded yet.
- **Tested environment.** English Fakturama 2.2.0, US currency format (full runs) and German currency format (field by field, see Currency), 100% display scaling, a maximized window, one sample image. Other UI languages, and currency formats the money reader cannot pin down, are refused by preflight; a date display other than `MMM d, yyyy` stops the run; other display scaling is untested.
- **Not exercised live:** an UNPAID invoice saved (the unpaid branch was run unsaved; its Documents state is only required to not be *paid*), Credit Card and SEPA terms, VAT rates other than 19% in an order (0% needs an exemption code and is refused), more item lines than the editor shows without scrolling (the line editor then stops).
- **Keyboard layout.** Ctrl shortcuts are sent as virtual keys and were verified with an Arabic layout active; digits map to the same keys in that layout, but full runs were done with the English layout.

## Written questions

**1. If you had 3 more hours, what would you do for this task?**

In this order:
1. **Follow the brief's Order-first sequence literally** as an option of `run`: open the Order, try its selectors, and branch into creation from there before returning to the same Order. All building blocks exist (selector checks, create steps, binding by window handle); what is missing is keeping the Order bound while other editors save, and tests for that path in the simulator.
2. **More real inputs.** Run extraction on a set of varied order images (other layouts, photos, an unpaid one, Credit Card and SEPA, two VAT rates, more lines) and turn the results into golden tests; then run each live, including saving an unpaid Invoice to record its Documents state.
3. **Robustness of the desktop session.** Detect focus thieves and notifications up front (preflight), support scrolling in the item grid for long orders, and run in a dedicated Windows session or VM so nothing can cover Fakturama.
4. **Packaging.** Batch mode for `automate` (a folder of images, one report), a short screen recording of a clean run, and a summary page that links each run's evidence.

## Notes

Known Fakturama pitfalls were cross-checked once against a public attempt at this task; all were re-verified live, and the implementation is independent.
