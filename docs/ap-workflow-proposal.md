# AP Intake, Review, and QIF Export Proposal

## Goal

Use BFSAI as the intake and review layer for incoming bills while keeping Quicken as the payment export target for now.

The system should:

- identify the client first, using the assigned PO Box whenever available
- extract bill data even if no payee account exists yet
- auto-populate the review queue as much as possible
- require user review before approval/payment
- generate QIF files only from bills already marked as paid
- avoid duplicate exports

## Intake Landing Zone

All newly scanned documents land first in:

- `server/scans`

That directory is the raw intake area before client classification and routing.

## Core Principles

1. PO Box is the first client-match signal.
2. Document classification and routing happen before extraction.
3. Extraction must not depend on a preexisting payee account.
4. Vendor/payee resolution happens after extraction, not before it.
5. Approval and payment are separate states.
6. QIF export only operates on paid, unexported bills.

## Document Classification and Routing

Classification is the first processing step after a file lands in `server/scans`.

Each client folder, such as `5010234`, contains subdirectories for each document type. The classifier determines both document type and route destination.

### Routing rules

- if the document is a bill, move it into that client's `/new`
- if the document is a credit card statement, move it into that client's `/new` and copy it to `/credit_card_statements`
- if the document is another supported document type, move it into that client's respective type folder
- if the type is unknown or unsupported, move it into that client's `/other`

### Why bills stay in `/new`

`/new` is the active work queue for documents that still need extraction and AP review.

Bills and credit card statements both continue through extraction and review, so they remain actionable in `/new` even if a typed copy is also stored elsewhere.

### Example client folder structure

For client `5010234`:

- `5010234/new`
- `5010234/review`
- `5010234/processed`
- `5010234/output`
- `5010234/bills`
- `5010234/credit_card_statements`
- `5010234/statements`
- `5010234/deposits`
- `5010234/insurances`
- `5010234/taxes`
- `5010234/other`

### Classification outputs

The classification step should produce:

- `document_type`
- `classification_confidence`
- `classification_reason`
- `target_client_id`, if known
- `target_client_mailbox`, if known
- `route_action`
- `target_path`

Recommended `route_action` values:

- `move_to_new`
- `move_to_type_folder`
- `copy_to_type_folder`
- `move_to_other`

## Client Identification

### Primary rule

Each active client should have a distinct mailbox number in the format:

- `5010` + 3-digit client suffix
- examples: `5010012`, `5010350`

When a document arrives, BFSAI should try to derive `client_id` in this order:

1. exact PO Box match from extracted document text
2. PO Box inferred from the intake folder or scan route
3. legacy fallback heuristics:
   - vendor history for that intake source
   - service address match
   - account number match
   - manual user selection

### Legacy handling

Some legacy clients will not have a distinct PO Box. Those documents should still be accepted and queued with:

- `client_match_status = unresolved`
- `needs_client_review = true`

That keeps the document moving without silently assigning it to the wrong client.

## End-to-End Workflow

### 1. Intake

Document lands in `server/scans`.

Create a raw intake record immediately with:

- source file path
- original filename
- intake timestamp
- intake source

### 2. Classification and Routing

Run document classification before extraction.

From `server/scans`, route the file into the correct client folder:

- bill -> `<client_mailbox>/new`
- credit card statement -> `<client_mailbox>/new` and copy to `<client_mailbox>/credit_card_statements`
- other typed document -> `<client_mailbox>/<document_type_folder>`
- unknown -> `<client_mailbox>/other`

If classification says the document should not be extracted, processing can stop after routing.

If the document should be extracted, continue to OCR and downstream resolution.

### 3. Client Resolution

Run client matching before vendor matching.

Possible results:

- `matched`
- `suggested`
- `unresolved`

If matched by PO Box with high confidence, attach `client_id`.

### 4. Extraction

Extract bill fields regardless of whether a vendor or payee account exists.

Required extraction targets:

- raw vendor name
- remit/payable name
- invoice number
- invoice date
- due date
- subtotal
- tax
- total
- amount due
- account number
- service address
- remittance address
- line items
- detected PO Box

### 5. Payee Resolution

After extraction, try to map the document to an existing `payee_account`.

Priority:

1. exact alias match
2. account number match
3. remittance/payable name match
4. fuzzy vendor alias match
5. historical approved-match pattern for the client

Possible outcomes:

- `matched`
- `suggested`
- `unresolved`

