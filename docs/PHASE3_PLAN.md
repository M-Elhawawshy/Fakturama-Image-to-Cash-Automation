# Phase 3 — Order flow via UIA (OrderSpec → saved, verified Order)

**Goal:** `python -m fakturama_agent run out/order.json` drives Fakturama 2.2.0 from a validated `OrderSpec` to a saved Order that is verified in Data > Documents (brief steps 1.3–4.5). The linked Invoice and payment status are Phase 4.

**Approach:** ten parts (A–J), each one a working, committed command with its own offline tests and a live check you can do by hand. A part is done only when its live check passes on your machine. Parts A–F persist nothing in Fakturama; G–J create records, so they start with a workspace backup.

Binding inputs: `docs/DESIGN.md`, `agent findings/fakturama-uia-findings.md`, the brief. Lessons from the reference implementation ([KareemAbedHelmy/fakturama-image-to-cash](https://github.com/KareemAbedHelmy/fakturama-image-to-cash)) are listed at the end and referenced per part as **[R#]**.

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

- Find Fakturama's process and windows with Win32 `EnumWindows` (a UIA desktop walk can hang on other apps **[R9]**); require exactly one main window `^Fakturama`, class `SWT_Window0`; include nested dialogs.
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

NatTable exposes no rows to UIA. Primary method: focus the grid, **Select All + Copy**, and read the clipboard as tab-separated text, accepting it only if the clipboard sequence number changed and its owner is the Fakturama process **[R1]**. Before any Copy, decide from the grid's pixels whether it is empty, because Copy on an empty grid crashes Fakturama with a NullPointerException **[R2]**; then read twice and require identical results (list is stable). Unreadable → manual review, never "zero rows". OCR stays only for grounding a row click (part E). *(Decision 1.)*

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

Invoke `Create: New Order`, bind that exact tab instance, read and keep the proposed No.; set Cust.Ref. with real key events; set Net and With VAT by expanding each dropdown and clicking the exact ListItem; set Date in the segmented CDateTime (month/day/year segments, verified after blur); read everything back after blur. Input primitives built here: type-with-readback (Ctrl+A, real keystrokes, escaped text **[R5]**), exact dropdown selection, guarded click on a UIA rectangle, condition waits, unknown-dialog detection (→ review).

The date field is the riskiest control. If segment entry proves unreliable we stop and decide before going further.

**Live check:** `order-open out/order.json` leaves a New Order tab with No. PO000004, Date 14 Jul 2026, Cust.Ref. WEB-2026-0714-A17, Net, With VAT. Nothing is saved; you close the tab and answer **No** to "Save changes?".

**Done 2026-11-05.** What the live work showed:
- The editor is a Pane `New Order` with its own native window handle; that handle binds "our" editor (the tab is renamed `*New Order` once dirty, and two unsaved orders both propose PO000004: the number is fixed only on Save, relevant for part I). Reads snapshot that handle only.
- No. and Date are unnamed Edits after their labels (inside a Pane, so `label_sibling` with `descend`); the price-mode ComboBox (`---`/`Net`/`Gross`, default **Gross** here) is the ComboBox after `Date`; `VAT` offers `With VAT`/`Free of Tax`.
- Dropdowns: UIA Expand opens them; the ListItems appear under the ComboBox; a guarded click on the exact ListItem sets the value (read back after the list closed).
- Cust.Ref.: UIA SetFocus (verified with CompareElements on the focused element), Ctrl+A/Backspace as real key events, text as Unicode input, blur by focusing `Consultant`, read back.
- Date (`MMM d, yyyy`): the active segment is remembered between uses (it was month once, year the next time), Up/Down change only that segment, Right moves on and wraps, digits fill a segment and auto-advance. Entry: find the active segment with two probes (Up/Down and Down/Up; the segment changed by both, most significant first, which stays unambiguous at month ends where a month step clamps the day), go Right to it, type zero-padded digits year -> month -> day, verify each, blur, verify the whole date. No OCR needed. Tested offline against a simulator with Calendar.add semantics from every start segment and edge dates.

### E. Selectors as existence checks (unsaved editor only)

From the open Order: upper icon beside `Addresses` → `Select the address`, search Company, read rows (part C), decide **select / not found / ambiguous** on exact Company, First Name, Name, ZIP, City. Upper icon beside `Items` → `Select a product`, search the full SKU, exact match on Item No. Select = click the exact row (Select All does not select a row; ground the copied identifier in the grid with OCR, click it, re-copy the selection and verify it is that row **[R3]**), then OK, then verify the Order: addresses populated / exactly one new line with that SKU. Not found → Cancel. Ambiguous → review. Handle the product dialog closing itself when a search leaves one product **[R4]**.

**Live check:** with the sample → "Debtor not found", "CHR-ERG-01 not found", dialogs cancelled. With a hand-written test spec for Learning Labs / Alice Smith and `SKU-001` → the Debtor is selected and its address appears in the Order; one `SKU-001` line is added. Nothing saved.

**Done 2026-11-05** (`order-select SPEC`; test spec `tests/fixtures/specs/existing_debtor.json`). What the live work showed:
- **Copy with no selected cell raises Fakturama's `Internal Error` ("this.copiedCells is null")**, not only on an empty grid (triggered once while exploring; dismissed with its OK). Rule now everywhere, part C included: copy only when the pixels show exactly the expected rows highlighted (view style blue (0,120,212), dialog style (217,232,251), measured over the header's column extent, not the pane width).
- After a search no row is selected; Down does not select; a guarded click inside a row band does (hit test: the grid Pane is under the point). The address grid selects one row at a time (Ctrl+A copies one row), so both dialogs are read by walking: click row 1, then Down, each row highlighted and copied. Selecting = the same walk to the row, copy, compare with the row read before, OK.
- `Select the address` grid is a second visual style (bordered header, numbered rows, alternating shading, lines only as wide as the table, white below the last row); the detector handles both. OCR merges its right-hand headers, so only `No.`…`City` are checked; the copy has 9 cells (two columns are truncated on screen and named as displayed).
- Search filters as you type (substring, any column, case-insensitive): the decision is made on exact Company, First Name, Name, ZIP, City (debtor) / exact Item No. (product). Same Company with other details = conflict (review).
- After OK: the Order's invoice address shows e.g. `Learning Labs / Alice Smith / 10 Test Street / DE-10115 Berlin / Germany` and a `Delivery address` tab appears (never selected: R11); a product adds one line, read from the Order's item grid (`Pos.` row header; copy has model values such as `USD 11.9`).
- A tooltip from the just-clicked Items icon covered the item grid; the detector refused it ("used rows after an empty row"). The mouse is now parked on the title bar before every grid capture.

### F. Master-data checks: VAT and payment method (read-only)

Data > VATs: exact `VAT 19%` row; open it and read Name, Value and `VAT code (E-Invoice)` = `S (Standard rate)`; close unchanged. Data > terms of payment: exact Name row; open it and read the payment code (`Credit transfer ` etc.). Any conflict → review, per brief 2.10.2 and 3.5.

**Live check:** `check-vat 19` → reusable; `check-payment "Bank Transfer"` → shows the stored code. If the code is empty, as the database suggests, that is a review case and you decide how to fix it *(Decision 4)*.

### G. Create payment term, VAT and Product (persists records; backup first)

Each creates one record, saves once, and verifies by searching again:

- payment term (Name/Description, code, zero discounts/days); created **before** any New Debtor editor is opened, because that editor reads its payment list once **[R6]**;
- VAT (Name/Description, Value, code S; click the field before typing, Value commits on blur **[R7]**);
- Product: select VAT **before** gross price (changing VAT recalculates gross), Item No., Name, Description, gross price from `derived.product_gross_price`, cost 0, stock 0; Save once; reselect from the open Order (part E) as proof.

**Live check:** `create-product` for `CHR-ERG-01` → Data > Products shows it with gross 297.50 and VAT 19%; reselecting from the Order adds the line. VAT and term creation are tested with test specs (VAT 7%, Credit Card), since the sample's VAT already exists.

### H. Create Debtor (persists; backup first)

New Contact → keep the proposed Customer ID; Company with real key events (UIA writes are not persisted, and Tab types a tab into it **[R5]**); First/Last name; Main address: street, ZIP, city, country, e-mail, phone; role Invoice (+ Delivery if identical; the role popup needs a real click **[R8]**); a second address with the Delivery role when they differ (the sample's case); Miscellaneous: alias, discount 0, Net; Payment combo = the method; Save once; reselect from the Order and verify both addresses.

Known risk: Fakturama's e-mail validator rejects the `.test` domain and the reference saw the contact persisted **without** Company/E-Mail **[R10]**. Our sample's e-mail is `marta.klein@example.test`. We test this first in part H and read the saved contact back field by field *(Decision 5)*.

**Live check:** `create-debtor out/order.json` → Data > Debtors shows Northstar Office GmbH / Marta Klein with both addresses and Bank Transfer; selecting it from the Order fills both address tabs.

### I. Lines, Order verification and Save (persists the Order; backup first)

Per item, after selection: set Qty., U.Price, Discount through the inline editors (double-click, type, commit, leave the editor); check the line VAT popup text (`VAT 19% (19.0%)`); read the item grid with the table reader (twice, stable) and compare every line with `derived.expected_line_net`. Then: overall Discount 0, Shipping free; Total Net / VAT / Total equal the spec; **select the Invoice address tab** (saving with the Delivery tab selected crashes 2.2.0 **[R11]**); journal `attempting save_order`; Save once; handle the known contact-confirmation popup on save **[R12]**; verify the Documents row: number, Date, Cust.Ref., state open, Total; journal `done`.

**Live check:** an Order PO00000N with two lines 450.00 / 120.00 and totals 570.00 / 108.30 / 678.30 is in Data > Documents, state open.

### J. `run`: the state machine end to end

Wire A–I as explicit steps (precondition → one action → read-back) with outcomes ok / create / review, per the design. Plus:
- **Journal** (`out/runs/<timestamp>/journal.jsonl`): attempting/done per write; a Save whose outcome is uncertain is never repeated.
- **Evidence**: before/after screenshot of the bound editor per step, values read back, a UIA dump on failure, `report.md`.
- **Duplicate guard**: preflight refuses to start if Documents already holds an Order with the same Cust.Ref. *(Decision 2)*.
- **Simulator**: a `FakeFakturama` implementing the same step interface, so the whole decision tree (all exist / all missing / ambiguous debtor / failure after a master Save / failure after the Order Save) is tested offline **[R13]**.

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
5. **`.test` e-mail** (open until part H): if Fakturama rejects it, the run stops for review rather than dropping the e-mail silently. Revisit once part H shows the real behavior.

## Lessons from the reference implementation

Taken from its code and `docs/live-run.md` (same Fakturama 2.2.0, same sample order). Treat as hypotheses to confirm live.

| # | Lesson | Part |
|---|---|---|
| R1 | Read NatTable grids via Select All + Copy; accept the clipboard only when freshly written by the Fakturama process | C |
| R2 | Copy on an empty grid throws `copiedCells is null`; detect emptiness from pixels first, after the grid stops changing | C |
| R3 | Select All/Copy does not select a row; click the exact row, then verify the selection by copying without Select All | E |
| R4 | The product dialog may add a unique match and close by itself | E |
| R5 | Company (and similar fields) persist only real key events; escape key syntax; Tab inserts a tab character in Company | D, H |
| R6 | A New Debtor editor loads its payment list once at open; create a missing term before opening it | G |
| R7 | New VAT editor: click the field before typing; Value commits only on focus loss | G |
| R8 | The address-role popup opens on a real click, not on Invoke | H |
| R9 | Enumerate windows with Win32 `EnumWindows`; a UIA desktop walk can hang on another app | A |
| R10 | Fakturama rejects `.test` e-mail domains; contact persisted without Company/E-Mail | H |
| R11 | Saving an Order with the Delivery address tab selected crashes 2.2.0 (NPE in `doSave`); select the Invoice address tab first | I |
| R12 | A contact-confirmation popup can appear during Order Save | I |
| R13 | A simulator behind the same interface tests the workflow's decision tree offline | J |
| — | Selecting a Debtor resets the overall Discount to the contact's value (we keep 0, verify after) | I |
| — | Phase 4: an unpaid Invoice shows state `COMMAND_ERROR` in Documents; the paid-date label is `at` | — |

Not taken over: the operator-prompted "guided runner" (we stop for review instead), Tesseract (we have RapidOCR), the local-model extractor (Phase 2 is done).
