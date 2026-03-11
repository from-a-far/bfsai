
# AGENTS.md

## Overview
Docker-based local AI system for extracting structured data from invoices, integrated with a Rails application. This document defines agents responsible for extraction, alignment, verification, and database ingestion.

## Agent Definitions

### 1. **Invoice Extractor Agent**
Extracts structured data from invoice documents using local LLM/OCR.

**Responsibilities:**
- Parse invoice images/PDFs
- Extract: vendor, amount, date, line items, tax, payment terms
- Output: JSON with extraction confidence scores
- Store raw extractions temporarily

**Inputs:** Invoice file path
**Outputs:** Structured extraction JSON

---

### 2. **Document Alignment Agent**
Links extracted data back to source documents for traceability.

**Responsibilities:**
- Map extraction results to original invoice files
- Generate alignment metadata (page numbers, field coordinates)
- Create audit trail for data lineage
- Store document references in database

**Inputs:** Extraction JSON, document path
**Outputs:** Aligned extraction record with document pointers

---

### 3. **Verification Agent**
Validates extractions automatically against business rules and heuristics.

**Responsibilities:**
- Cross-check totals (sum of line items vs. reported total)
- Validate formats (dates, amounts, vendor names)
- Flag anomalies (unusually high amounts, invalid tax rates)
- Compare against historical vendor data from RoR database
- Assign verification status: `verified`, `flagged`, `requires_review`

**Inputs:** Aligned extraction record
**Outputs:** Verification report with confidence score

---

### 4. **Database Ingestion Agent**
Persists verified extractions into Rails application database.

**Responsibilities:**
- Write extractions to appropriate models
- Update invoice status in RoR app
- Handle conflicts/duplicates
- Maintain referential integrity

**Inputs:** Verified extraction record
**Outputs:** Database transaction confirmation

---

## Data Flow

```
Invoice File → Extractor → Alignment → Verification → Ingestion → RoR DB
```

## Architecture Notes
- All agents run in isolated Docker containers
- Shared filesystem mount for document access
- Database connection pooling to Rails app
- Message queue for agent orchestration (optional)
