# Phase 3 — Order flow via UIA (OrderSpec → saved, verified Order)

**Goal:** `python -m fakturama_agent run out/order.json` drives Fakturama 2.2.0 from a validated `OrderSpec` to a saved Order that is verified in Data > Documents (brief steps 1.3–4.5). The linked Invoice and payment status are Phase 4.

**Approach:** ten parts (A–J), each one a working, committed command with its own offline tests and a live check you can do by hand. A part is done only when its live check passes on your machine. Parts A–F persist nothing in Fakturama; G–J create records, so they start with a workspace backup.

Binding inputs: `docs/DESIGN.md`, `agent findings/fakturama-uia-findings.md`, the brief. Known Fakturama 2.2.0 behaviours the parts rely on, with what was observed live here, are listed at the end.

## Starting state (read-only look at the workspace, 2026-11-05)

Workspace: `C:\Users\ma774\OneDrive\Desktop\work\fakturama data TJM Labs` (HSQLDB, inside OneDrive).

| Record | Present | Branch a sample run takes |
|---|---|---|
| Debtors | Learning Labs / Alice Smith, Nimbus Demo / Bob Learner, Orion Study / Dan Taylor | Northstar missing → create |
| Orders | PO000001–PO000003 (earlier exploration) | next proposed No. should be PO000004 |
| Products | none of `CHR-ERG-01`, `MAT-DESK-02` | create both |
| VAT | `VAT 19%`, 0.19, code S | reuse |
| Terms of payment | `Cash` (code `ZZZ`); `Bank Transfer` had no code and was deleted by hand on 2026-11-05 | Bank Transfer missing → create (part G) |

The existing debtors give us a free "existing Debtor" case: a hand-written test OrderSpec for Learning Labs / Alice Smith exercises the select branch without creating anything.

## Stack additions

- `pywinauto` 0.6.9 (UIA backend), Win32 calls via `ctypes` (window enumeration, foreground, clipboard).
- Package `src/fakturama_agent/uia/` (adapter: windows, locators, input, waits, tables, dates) and `src/fakturama_agent/flow/` (steps, state machine, journal). Labels in `uia/locators.yaml`.
- Test markers: offline by default; `-m uia` = needs a running Fakturama, read-only; `-m uia_write` = creates records (only after a backup).

## Parts

### A. Foundation: bind the window, dump the tree (read-only)

- Find Fakturama's process and windows with Win32 `EnumWindows` (a UIA desktop walk can hang on other apps); require exactly one main window `^Fakturama`, class `SWT_Window0`; include nested dialogs.
- `inspect` command: dump a UIA subtree (name, control type, automation id, rectangle, patterns) to JSON, scoped to the main window, a tab, or a dialog.
- These dumps become the **offline fixtures** for every later part (locator tests run against a recorded tree, no Fakturama needed).
- `locators.yaml` with the labels from the findings; a loader that fails loudly on a missing key.

**Live check:** with Fakturama open, `inspect --window main -o out/tree_main.json` lists the toolbar buttons (`Create: New Order`, `Save the current contents`, …). With Fakturama closed, or with two instances open, it exits 1 with a clear message.

### B. Preflight (read-only)

Checks, each reported as ok/failed with what was seen: one main window, visible and not minimized; Fakturama 2.2.0; English labels (toolbar names); no dirty editors (no tab title starting with `*`); File > Preferences read and **closed with Cancel**: `immediately take over a clearly found item number` must be **off**, currency locale is read and handed to the money parser (never changed); foreground window is Fakturama.

**Live check:** `preflight` prints the checklist. Turn the "take over item number" preference on and it fails naming that setting. Afterwards, Preferences shows nothing changed.