If matched, auto-fill:

- category
- expense account
- default memo
- payment method
- tax handling

### 6. Verification

Run automated checks:

- total present
- vendor present
- invoice number present
- invoice date present
- subtotal + tax = total within tolerance
- amount due vs total mismatch
- possible duplicate bill
- missing client
- missing payee account
- missing category
- unusual amount for this payee/client

### 7. Review Queue

Queue items should pre-populate everything possible and highlight only exceptions.

Recommended queue classes:

- `green`: client matched, payee matched, category filled, totals reconcile
- `yellow`: one or two unresolved or low-confidence fields
- `red`: duplicate risk, high amount, unresolved client, unresolved payee, failed math

User actions:

- approve bill
- edit extracted fields
- create/select payee account inline
- mark paid
- hold
- reject

### 8. Payment and QIF Export

The QIF flow should stay aligned to your existing process:

1. user marks bills as paid
2. user opens `Process QIF`
3. system shows a grid of paid, unexported bills
4. user checks which bills to include
5. system generates one downloadable QIF file
6. selected bills are marked with an export batch id and timestamp

This prevents re-exporting the same payment accidentally.

## Proposed State Model

### Document-level states

- `new`
- `classified`
- `extracted`
- `review`
- `approved`
- `rejected`
- `error`

### Bill-level states

- `draft`
- `approved`
- `held`
- `paid`
- `exported`
- `reconciled`
- `void`

### Match states

- `matched`
- `suggested`
- `unresolved`

## Proposed Schema

The names below are intended as concrete targets. They can be adapted to Rails naming conventions if BFSCdx is the system of record.

### `clients`

Stores canonical client identity.

Suggested columns:

- `id`
- `name`
- `mailbox_number` varchar, unique, nullable for legacy clients
- `legacy_client_code` varchar, nullable
- `status`
- `created_at`
- `updated_at`

Constraint:

- unique index on `mailbox_number`

### `payee_accounts`

Existing concept, expanded as the accounting mapping layer.

Suggested columns:

- `id`
- `client_id`
- `display_name`
- `normalized_name`
- `default_category`
- `default_expense_account`
- `default_memo`
- `default_payment_method`
- `tax_code`
- `active`
- `created_at`
- `updated_at`

Indexes:

- `(client_id, normalized_name)`

### `payee_aliases`

Lets extraction succeed before the canonical payee is known.

Suggested columns:

- `id`
- `payee_account_id`
- `alias_text`
- `normalized_alias_text`
- `match_type` varchar
- `confidence_boost` decimal
- `created_at`
- `updated_at`

Indexes:

- unique `(payee_account_id, normalized_alias_text)`
- index on `normalized_alias_text`

### `document_intakes`

Raw intake record for any scanned/uploaded document.

Suggested columns:

- `id`
- `source_path`
- `original_filename`
- `sha256`
- `intake_source`
- `ocr_text`
- `page_count`
- `status`
- `created_at`
- `updated_at`

### `document_extractions`

Stores machine-extracted data, even when unresolved.

Suggested columns:

- `id`
- `document_intake_id`
- `detected_mailbox_number`
- `client_id`, nullable
- `client_match_status`
- `client_match_confidence`
- `raw_vendor_name`
- `raw_payable_to`
- `invoice_number`
- `invoice_date`
- `due_date`
- `account_number`
- `service_address`
- `remittance_address`
- `subtotal`
- `tax`
- `total`
- `amount_due`
- `currency`
- `line_items_json`
- `field_confidence_json`
- `alignment_json`
- `verification_json`
- `status`
- `created_at`
- `updated_at`

Indexes:

- `document_intake_id`
- `client_id`
- `(detected_mailbox_number, invoice_number)`

### `payee_match_candidates`

Stores candidate payee matches produced by the resolver.

Suggested columns:

- `id`
- `document_extraction_id`
- `payee_account_id`
- `match_status`
- `match_method`
- `confidence`
- `reason`
- `rank`
- `created_at`

Indexes:

- `(document_extraction_id, rank)`

### `bills`

Canonical payable record created from an extraction after queue review.

Suggested columns:

- `id`
- `document_extraction_id`
- `client_id`
- `payee_account_id`, nullable until resolved
- `raw_vendor_name`
- `invoice_number`
- `invoice_date`
- `due_date`
- `subtotal`
- `tax`
- `total`
- `amount_paid`
- `category`
- `expense_account`
- `memo`
- `payment_method`
- `status`
- `hold_reason`
- `approved_by_user_id`
- `approved_at`
- `paid_at`
- `created_at`
- `updated_at`

