# AP Implementation Plan

## Purpose

This plan translates the AP workflow proposal into concrete implementation work against the current BFSAI codebase.

It covers:

- scan intake from `server/scans`
- classification-first routing
- PO Box-first client resolution
- extraction without requiring a preexisting payee account
- approved/paid/exported bill states
- paid-bill QIF batch export

## Current BFSAI Baseline

The current code already provides:

- document classification via [app/classifier.py](/Users/ajfarren/Projects/aq/bfsai/app/classifier.py)
- document routing helpers via [app/documents.py](/Users/ajfarren/Projects/aq/bfsai/app/documents.py)
- extraction, verification, alignment, and review via [app/pipeline.py](/Users/ajfarren/Projects/aq/bfsai/app/pipeline.py)
- a worker that sweeps `<watch_root>/<po_box>/new` via [app/worker.py](/Users/ajfarren/Projects/aq/bfsai/app/worker.py)
- a local SQLite schema via [app/repository.py](/Users/ajfarren/Projects/aq/bfsai/app/repository.py)

What is missing for the target workflow:

- a raw intake area at `server/scans`
- a classification-first scan intake stage before `process_file`
- client matching and client state
- canonical bill records separate from document records
- paid/exported bill states
- QIF export batches

## Target Flow

1. Scanner drops file into `server/scans`
2. Intake worker picks up file
3. System classifies document type
4. System resolves client using PO Box first
5. File is routed into the correct client subdirectory
6. If the routed type is extractable, existing extraction/review flow continues
7. Review queue creates or updates a canonical bill
8. User approves bill
9. User later marks bill as paid
10. `Process QIF` shows paid, unexported bills
11. User selects bills and downloads generated QIF

## Phase 1: Scan Intake and Classification-First Routing

### New behavior

All scans land in:

- `server/scans`

The worker should first sweep that directory, not just per-client `new` folders.

### Required code changes

#### 1. Settings

Update [app/config.py](/Users/ajfarren/Projects/aq/bfsai/app/config.py) and config YAML to support:

- `scan_root`
- optional `legacy_unresolved_client_dir`

Suggested config additions:

```yaml
scan_root: ./server/scans
legacy_unresolved_client_dir: ./server/unresolved
watch_root: ./sample_data
```

Suggested dataclass additions:

```python
@dataclass(slots=True)
class Settings:
    scan_root: Path
    watch_root: Path
    database_path: Path
    ...
```

#### 2. New intake service

Add a new service:

- `app/intake.py`

Responsibilities:

- watch `settings.scan_root`
- read minimal OCR/classification text
- classify document type
- resolve client from PO Box or fallback
- route file into client folder
- create intake/event records

Suggested interface:

```python
class ScanIntakeService:
    def __init__(self, settings: Settings, repository: Repository): ...
    def process_scan(self, source_path: Path) -> str | None: ...
```

#### 3. Worker split

Refactor [app/worker.py](/Users/ajfarren/Projects/aq/bfsai/app/worker.py) into two sweeps:

- `sweep_scans_once(...)`
- `sweep_client_new_once(...)`

Target loop:

```python
while True:
    sweep_scans_once(intake_service)
    sweep_client_new_once(processor, settings.watch_root)
    time.sleep(settings.poll_seconds)
```

#### 4. Classifier contract

Extend [app/classifier.py](/Users/ajfarren/Projects/aq/bfsai/app/classifier.py) so it returns route intent, not just type and extractability.

Suggested replacement for `DocumentRouting`:

```python
@dataclass(frozen=True, slots=True)
class DocumentRouting:
    dtype: str
    should_extract: bool
    retain_in_new: bool
    copy_to_type_folder: bool
    move_to_type_folder: bool
    reason: str
```

Target routing behavior:

- bill: `retain_in_new = True`
- credit card statement: `retain_in_new = True`, `copy_to_type_folder = True`
- other typed docs: `move_to_type_folder = True`

#### 5. Documents helpers

Extend [app/documents.py](/Users/ajfarren/Projects/aq/bfsai/app/documents.py) with helpers for:

- moving raw scans from `server/scans`
- routing into client subdirectories
- routing unresolved-client docs into a holding folder

Suggested new helpers:

