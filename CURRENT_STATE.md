# Easy PDF Extractor — Current State

**Date:** 2026-04-28  
**Branch:** main  

---

## 1. What the System Does

A FastAPI service that accepts scientific PDF files and returns:

- **Annotated PDF** — bounding boxes drawn on every page, color-coded by layout class.
- **Cleaned text** — body text reassembled from detected regions, de-noised, and ASCII-normalised.
- **Section extracts** — domain-specific sections (`methods`, `results`, `discussion`, `das`) pulled from the cleaned text.

---

## 2. Module Map

| File | Role |
|------|------|
| `api.py` | FastAPI app; 3 endpoints; upload handling |
| `pdf_processor.py` | Layout detection, bbox rendering, CSV export, text + section extraction |
| `extractor_helper.py` | `extract_section`, `remove_references_section`, all section term lists |
| `sections.py` | Raw term lists (METHODS_TERMS, RESULTS_TERMS, etc.) |
| `utils.py` | String cleaning, URL reconstruction, unicode stripping |

---

## 3. API Endpoints

| Method | Path | Input | Output |
|--------|------|-------|--------|
| `POST` | `/process-pdf/` | PDF file | Annotated PDF (bounding boxes) |
| `POST` | `/extract-text/` | PDF file + `omit_store_results` flag | `{"text": "..."}` |
| `POST` | `/extract-sections/` | PDF file + `section_type` + `omit_store_results` | `{"sections": {...}}` or `{section_type: "..."}` |

Valid `section_type` values: `methods`, `results`, `discussion`, `das`, `all`.

---

## 4. Processing Pipeline

```
PDF upload
    │
    ▼
pymupdf4llm.to_markdown()          ← layout detection, produces page_boxes
    │
    ▼
save_bboxes_csv()                  ← page_number, order, class_id, x0/y0/x1/y1
    │
    ▼
PDFTextExtractor._load_relevant_rows()   ← keep class_id 0 (headers) + 1 (text)
    │
    ▼
per-region pymupdf.get_text()      ← clip to bbox coords
    │
    ▼
utils.process_page_text()          ← clean_string → replace_text_with_links → join_text
utils.remove_unicode()             ← normalise hyphens, strip non-ASCII
    │
    ▼
join heuristic                     ← same-class + lowercase-next → space; else \n or \n\n
    │
    ▼
regex post-processing              ← insert \n before first word after section headings
clean_string() again               ← OCR artifact fixes
    │
    ▼
<pdf_stem>.txt saved to disk
    │
    ▼
extract_sections()                 ← remove_references_section → extract_section per type
```

---

## 5. Layout Detection Classes

| class_id | Labels | Color |
|----------|--------|-------|
| 0 | `section-header`, `title` | Red |
| 1 | `text`, `list-item` | Blue |
| 2 | `page-header`, `page-footer` | Gray |
| 3 | `picture` | Green |
| 4 | `caption` | Magenta |
| 5 | `table` | Orange |

Text extraction only uses class_id **0** and **1**.

---

## 6. Section Recognition

`extractor_helper.py` / `sections.py` define term lists for:

- `METHODS_TERMS` — 30+ variants (`Materials and Methods`, `Experimental procedures`, `research design and methods`, etc.)
- `RESULTS_TERMS` — 5 variants
- `DISCUSSION_TERMS` — 7 variants
- `DATA_AVAILABILITY` — 35+ variants
- Plus: `REFERENCES_TERMS`, `CONCLUSION`, `ABSTRACT`, `FUNDING`, `COI`, `AUTH_CONT`, `SUPP_DATA`, `ETHICS`, `ABBREVIATIONS`, `LIMITATIONS`, `INTRODUCTION`, `CAS`, `ACNOWLEDGEMENTS`

`extract_section` handles:
- Regular format (`Methods`)
- Spaced letters (`M E T H O D S`)
- Numbered sections (`1. Methods`, `I. Methods`)
- Pipe-separated (`1 | INTRODUCTION`)
- Inline with colon (`Methods: text...`)

Returns **longest match** when multiple candidates found.

---

## 7. Artefact Layout

```
uploads/
  <pdf_stem>/
    <original>.pdf

test/                          ← default output_dir
  <pdf_stem>/
    <pdf_stem>_processed.pdf   ← annotated bboxes
    <pdf_stem>_detections.csv  ← page, order, class_id, x0, y0, x1, y1
    <pdf_stem>.txt             ← cleaned extracted text
```

---

## 8. Key Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `FAST_API_PORT` | `8003` | Listen port |
| `FAST_API_WORKERS` | `4` | Uvicorn worker count |
| `omit_store_results` | `false` | Force re-processing even if artefacts exist |

---

## 9. Dependencies (key)

| Package | Version | Purpose |
|---------|---------|---------|
| `PyMuPDF` | 1.27.1 | PDF access, text clipping, bbox drawing |
| `pymupdf4llm` | 0.3.4 | Layout-aware metadata (`page_boxes`) |
| `pymupdf-layout` | 1.27.1 | Layout detection support |
| `fastapi` | 0.135.1 | HTTP API |
| `uvicorn` | 0.41.0 | ASGI server |
| `onnxruntime` | 1.24.2 | Present in deps, not actively used in current code |

Python: 3.13 (`.venv` pyc paths confirm).

---

## 10. What Works Well

- Layout detection and bbox visualisation on standard single/double-column scientific PDFs.
- Text cleaning pipeline reliably fixes known OCR artefacts (30+ hardcoded substitutions in `utils.clean_string`).
- Section extraction handles non-standard heading variants (`research design and methods`, `MATERIALS AND METHODS`, spaced letters, numbered headers).
- References removal before section extraction reduces false positives.
- `omit_store_results` flag allows cache bypass for fresh processing.
- Per-PDF artefact directories keep outputs isolated.

---

## 11. Known Limitations (currently cannot be fixed)

These are inherent to PDF layout complexity:

| Category | Description |
|----------|-------------|
| **Incomplete sections** | Some paragraphs or sections may not appear in the output |
| **Partial text** | Occasional sentences may be trimmed at region boundaries |
| **Column ordering** | In two-column layouts, text order may not always follow reading order |
| **Reordered content** | Paragraphs may occasionally appear out of sequence |
| **Header placement** | Section headers may sometimes land slightly out of position |
| **Figure/table legends** | Legends may appear near but not adjacent to their source content |
| **Repeated text** | Short fragments may occasionally be duplicated |
| **Character encoding** | Uncommon symbols or units may not render correctly |
| **Page metadata** | Headers, footers, or line numbers may appear in the extracted text |

---

## 12. Gaps / Missing Pieces

| Area | Status |
|------|--------|
| **Authentication** | None on any endpoint. Suitable for internal/trusted networks only. |
| **Upload size limits** | Not enforced at the API level. |
| **Async processing** | All processing is synchronous within the request. Large PDFs may block the worker. |
| **Table extraction** | Tables detected (class_id 5) and visualised but not extracted into structured form. |
| **Cleanup / retention** | No artefact cleanup policy; `uploads/` and `test/` grow unbounded. |
