# Run! Demo Data Manifest

This file records the local source files and generated demo data that were packaged with the pathfinding demo.

## Packaged Source Documents

- `data/source_documents/Run_项目Proposal_TODO完整文档_v3.md`
  - Original hackathon proposal / TODO plan.
  - Used to define the P0 demo boundary and rule-engine requirements.

- `data/source_documents/北京换乘人工标注数据（需清洗）.docx`
  - Original manually labeled transfer / station-entry notes.
  - Parsed by `python -m src.backend.extract_manual_transfers`.

## Generated / Cleaned Data

- `data/manual_transfers.json`
  - Structured transfer and station-entry records extracted from the DOCX.
  - Used before Amap fallback when a matching manual station/transfer entry exists.

- `data/amap_cache.json`
  - Cached Amap ETA / geocoding responses used by the demo.
  - Contains no API key. It improves demo stability when the live API is slow or unavailable.

## Existing Repository Data Used By The Demo

- `列车时刻表20260501.csv`
  - Railway timetable snapshot used by `src/backend/rail_schedule.py`.

- `列车时刻表20260501_edges.csv`
  - Existing preprocessed railway edge file, still useful for the older `shortest_train_path.py`.

- `railbox_csv_地铁列车时刻表/`
  - Beijing subway timetable CSVs.
  - Current V0 does not depend on subway travel-time edges for the P0 rescue decision; it is reserved for the next iteration.

## Not Packaged

- Amap API key and DeepSeek API key are not stored in the repository.
- Runtime logs `data/demo_server.out` and `data/demo_server.err` are ignored.
- Python `__pycache__` folders are ignored.