**Done 2026-11-05.** What the live work showed:
- Version comes from the plugin jar names (`plugins/com.sebulli.fakturama.rcp_2.2.0.jar`); `Fakturama.exe` has no version resource.
- Preferences is a top-level `#32770` window titled `Preferences`; pages are TreeItems (SelectionItem.Select switches the page); the take-over CheckBox is on **Documents**, currency on **General**. Toggle and selection states are now part of every snapshot.
- Money format is learned from the `Example` field (-1234.57 formatted by Fakturama, `$-1,234.57` here) plus `decimal places (currency)`; the locale is never changed.
- Opening the File menu: Invoke, Expand and the accessible default action were all refused (COM -2146233079) on an enabled, collapsed item, while they had worked minutes earlier; a real click always worked. `open_menu` tries the UIA methods, then a **guarded click** (Fakturama in front + UIA hit test confirms the item is under the point). Part D reuses `guarded_click`.
- Open tabs: views and editors are both TabItems and cannot be told apart reliably, so all are listed and only a leading `*` fails.
- `Select the address` is its own top-level window; its Search Edit sits inside a Pane after the `Search:` label (`label_sibling` with `descend: true`). Locators now carry `window` (dialog title regex) and `transient` (menus/views); `locate` resolves each in its window and skips closed ones.

### C. Table reader (read-only)

NatTable exposes no rows to UIA. Primary method: focus the grid, **Select All + Copy**, and read the clipboard as tab-separated text, accepting it only if the clipboard sequence number changed and its owner is the Fakturama process. Before any Copy, decide from the grid's pixels whether it is empty, because Copy on an empty grid crashes Fakturama with a NullPointerException; then read twice and require identical results (list is stable). Unreadable → manual review, never "zero rows". OCR stays only for grounding a row click (part E). *(Decision 1.)*

`read-table` command for Data > Documents / VATs / terms of payment / Debtors / Products (opened via existing view tabs or by invoking the menu item, as the findings require).

**Live check:** `read-table documents` shows PO000001–PO000003 with their Cust.Ref. and totals; `read-table vats` shows `VAT 19%`; `read-table payments` shows Cash (Bank Transfer was deleted). Offline: recorded TSV and captured empty/non-empty grid images.

