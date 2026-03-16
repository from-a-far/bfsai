# bfsai

Local invoice extraction, review, training-corpus generation, and Rails ingestion.

## Admin pages

- Dashboard: `http://127.0.0.1:8001/`
- Training: `http://127.0.0.1:8001/training`
- Services: `http://127.0.0.1:8001/services`

## Strategy switching

Two extraction strategies are available:

- `legacy_local`: the current OCR + heuristic + learning workflow
- `ppstruct_layoutlm_qwen`: experimental PP-StructureV3 + LayoutLMv3 + Qwen2.5-VL-7B profile

The active strategy is read from `config/settings.yaml` and can be overridden at runtime via:

```bash
python scripts/switch_strategy.py legacy_local
python scripts/switch_strategy.py ppstruct_layoutlm_qwen
```

Runtime overrides are written to `storage/runtime/active_strategy.json`.

## Training corpus

When a document is approved, BFSAI exports a corpus example into:

```text
storage/training_corpus/examples/<document_id>/
```

Each example includes:

- approved source document
- approved JSON bundle
- `ground_truth.json`
- `alignment.json`
- `manifest.json`

The training page lets you select approved examples, build a corpus manifest, benchmark a candidate strategy, and activate it if the benchmark score improves.

Training run artifacts are written to:

```text
storage/training_runs/<run_id>/
```

Including:

- `manifest.json`
- `qwen_train.jsonl`
- `layoutlm_annotations.jsonl`

## Service management

The services page wraps the local management script:

```bash
python scripts/manage_services.py status
python scripts/manage_services.py start all
python scripts/manage_services.py restart worker
python scripts/manage_services.py stop api
```

This manages host-run `api` and `worker` processes and stores pid/log files under:

```text
storage/runtime/services/
```

## Experimental model endpoints

The experimental strategy is scaffolded to use a Qwen-compatible OpenAI-style chat endpoint if `qwen_base_url` is configured in `config/settings.yaml`.

If no compatible endpoint is configured, the strategy remains switchable and benchmarkable but falls back to the local extractor while still producing training manifests for future PP-Structure/LayoutLM/Qwen training work.
