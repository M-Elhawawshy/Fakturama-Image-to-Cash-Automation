# Fakturama UIA findings for a full-version agent

Tested on Windows, Fakturama **2.2.0 in English**, Python 3.14, pywinauto 0.6.9 with `backend="uia"`. These are observed controls/behaviors, not a guarantee about other versions. The mini project saved and verified Orders PO000001–PO000003, including component creation/reselection and explicit recovery after interruptions. Invoice/payment-state automation remains untested.

## Main window and actions

- Main window: title regex `^Fakturama.*`, class `SWT_Window0`. Require exactly one. Inspect both process-owned top-level windows and nested UIA `Window` descendants: SWT dialogs can be nested under the main window.
- Toolbar: `Create: New Order` (Button), `Save the current contents` (Button), `Create a new product` (Button), `Create a new contact` (SplitButton). **Use direct InvokePattern when exposed.** Never mouse-retry an uncertain failed Invoke.
- Data views: `Documents`, `Products`, `Debtors`, `terms of payment`, `VATs`. Prefer existing view TabItems. Otherwise **Invoke the top-level MenuItem**, then its entry. File/Data advertise ExpandCollapse, but Expand intermittently failed with COM `-2146233079`; direct Invoke worked. Preferences are under **File > Preferences**.
- Tabs expose SelectionItem, but this SWT provider can reject Select with COM `DISP_E_MEMBERNOTFOUND` (`-2147352573`). That known unsupported-member case needs a guarded click on the current UIA rectangle and selected-state readback.

## Critical SWT limitations

**Pattern availability does not prove persistence.** ValuePattern.SetValue and dropdown SelectionItem.Select sometimes changed displayed text without updating SWT's Java model. Native keyboard editing at a UIA-located/focused Edit, and native clicking of the exact UIA ListItem, persisted correctly. Use real key events for shortcuts such as Ctrl+A/Backspace (`vk_packet=False`); ordinary literal text can use Unicode input. Read after blur/save/reselection, not just immediately after setting text.

NatTable rows/cells/headers were not exposed by UIA, including populated tables. UIA still supplies the enclosing pane rectangle. The tested fallback screenshots that pane, detects ruled cells and OCRs required columns. There are **no fixed desktop coordinates**. Complete unscrolled results and a blank terminal row are required; unreadable/clipped/scrollable results are manual review, never "zero matches".

Selection makes some table text bold/italic and changes colors. OCR can misread `Learner` as `learner`. Check exact identity before the click, verify exactly the intended row highlight, then verify the accepted address/product in its destination. Do not case-fold or fuzzy-match identifiers to compensate.

## Required preferences and desktop

- File > Preferences > Documents: **off** `immediately take over a clearly found item number`. With it on, even a SKU **prefix** yielding one result can auto-add and close the dialog while typing, before duplicate checks. With it off, full-SKU search and explicit row/OK selection worked.
- Preferences > General: **Currency locale = United States** for the tested `$1,234.50` reader. Applying preferences initially reset this installation to Afghanistan. Explicitly select United States, apply, reopen and verify. Cached table text may need a view refresh. Runtime preflight reads both settings and uses Cancel, changing nothing.
- Keep the app visible, restored/maximized and the desktop idle. GoLand's floating AI Chat window stole focus/covered a table during a live run. Check foreground PID, hit-test physical targets and reject occluded captures. Restoring focus during observation is safe; repeating an uncertain entry/Save is not. Minimized/headless operation is not supported by this hybrid approach.

## Order editor

- New Order opens a TabItem and pane named `New Order`; dirty tab text starts `*`. Preserve the proposed No. Bind the exact tab instance when returning from master editors; names can repeat.
- `Cust.Ref.` is a named Edit. No. and Date are unnamed Edits associated with Text labels, sometimes inside the following container. Use scoped label/sibling structure, not global Edit indexes.
- Date is a **segmented** CDateTime Edit displaying e.g. `Nov 03, 2026`. Setting its complete Value did not persist. The working adapter locates month/day/year by OCR inside its UIA rectangle, requires agreement with its accessible value, enters segment digits and verifies the final date after blur.
- Header price-mode ComboBox is in the No./Date container; select `Net`. Separate ComboBox `VAT`: select `With VAT`. `Discount`, `Shipping`, disabled total fields `Total Net`, `VAT`, `Total` are accessible. Tested zero-shipping option: `Free of shipping costs`.
- Text `Addresses` has two Image siblings; the first opens `Select the address`, the second is the green +. Text `Items` has four Image siblings; the first opens `Select a product`. Images lack usable Invoke: click their observed UIA rectangles after checking structure. The items table container follows the Items icon group.