**Done 2026-11-05.** What the live work showed:
- A view is a Pane named like the view; its grid is the unnamed Pane after the search bar. View tabs refuse SelectionItem.Select and their default action (COM -2146233079), so a tab is shown with a guarded click; a view without a tab is opened with Invoke on Data > <view>.
- Documents has a category Tree left of the grid whose selection filters the list (it was on `This transaction`, a context filter). The reader selects the configured category (`Orders`) and verifies it before reading. A workspace without any order has no `Orders` entry: that is an error for now, part J decides.
- UIA `SetFocus` on the grid Pane gives it keyboard focus (verified via the focused element's window handle); Ctrl+A, Ctrl+C then copy all rows; clipboard owner = Fakturama's pid.
- The copy holds **model values, no header row**: price `11.899999999999999`, empty cell `null`, date `Tue Nov 03 00:00:00 EET 2026`, state `COMMAND_ORDER_PENDING`, VAT as a Java object dump. Identifiers (Document, Cust.Ref., Item No., names) are exact. Later parts compare with these model values, not display text.
- The header is verified by OCR of the header band (3x upscale; all 7 views read exactly, ~0.7 s each), against `tables:` in `locators.yaml`.
- Emptiness: header line + 20-px rows separated by RGB(192,192,192) lines; a row with no "ink" ends the list. Unrecognised pictures are errors, never "empty"; ink after an empty row (an overlay) is an error; the copied row count must equal the inked rows unless every visible row is used.

### D. Order header (writes to an unsaved editor only)

Invoke `Create: New Order`, bind that exact tab instance, read and keep the proposed No.; set Cust.Ref. with real key events; set Net and With VAT by expanding each dropdown and clicking the exact ListItem; set Date in the segmented CDateTime (month/day/year segments, verified after blur); read everything back after blur. Input primitives built here: type-with-readback (Ctrl+A, real keystrokes, escaped text), exact dropdown selection, guarded click on a UIA rectangle, condition waits, unknown-dialog detection (→ review).

The date field is the riskiest control. If segment entry proves unreliable we stop and decide before going further.

**Live check:** `order-open out/order.json` leaves a New Order tab with No. PO000004, Date 14 Jul 2026, Cust.Ref. WEB-2026-0714-A17, Net, With VAT. Nothing is saved; you close the tab and answer **No** to "Save changes?".

**Done 2026-11-05.** What the live work showed:
- The editor is a Pane `New Order` with its own native window handle; that handle binds "our" editor (the tab is renamed `*New Order` once dirty, and two unsaved orders both propose PO000004: the number is fixed only on Save, relevant for part I). Reads snapshot that handle only.
- No. and Date are unnamed Edits after their labels (inside a Pane, so `label_sibling` with `descend`); the price-mode ComboBox (`---`/`Net`/`Gross`, default **Gross** here) is the ComboBox after `Date`; `VAT` offers `With VAT`/`Free of Tax`.
- Dropdowns: UIA Expand opens them; the ListItems appear under the ComboBox; a guarded click on the exact ListItem sets the value (read back after the list closed).
- Cust.Ref.: UIA SetFocus (verified with CompareElements on the focused element), Ctrl+A/Backspace as real key events, text as Unicode input, blur by focusing `Consultant`, read back.
- Date (`MMM d, yyyy`): the active segment is remembered between uses (it was month once, year the next time), Up/Down change only that segment, Right moves on and wraps, digits fill a segment and auto-advance. Entry: find the active segment with two probes (Up/Down and Down/Up; the segment changed by both, most significant first, which stays unambiguous at month ends where a month step clamps the day), go Right to it, type zero-padded digits year -> month -> day, verify each, blur, verify the whole date. No OCR needed. Tested offline against a simulator with Calendar.add semantics from every start segment and edge dates.

### E. Selectors as existence checks (unsaved editor only)

From the open Order: upper icon beside `Addresses` → `Select the address`, search Company, read rows (part C), decide **select / not found / ambiguous** on exact Company, First Name, Name, ZIP, City. Upper icon beside `Items` → `Select a product`, search the full SKU, exact match on Item No. Select = click the exact row (Select All does not select a row; ground the copied identifier in the grid with OCR, click it, re-copy the selection and verify it is that row), then OK, then verify the Order: addresses populated / exactly one new line with that SKU. Not found → Cancel. Ambiguous → review. Handle the product dialog closing itself when a search leaves one product.

**Live check:** with the sample → "Debtor not found", "CHR-ERG-01 not found", dialogs cancelled. With a hand-written test spec for Learning Labs / Alice Smith and `SKU-001` → the Debtor is selected and its address appears in the Order; one `SKU-001` line is added. Nothing saved.

**Done 2026-11-05** (`order-select SPEC`; test spec `tests/fixtures/specs/existing_debtor.json`). What the live work showed:
- **Copy with no selected cell raises Fakturama's `Internal Error` ("this.copiedCells is null")**, not only on an empty grid (triggered once while exploring; dismissed with its OK). Rule now everywhere, part C included: copy only when the pixels show exactly the expected rows highlighted (view style blue (0,120,212), dialog style (217,232,251), measured over the header's column extent, not the pane width).
- After a search no row is selected; Down does not select; a guarded click inside a row band does (hit test: the grid Pane is under the point). The address grid selects one row at a time (Ctrl+A copies one row), so both dialogs are read by walking: click row 1, then Down, each row highlighted and copied. Selecting = the same walk to the row, copy, compare with the row read before, OK.
- `Select the address` grid is a second visual style (bordered header, numbered rows, alternating shading, lines only as wide as the table, white below the last row); the detector handles both. OCR merges its right-hand headers, so only `No.`…`City` are checked; the copy has 9 cells (two columns are truncated on screen and named as displayed).
- Search filters as you type (substring, any column, case-insensitive): the decision is made on exact Company, First Name, Name, ZIP, City (debtor) / exact Item No. (product). Same Company with other details = conflict (review).
- After OK: the Order's invoice address shows e.g. `Learning Labs / Alice Smith / 10 Test Street / DE-10115 Berlin / Germany` and a `Delivery address` tab appears (never selected: saving with it selected crashes 2.2.0); a product adds one line, read from the Order's item grid (`Pos.` row header; copy has model values such as `USD 11.9`).
- A tooltip from the just-clicked Items icon covered the item grid; the detector refused it ("used rows after an empty row"). The mouse is now parked on the title bar before every grid capture.

### F. Master-data checks: VAT and payment method (read-only)