```python
def move_scan_to_client_new(...)
def copy_client_new_to_document_type(...)
def move_scan_to_client_document_type(...)
def move_scan_to_unresolved(...)
```

### New BFSAI schema for intake tracking

Add these tables in [app/repository.py](/Users/ajfarren/Projects/aq/bfsai/app/repository.py):

#### `scan_intakes`

```sql
CREATE TABLE IF NOT EXISTS scan_intakes (
  id TEXT PRIMARY KEY,
  original_filename TEXT NOT NULL,
  source_path TEXT NOT NULL,
  current_path TEXT NOT NULL,
  sha256 TEXT,
  status TEXT NOT NULL,
  detected_po_box TEXT,
  matched_po_box TEXT,
  client_match_status TEXT,
  classification_json TEXT NOT NULL DEFAULT '{}',
  error_message TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

#### `scan_intake_events`

```sql
CREATE TABLE IF NOT EXISTS scan_intake_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scan_intake_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
```

### Tests to add

- `tests/test_intake.py`
- expand `tests/test_classifier.py`
- expand `tests/test_documents.py`

Key test cases:

- bill from `server/scans` moves to `<po_box>/new`
- credit card statement moves to `<po_box>/new` and copies to `<po_box>/credit_card_statements`
- typed non-bill moves to target type folder only
- unknown doc moves to `<po_box>/other`
- unresolved legacy client lands in unresolved holding area

## Phase 2: Client Resolution

### Goal

Determine the client before vendor matching, using the extracted PO Box as the first signal.

### Required code changes

#### 1. New client resolver

Add:

- `app/client_resolution.py`

Suggested interface:

```python
class ClientResolver:
    def __init__(self, repository: Repository): ...
    def resolve(self, po_box: str | None, text: str, source_path: Path) -> ClientMatchResult: ...
```

Suggested result shape:

```python
@dataclass(frozen=True, slots=True)
class ClientMatchResult:
    client_id: str | None
    mailbox_number: str | None
    status: str
    confidence: float
    method: str
    reason: str
```

#### 2. PO Box detection utility

Add a simple parser in `app/utils.py` or `app/client_resolution.py` for mailbox patterns:

```python
r"\b5010\d{3}\b"
```

This should be the first explicit client resolution attempt.

### BFSAI schema additions

Add client-related columns to the `documents` table:

```sql
ALTER TABLE documents ADD COLUMN client_id TEXT;
ALTER TABLE documents ADD COLUMN client_mailbox TEXT;
ALTER TABLE documents ADD COLUMN client_match_status TEXT;
ALTER TABLE documents ADD COLUMN client_match_confidence REAL;
ALTER TABLE documents ADD COLUMN client_match_method TEXT;
```

### BFSCdx / Rails schema proposal

If BFSCdx is the client system of record, add or confirm:

#### `clients`

```ruby
class CreateClients < ActiveRecord::Migration[7.1]
  def change
    create_table :clients do |t|
      t.string :name, null: false
      t.string :mailbox_number
      t.string :legacy_client_code
      t.string :status, null: false, default: "active"
      t.timestamps
    end

    add_index :clients, :mailbox_number, unique: true
  end
end
```

## Phase 3: Payee Resolution Without Preexisting Payee Dependency

### Goal

Extraction should always succeed, even when no `payee_account` exists.

### Required code changes

#### 1. Keep raw vendor and match result separate

Extend [app/schemas.py](/Users/ajfarren/Projects/aq/bfsai/app/schemas.py) or store this in `documents.extraction_json`:

- `raw_vendor_name`
- `matched_payee_account_id`
- `payee_match_status`
- `payee_match_confidence`
- `payee_match_method`

#### 2. New payee resolver

Add:

- `app/payee_resolution.py`

Responsibilities:

- exact alias matching
- account number matching
- fuzzy alias matching
- historical match suggestions

#### 3. Review UI changes

Update [app/templates/document.html](/Users/ajfarren/Projects/aq/bfsai/app/templates/document.html) to show:

- resolved client
- payee match status
- suggested payees
- inline create-payee action

### BFSCdx / Rails schema proposal

#### `payee_accounts`

Assuming this table already exists, confirm or add:

```ruby
class AddApDefaultsToPayeeAccounts < ActiveRecord::Migration[7.1]
  def change
    add_reference :payee_accounts, :client, foreign_key: true
    add_column :payee_accounts, :default_category, :string
    add_column :payee_accounts, :default_expense_account, :string
    add_column :payee_accounts, :default_memo, :string
    add_column :payee_accounts, :default_payment_method, :string
    add_column :payee_accounts, :tax_code, :string
    add_column :payee_accounts, :normalized_name, :string
    add_index :payee_accounts, [:client_id, :normalized_name]
  end