## Debtors and payment methods

- `Select the address`: Text `Search:` followed by Edit; unnamed table pane; Buttons `OK`/`Cancel`. Search Company. Exact identity columns: **Company, First Name, Name, ZIP, City**. Several exact matches or conflicting partial data require review.
- New contact toolbar opens pane `New Debtor`; keep `Customer ID`. Company is named; paired unnamed Edits follow `First Name Last Name` and `ZIP - City`.
- Tabs `Addresses` > `Main address`: named Street, E-Mail, Telephone; Country ComboBox. Edit `address type` has an unnamed Button in its parent: invoke it to expose `Invoice address` and `Delivery address` CheckBoxes. Toggle both and verify the roles string.
- `Miscellaneous`: `Alias name`, `Discount`, `Net or Gross` ComboBox, **`Payment` ComboBox**. There is no separate Payment tab here. Select an existing method, save, return to the Order and reselect the debtor. Saved contact pane titles are e.g. `Orion Study, Dan Taylor`.
- Rendered German addresses include `DE-` before ZIP. Compare all normalized lines, including street/country. Existing debtor identity/payment can be verified by opening its Data > Debtors row.
- One-time payment setup was tested separately: `Create a new term of payment`, Name/Description, code field **`!editorPaymentPaymentcode!`**, option **`Credit transfer `** (trailing space). Fresh contact editors see newly saved methods; older editors can retain stale options. The mini pipeline never creates payment methods.

## Products, VAT and Order lines

- `Select a product`: Search Edit, unnamed table, OK/Cancel. Columns: `Item No.`, `Name`, `Description`, `Stock`, `Price`, `VAT`. Compare full SKU exactly. After OK, verify exactly one expected SKU/name row was added to the Order.
- New product pane `New product`: `Item Number`, `Name`, `Description`, ComboBox `VAT`, `Price (gross)`, `cost price (net)`, `Stock`. **Select VAT before entering gross price**: changing VAT recalculates gross. Save once and reselect to verify persistence. Tested gross master prices: 11.90 and 5.95 for net 10 and 5 at 19%.
- Data > VATs: search exact `VAT 19%`; table `Name`/`Value`. Open the row and read `Name`, `Value`, ComboBox `VAT code (E-Invoice)` = `S (Standard rate)`. A VAT named 19% was once stored as 0%; check the actual rate. New-rate toolbar: `Create a new tax rate`; Name, Description, Value and code were tested in separate demo setup. Old product editors can cache stale VAT options.
- Order table columns: `Pos.`, `Qty.`, `Item No.`, `Picture`, `Name`, `Description`, `VAT`, `U.Price`, `Discount`, `Price`. Qty/U.Price/Discount open one inline Edit on double-click. Commit and leave the editor before OCR; Enter alone can leave it active. Pos. is useful for selecting without editing a data cell.
- Description double-click opens `position description`, Edit `Please enter a descriptive text:`, OK/Cancel. It can be blank even when the product description is populated if `Copy description from product` is off; fill/verify explicitly.
- Line VAT opens an **inline Edit plus popup List**, not a ComboBox. Accessible selection text is e.g. `VAT 19% (19.0%)`; check both percentages. A 10% deduction displays `-10.00 %`. Independent line-price math confirms its effect.

## Save, Documents and remaining exploration

- Verify header, both addresses, every line and totals before invoking Save once. Mark the attempt before dispatch. No transaction spans editors: saved contacts/products remain after a later failure.
- Documents table: `Document`, `Date`, `Cust.Ref.`, `State`, `Total`; Search Edit is accessible. **Document contains only the number**, e.g. `PO000003`, not `Order PO000003`. State is `open`; row date can be `Nov 3, 2026`. Open the exact row and verify Text `Order`, No., Date, Cust.Ref. and total through accessible fields.
- Closing a dirty Eclipse editor can open `Save Parts`; Ctrl+W is not an unconditional discard. Duplicate Contact warnings need manual review. Do not automatically dismiss unknown warnings or save/discard unrelated tabs.
- Follow-up group `Create a follow-up document` exposes Confirmation/Invoice/Delivery/Proforma buttons. Their complete workflows, invoice persistence, paid state and payment date **have not been tested**. Explore fresh trees and readbacks for those full-brief steps.

Working reference: `fakt/uia/labels.py` has English labels; `app.py` handles patterns/waits/input; `table.py` and `date_input.py` contain the narrow fallbacks. Run `worker/tools/inspect_tree.py --all-windows --patterns` for process-scoped discovery. README lists actual saved Orders and evidence folders.