Indexes:

- `(client_id, status, due_date)`
- `(payee_account_id, invoice_number, total)`

### `bill_duplicates`

Tracks possible duplicate relationships.

Suggested columns:

- `id`
- `bill_id`
- `duplicate_bill_id`
- `duplicate_type`
- `confidence`
- `reason`
- `created_at`

### `qif_export_batches`

Represents one downloadable QIF file.

Suggested columns:

- `id`
- `created_by_user_id`
- `filename`
- `storage_path`
- `sha256`
- `bill_count`
- `status`
- `created_at`

### `qif_export_batch_items`

Links bills to the export batch.

Suggested columns:

- `id`
- `qif_export_batch_id`
- `bill_id`
- `created_at`

Constraint:

- unique `(bill_id)` if a bill can only be exported once

## Queue Behavior

### Auto-populate rules

If `client_id` and `payee_account_id` are resolved, queue should default:

- category from `payee_accounts.default_category`
- expense account from `payee_accounts.default_expense_account`
- memo from vendor/client template
- due date from extraction or payment terms

### Inline payee creation

If no payee exists:

- user can create a new payee account directly in the queue
- current extracted vendor string is saved as first alias
- selected category/account defaults are saved to the new payee
- current bill is rehydrated immediately without leaving the queue

This removes the current dependency on preloading payees before extraction.

## Matching Logic

### Client match

1. exact mailbox number extracted from OCR
2. mailbox number inferred from intake route
3. legacy client heuristic

### Payee match

1. exact alias text match
2. exact account number match against known payee metadata
3. exact remittance/payable name match
4. fuzzy alias match
5. prior confirmed payee used for same client + similar vendor text

### Duplicate detection

Flag if any of the following are true:

- same file hash
- same client + same payee + same invoice number
- same client + same payee + same amount + same invoice date
- same client + same account number + same amount within configurable date window

## QIF Export Contract

`Process QIF` should query only bills where:

- `status = 'paid'`
- not already linked to a `qif_export_batch_items` record
- not voided

Grid columns:

- checkbox
- client
- payee
- invoice number
- invoice date
- paid date
- amount
- category
- memo

On submit:

1. create batch
2. lock selected bills
3. generate QIF content
4. persist batch file
5. mark bills `exported`
6. return downloadable file response

Recommended QIF item memo:

- client name or mailbox
- invoice number
- payee display name

## Suggested API / Service Boundaries

### Intake service

- receives document
- watches `server/scans`
- stores raw file
- creates `document_intakes`

### Classification service

- classifies document type
- resolves route target
- moves bills into client `new`
- copies credit card statements into `credit_card_statements`
- moves non-bill documents into their type folders

### Extraction service

- OCR
- field extraction
- PO Box detection
- writes `document_extractions`

### Client resolver

- resolves `client_id`
- sets client match metadata

### Payee resolver

- creates candidate matches
- may auto-resolve high confidence cases

### Review queue service

- builds user-facing queue rows
- computes warning badges

### Bill service

- creates/updates canonical `bills`
- tracks approval/payment transitions

### QIF export service

- lists eligible paid bills
- builds export batch
- writes QIF file

## Implementation Phases

### Phase 1

- add watcher/ingest flow for `server/scans`
- add classification-first routing into client subdirectories
- add nullable client and payee resolution to extraction flow
- stop requiring payee account before extraction
- add PO Box-first client resolution
- add unresolved queue handling

### Phase 2

- add canonical `bills` table
- split approval from payment
- add paid state and hold state

### Phase 3

- add `Process QIF` batch model
- add paid-bill selection grid
- generate downloadable QIF
- mark exported bills to prevent duplicates

### Phase 4

- add inline payee-account creation from queue
- feed created aliases back into resolver
- improve confidence scoring from review history

## Practical Recommendation For BFSAI

Given the current BFSAI codebase, the shortest path is:

1. keep the existing extraction, verification, and review UI
2. add client resolution based on extracted PO Box before vendor learning
3. change approval so it creates an approved bill record but does not imply payment
4. add a separate paid-bill grid and QIF export batch flow
5. move vendor learning from "must exist before extraction" to "gets better after every confirmation"

That preserves the good parts of the current pipeline while fixing the core blocker in the Kofax-era workflow.