end
```

#### `payee_aliases`

```ruby
class CreatePayeeAliases < ActiveRecord::Migration[7.1]
  def change
    create_table :payee_aliases do |t|
      t.references :payee_account, null: false, foreign_key: true
      t.string :alias_text, null: false
      t.string :normalized_alias_text, null: false
      t.string :match_type
      t.decimal :confidence_boost, precision: 6, scale: 4
      t.timestamps
    end

    add_index :payee_aliases, :normalized_alias_text
    add_index :payee_aliases, [:payee_account_id, :normalized_alias_text], unique: true, name: "idx_payee_aliases_unique_alias"
  end
end
```

## Phase 4: Canonical Bill Records

### Goal

Split document approval from AP bill lifecycle.

### Required code changes

#### 1. New bill service

Add:

- `app/bills.py`

Responsibilities:

- create bill from approved document
- update bill state
- mark paid
- list bills eligible for QIF export

#### 2. Approval flow change

Current behavior in [app/main.py](/Users/ajfarren/Projects/aq/bfsai/app/main.py) approves a document and then immediately ingests it.

Target behavior:

- approving a document creates or updates a canonical bill
- approval does not imply payment
- payment is a separate user action

Specifically, replace the current auto-ingest step after approve with a bill creation/update step.

### BFSAI schema additions

Add bill tables to [app/repository.py](/Users/ajfarren/Projects/aq/bfsai/app/repository.py):

#### `bills`

```sql
CREATE TABLE IF NOT EXISTS bills (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL,
  client_id TEXT,
  client_mailbox TEXT,
  payee_account_id TEXT,
  raw_vendor_name TEXT,
  invoice_number TEXT,
  invoice_date TEXT,
  due_date TEXT,
  subtotal REAL,
  tax REAL,
  total REAL,
  amount_paid REAL,
  category TEXT,
  expense_account TEXT,
  memo TEXT,
  payment_method TEXT,
  status TEXT NOT NULL,
  hold_reason TEXT,
  approved_at TEXT,
  paid_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bills_status_due_date
ON bills (status, due_date, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_bills_client_invoice
ON bills (client_id, invoice_number, total);
```

#### `bill_events`

```sql
CREATE TABLE IF NOT EXISTS bill_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bill_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
```

### Suggested BFSCdx / Rails schema

```ruby
class CreateBills < ActiveRecord::Migration[7.1]
  def change
    create_table :bills do |t|
      t.references :client, foreign_key: true
      t.references :payee_account, foreign_key: true
      t.string :document_external_id
      t.string :raw_vendor_name
      t.string :invoice_number
      t.date :invoice_date
      t.date :due_date
      t.decimal :subtotal, precision: 12, scale: 2
      t.decimal :tax, precision: 12, scale: 2
      t.decimal :total, precision: 12, scale: 2
      t.decimal :amount_paid, precision: 12, scale: 2
      t.string :category
      t.string :expense_account
      t.string :memo
      t.string :payment_method
      t.string :status, null: false, default: "draft"
      t.string :hold_reason
      t.datetime :approved_at
      t.datetime :paid_at
      t.timestamps
    end

    add_index :bills, [:client_id, :invoice_number, :total]
    add_index :bills, [:status, :due_date]
  end
end
```

## Phase 5: Paid-Bill QIF Export

### Goal

Support the existing user workflow:

- bills are already paid
- user opens `Process QIF`
- user selects paid bills from a grid
- system generates a downloadable QIF

### Required code changes

#### 1. New QIF export service

Add:

- `app/qif.py`

Suggested interface:

```python
class QifExportService:
    def __init__(self, settings: Settings, repository: Repository): ...
    def list_export_candidates(self, client_id: str | None = None) -> list[dict[str, Any]]: ...
    def create_batch(self, bill_ids: list[str], created_by: str | None = None) -> dict[str, Any]: ...
    def render_qif(self, bills: list[dict[str, Any]]) -> str: ...
```

#### 2. New UI endpoints

Add routes to [app/main.py](/Users/ajfarren/Projects/aq/bfsai/app/main.py):

- `GET /qif`
- `POST /qif/batches`
- `GET /qif/batches/{batch_id}/download`

Suggested screen behavior:

- grid of paid, unexported bills
- checkboxes
- batch summary
- download button after generation

#### 3. New template

Add:

- `app/templates/qif.html`

### BFSAI schema additions

#### `qif_export_batches`

```sql
CREATE TABLE IF NOT EXISTS qif_export_batches (
  id TEXT PRIMARY KEY,
  filename TEXT NOT NULL,
  storage_path TEXT NOT NULL,
  sha256 TEXT,
  status TEXT NOT NULL,
  bill_count INTEGER NOT NULL DEFAULT 0,
  created_by TEXT,
  created_at TEXT NOT NULL
);
```

#### `qif_export_batch_items`

```sql
CREATE TABLE IF NOT EXISTS qif_export_batch_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id TEXT NOT NULL,
  bill_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (bill_id)
);
```

### Candidate query

`Process QIF` should query:

```sql
SELECT *
FROM bills
WHERE status = 'paid'
  AND id NOT IN (SELECT bill_id FROM qif_export_batch_items)
ORDER BY paid_at DESC, updated_at DESC;
```

### Suggested QIF mapping

At minimum:

- amount
- paid date
- payee
- memo
- category

Memo format:

- `<client_mailbox> | <invoice_number> | <payee>`

## Phase 6: Queue Changes

### Goal

Make the review queue populate itself from extraction plus payee defaults, while preserving a user review step.

### Required changes

Update [app/templates/document.html](/Users/ajfarren/Projects/aq/bfsai/app/templates/document.html) and review endpoints in [app/main.py](/Users/ajfarren/Projects/aq/bfsai/app/main.py) to support:

- resolved client display
- resolved payee display
- match badges
- category and expense account fields
- payment method field
- paid checkbox or mark-paid action
- hold reason

Suggested review actions:

- `Approve bill`
- `Approve and next`
- `Mark paid`
- `Hold`
- `Create payee`
- `Re-run extraction`

## Phase 7: Duplicate Controls

### Goal

Avoid duplicate bill creation and duplicate QIF export.

### Required controls

#### Bill duplicate rules

- same `sha256`
- same `client_id + payee_account_id + invoice_number`
- same `client_id + payee_account_id + total + invoice_date`

#### Export duplicate rule

- once a bill is inserted into `qif_export_batch_items`, it can no longer appear in `Process QIF`

## Recommended File-by-File Order

1. [app/config.py](/Users/ajfarren/Projects/aq/bfsai/app/config.py)
2. [config/settings.example.yaml](/Users/ajfarren/Projects/aq/bfsai/config/settings.example.yaml)
3. [app/repository.py](/Users/ajfarren/Projects/aq/bfsai/app/repository.py)
4. new `app/intake.py`
5. new `app/client_resolution.py`
6. [app/classifier.py](/Users/ajfarren/Projects/aq/bfsai/app/classifier.py)
7. [app/documents.py](/Users/ajfarren/Projects/aq/bfsai/app/documents.py)
8. [app/worker.py](/Users/ajfarren/Projects/aq/bfsai/app/worker.py)
9. [app/pipeline.py](/Users/ajfarren/Projects/aq/bfsai/app/pipeline.py)
10. [app/main.py](/Users/ajfarren/Projects/aq/bfsai/app/main.py)
11. new `app/bills.py`
12. new `app/qif.py`
13. new UI template(s)
14. tests

## First Concrete Slice To Build

If implementation starts now, the highest-leverage first slice is:

1. add `scan_root`
2. add intake sweep from `server/scans`
3. classify and route scans into client folders
4. keep bills in client `new`
5. copy credit card statements to `credit_card_statements`
6. add basic PO Box extraction and client match metadata

That slice changes the operating model immediately without forcing the full bill/QIF redesign in one shot.