Data > VATs: exact `VAT 19%` row; open it and read Name, Value and `VAT code (E-Invoice)` = `S (Standard rate)`; close unchanged. Data > terms of payment: exact Name row; open it and read the payment code (`Credit transfer ` etc.). Any conflict → review, per brief 2.10.2 and 3.5.

**Live check:** `check-vat 19` → reusable; `check-payment "Bank Transfer"` → shows the stored code. If the code is empty, as the database suggests, that is a review case and you decide how to fix it *(Decision 4)*.

**Done 2026-11-05** (`check-vat RATE`, `check-payment METHOD`, `check-master SPEC`). Live: `VAT 19%` reusable (Value `19%`, code `S (Standard rate)`); `Bank Transfer` missing (deleted by hand) -> create in part G; `VAT 7%` missing. What the live work showed:
- A row's editor opens with a guarded double-click on its band; it is a Pane named like the record with its own window handle (bound like the Order editor). Fields: VAT `Name`, `Value` (`19%`), ComboBox `VAT code (E-Invoice)`; payment `Name`, `Description`, ComboBox `!editorPaymentPaymentcode!` (untranslated label; values end with a space, e.g. `Mutually defined ` for Cash).
- Ctrl+W closes an unchanged editor without a prompt. Before it: the tab must not show `*`, the editor's own tab is activated (keys go to the active part) and focus verified; if the editor does not close, any dialog is left for the user.
- The Error view that appeared during part E holds one entry, the copiedCells NPE triggered while exploring (logged in `~/.fakturama2/Logs/Error.log`, under the Items icon's handler because the dialog runs inside it). Idea for part J: note Error.log's size at the start and stop for review if Fakturama logs anything new during a run.

### G. Create payment term, VAT and Product (persists records; backup first)

Each creates one record, saves once, and verifies by searching again:

- payment term (Name/Description, code, zero discounts/days); created **before** any New Debtor editor is opened, because that editor reads its payment list once;
- VAT (Name/Description, Value, code S; click the field before typing, Value commits on blur);
- Product: select VAT **before** gross price (changing VAT recalculates gross), Item No., Name, Description, gross price from `derived.product_gross_price`, cost 0, stock 0; Save once; reselect from the open Order (part E) as proof.

**Live check:** `create-product` for `CHR-ERG-01` → Data > Products shows it with gross 297.50 and VAT 19%; reselecting from the Order adds the line. VAT and term creation are tested with test specs (VAT 7%, Credit Card), since the sample's VAT already exists.

**Built 2026-11-05** (`create-payment`, `create-vat`, `create-product [--sku] [--reselect]`, `create-master SPEC`; each runs preflight first). Created live: term `Bank Transfer` (code `Credit transfer`, the sample needs it), test VAT `VAT 7%`, product `CHR-ERG-01` (gross 297.50, VAT 19%). What the live work showed:
- Create buttons: `Create a new term of payment` / `Create a new tax rate` in the view toolbars (next to `Delete the marked entry`, never used), `Create a new product` in the main toolbar. Editors `New Term of Payment` (code defaults to `Mutually defined `; 12 codes incl. `Credit transfer `, `Credit card `, `SEPA direct debit `), `New TAX Rate` (code defaults to `S (Standard rate)`, Value `0%`), `New product` (unnamed gross/cost price Edits showing `$0.00`, `VAT` lists the VAT names).
- Typing with a verified UIA focus worked in all of them, including the VAT Value (no extra click needed). Every field is read back before Save; Save is the toolbar's `Save the current contents`, once; success = our (selected) tab carries the record's name without `*`. After Save the record is checked again through its Data view.
- **Opening a dropdown and pressing Escape marks an editor as changed.** Dropdowns are now only opened when the value must change.
- **Closing a changed editor shows `Save Parts`** (a list of parts with OK/Cancel, no "don't save" button); OK saves what is ticked. The tool never answers it: an unsaved editor is left for the user.
- Editors with the same name can coexist (an abandoned `*New TAX Rate`): tabs are matched by window handle (visible = active, then the *selected* tab with that name).

### H. Create Debtor (persists; backup first)

New Contact → keep the proposed Customer ID; Company with real key events (UIA writes are not persisted, and Tab types a tab into it); First/Last name; Main address: street, ZIP, city, country, e-mail, phone; role Invoice (+ Delivery if identical; the role popup needs a real click); a second address with the Delivery role when they differ (the sample's case); Miscellaneous: alias, discount 0, Net; Payment combo = the method; Save once; reselect from the Order and verify both addresses.

Known risk: Fakturama's e-mail validator rejects the `.test` domain, and a contact saved with a rejected value may lose Company/E-Mail. Our sample's e-mail is `marta.klein@example.test`. We test this first in part H and read the saved contact back field by field *(Decision 5)*.

**Live check:** `create-debtor out/order.json` → Data > Debtors shows Northstar Office GmbH / Marta Klein with both addresses and Bank Transfer; selecting it from the Order fills both address tabs.

**Done 2026-11-05** (`create-debtor SPEC [--without-email] [--reselect]`; `create-master` now ends with the Debtor). Created live: `CUST000004` Northstar Office GmbH / Marta Klein, Main address = billing (role Invoice), `additional address #1` = delivery (name `Northstar Office Warehouse`, role Delivery), alias, payment Bank Transfer, Net, discount 0%, E-Mail empty (see Decision 5). What the live work showed:
- Editor `New Debtor` proposes the Customer ID (kept). Only the *selected* tab's fields are exposed, so address locators resolve in whichever address tab is selected; '+' adds and selects `additional address #N`.
- Address type: the unnamed Button beside `address type` opens an untitled popup with CheckBoxes `Invoice address` / `Delivery address`; the button needs a real click (confirmed), the CheckBoxes accept UIA Toggle, Escape closes the popup only while it has the focus.
- Country lists 251 entries; an entry scrolled out of view has an empty rectangle: `choose` now scrolls it into view (ScrollItem) before the guarded click.
- Payment dropdown lists the terms' **descriptions** (`Pay Cash` for Cash), so the description is read from the verified term (part F now reads it too).
- **`.test` e-mail (confirmed):** Fakturama shows a red mark at the field's top-left corner, tooltip "Please enter a valid e-mail address" (`example.com` passes). UIA exposes nothing; the mark is read from pixels (`uia/marks.py`) on every typed field. Default: stop for review before saving; `--without-email` clears the field and saves, the report says so. **Decision 5 settled this way.**
- Closing an unsaved editor: `Save Parts` lists it ticked; unticking + OK discards (done only by hand / in exploration, never by the tool).
- **The Debtors list shows the delivery address's ZIP/City** for a contact with a delivery address (CUST000004: 10553), while `Select the address` shows the billing one (10117, type BILLING). The Debtors-list decision accepts the ZIP/City of either address; two contacts matching → ambiguous. After Save the contact is reopened from that list and read back field by field (all 25 fields matched).
- The Order's delivery address text is `Company / First Last / Street / DE-ZIP City / Country`: the address's `additional name` is not printed there, so it is verified on the contact only.

### I. Lines, Order verification and Save (persists the Order; backup first)

Per item, after selection: set Qty., U.Price, Discount through the inline editors (double-click, type, commit, leave the editor); check the line VAT popup text (`VAT 19% (19.0%)`); read the item grid with the table reader (twice, stable) and compare every line with `derived.expected_line_net`. Then: overall Discount 0, Shipping free; Total Net / VAT / Total equal the spec; **select the Invoice address tab** (saving with the Delivery tab selected crashes 2.2.0); journal `attempting save_order`; Save once; handle the known contact-confirmation popup on save; verify the Documents row: number, Date, Cust.Ref., state open, Total; journal `done`.

**Live check:** an Order PO00000N with two lines 450.00 / 120.00 and totals 570.00 / 108.30 / 678.30 is in Data > Documents, state open.

**Done 2026-11-05** (`create-order SPEC [--no-save] [--allow-duplicate]`, `check-order SPEC`). Saved live: **PO000004**, Jul 14 2026, Northstar Office GmbH / Marta Klein, `WEB-2026-0714-A17`, open, total 678.30; a second `create-order` stops at the duplicate guard (Decision 2, built here already). What the live work showed:
- Double-click on an item cell opens an inline UIA Edit over the cell (`1.00`, `USD 40`, `0.00 %`); real keys + Enter commit. Enter moves the Qty. editor to the next row (last row wraps to the first); Escape closes it unchanged. Plain numbers work in every column (`2`, `41.50`, `10` = 10 %). Only differing cells are typed.
- Cells are located from the grid's pixels: column separators in the header; **while the grid has the focus the header turns grid-line grey and its separators vanish**, so the empty rows' vertical lines are used (both sources must agree when both exist).
- Copy holds model values: Qty. `2.00`, U.Price `USD 250` (the app locale's currency code, not the order's EUR: amounts are compared, the code is not), Discount `-0.1` for 10 %, Price `USD 450`. Footer shows display money (`$570.00`).
- Overall Discount stayed `0%` and Shipping `Free of shipping costs` `$0.00` after selecting the Debtor; both are verified (not typed).
- Save: the toolbar Save, once; no dialog appeared (no contact confirmation popup); the editor tab became `PO000004`. Selecting the Invoice address tab first was enough; no crash.
- **After the Save refreshed Documents, UIA Select on the `Orders` filter only focused it**; the reader now falls back to a guarded click. The first live run crashed on that read after a successful Save; the post-Save check now reports instead of raising, and `check-order` re-verifies a saved Order read-only.

### J. `run`: the state machine end to end

**Handoff (2026-11-05, before J starts).**
- *Building blocks ready* (each tested offline and live): `preflight`; `create-master SPEC [--without-email]` (payment → VAT → products → Debtor; each check-first, create-if-missing, save once, read back); `create-order SPEC [--no-save] [--allow-duplicate]` (duplicate guard → header → selections → lines → totals → addresses → Save once → Documents); `check-order SPEC` (read-only). J wires these as steps of one `run`, it does not rewrite them: `flow/create_master.py` / `create_debtor.py` return `Created(status created|exists|review|stopped, saved)`, `flow/create_order.py` returns `OrderRun(steps, number, saved, unsaved_left_open)`.
- *Still to build:* the `run` command and step interface; the journal (`attempting`/`done` per Save, from the `saved` flags); evidence (screenshot of the bound editor before/after, read-backs, UIA dump on failure, `report.md`); the Error.log check (note its size at start, stop for review on new entries: `~/.fakturama2/Logs/Error.log`, currently 4980 bytes, one old copiedCells NPE); the `FakeFakturama` simulator and decision-tree tests (all exist / all missing / ambiguous Debtor / failure after a master Save / failure after the Order Save / addresses cleared).
- *Workspace state at handoff:* PO000004 and Debtor CUST000004 were deleted by hand; products CHR-ERG-01, MAT-DESK-02, term Bank Transfer, VAT 19% and test VAT 7% remain; an unsaved New Order (PO000005) from a stopped run may still be open (close without saving). For J's live check either restore the pre-G backup (shows the create branches) or run `create-master --without-email` first.
- *Open question:* in the stopped run the deleted Debtor was still selectable in `Select the address`, then the Order's addresses were cleared before Save. Either it was deleted during the run, or the dialog showed stale data. `create-order` now names this plainly (`addresses_cleared`); if it recurs with a Debtor deleted *before* the run, add a guard (cross-check the Debtors list before selecting).

Wire A–I as explicit steps (precondition → one action → read-back) with outcomes ok / create / review, per the design. Plus:
- **Journal** (`out/runs/<timestamp>/journal.jsonl`): attempting/done per write; a Save whose outcome is uncertain is never repeated.
- **Evidence**: before/after screenshot of the bound editor per step, values read back, a UIA dump on failure, `report.md`.
- **Duplicate guard**: preflight refuses to start if Documents already holds an Order with the same Cust.Ref. *(Decision 2)*.
- **Simulator**: a `FakeFakturama` implementing the same step interface, so the whole decision tree (all exist / all missing / ambiguous debtor / failure after a master Save / failure after the Order Save) is tested offline.

**Live check:** on a backed-up workspace, `run out/order.json` ends with a saved, verified Order. A second run on the same spec stops at the duplicate guard. With the guard overridden, it takes the select-existing branches for Debtor and Products.

## Tests per part

| Part | Offline (default `pytest`) | Live (`-m uia` / `-m uia_write`) |
|---|---|---|
| A | locator resolution on recorded trees | bind + dump |
| B | each check on recorded trees / fake values | real preflight |
| C | TSV parsing, emptiness detector on captured grid images, clipboard checks with a fake | read 3 Data views |
| D | date segment logic, typed-text escaping, readback comparison | header round-trip |
| E | match decisions (select/none/ambiguous), row grounding on captured grids | not-found + existing-debtor cases |
| F | VAT/payment readback rules | check VAT 19%, Bank Transfer |
| G, H | field plans from the spec (what is typed where), verification rules | create + reselect |
| I | line/total verification rules, save gating | save + Documents row |
| J | full decision tree on the simulator | end-to-end run |

## Safety for live tests

- **Backup before G–J:** close Fakturama, copy the workspace folder (e.g. `fakturama data TJM Labs` → `fakturama data TJM Labs - backup 2026-11-05`); restore by copying back. Done by you, not by the tool.
- The workspace lives in **OneDrive**; sync can hold locks on the HSQLDB files while Fakturama writes. Pausing OneDrive sync during live runs is safer.
- Reading tables uses the clipboard: anything you copied before is overwritten.
- During a live run: Fakturama maximized and in front, hands off mouse and keyboard; another window taking focus stops the run (review), it is never retried blindly.

## Decisions (confirmed 2026-11-05)

1. **Tables are read by clipboard copy**, not screenshot OCR (DESIGN.md updated). Exact text, no OCR misreads such as `Learner`/`learner`; OCR only locates the row to click.
2. **Duplicate guard on Cust.Ref.**: a run refuses to start when an Order with the same Cust.Ref. exists, unless `--allow-duplicate`.
3. **You start Fakturama**; the tool never launches it. Preflight only checks.
4. **Payment codes follow the brief's mapping** (2.10.4): Bank Transfer = `Credit transfer`, Credit Card = `Credit card`, SEPA Direct Debit = `SEPA direct debit`. An existing term is reused only if its code matches; an existing term with a missing or different code is a conflicting definition → manual review (brief 2.10.2). The tool creates missing terms but never edits existing ones.
5. **`.test` e-mail** (settled in part H): Fakturama rejects it (validation mark). Default: stop for review before saving; `--without-email` saves the Debtor with an empty E-Mail and the report says so. Never dropped silently.

## Known Fakturama 2.2.0 behaviours

What the parts rely on, and what was observed live in this workspace.

| Behaviour | Status here | Part |
|---|---|---|
| NatTable rows are not UIA elements; Select All + Copy gives the rows as TSV (accepted only when freshly written by the Fakturama process) | confirmed on all Data views and both dialogs | C |
| Copy on an empty grid, or with no cell selected, raises `copiedCells is null` | confirmed (no selection); copy only when pixels show the expected highlighted rows | C, E |
| Select All/Copy does not select a row; a click inside the row does | confirmed; the selected row is copied and compared before OK | E |
| The product dialog may add a unique match and close by itself | not seen; handled | E |
| Company and similar fields need real key events; Tab types a tab into Company | real keys + blur on Customer ID worked | D, H |
| A New Debtor editor reads the payment list when it opens | followed: terms are created before the Debtor | G, H |
| VAT editor: Value commits on focus loss | confirmed; no extra click needed | G |
| The address-role popup needs a real click; its CheckBoxes accept UIA Toggle | confirmed | H |
| A UIA desktop walk can hang on other apps; windows are found with Win32 `EnumWindows` | followed | A |
| The e-mail validator rejects the `.test` domain (red mark, "Please enter a valid e-mail address") | confirmed; stop for review or `--without-email` | H |
| Saving an Order while the Delivery address tab is selected crashes 2.2.0 | not tried; the Invoice tab is always selected and verified before Save | I |
| A contact-confirmation popup can appear during Order Save | not seen; any dialog during Save stops the run | I |
| Selecting a Debtor sets the overall Discount to the contact's discount | contact discount is 0; verified after selection | I |
| Phase 4: an unpaid Invoice may show state `COMMAND_ERROR` in Documents; the paid-date label may be `at` | to verify in Phase 4 | — |
