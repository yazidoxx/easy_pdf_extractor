"""PDF layout and text extraction utilities built on PyMuPDF.

This module provides two main capabilities:

* Layout detection and visualisation of bounding boxes for different
  content types (sections, text, tables, etc.).
* Text extraction driven by pre-computed detection CSVs, with domain
  specific post-processing applied to the extracted text.

The external behaviour of the original implementation is preserved.
"""

from __future__ import annotations

import csv
import re
import logging
from pathlib import Path
from typing import Any, Iterable, Mapping

import pymupdf  # PyMuPDF
import pymupdf.layout  # type: ignore[import-untyped]
import pymupdf4llm  # type: ignore[import-untyped]

from utils import extract_links, process_page_text, remove_unicode, clean_string
from extractor_helper import extract_section, remove_references_section, METHODS_TERMS, DISCUSSION_TERMS, RESULTS_TERMS, DATA_AVAILABILITY

PageBoxes = Mapping[str, Any]


# Consistent class → color mapping (RGB in 0–1 range)
CLASS_COLOR_MAP: dict[str, tuple[float, float, float]] = {
    "section-header": (1, 0, 0),  # red
    "title": (1, 0, 0),  # treat title same as section-header
    "text": (0, 0, 1),  # blue
    "list-item": (0, 0, 1),  # blue
    "page-header": (0.4, 0.4, 0.4),  # dark gray
    "page-footer": (0.4, 0.4, 0.4),  # dark gray
    "picture": (0, 0.6, 0),  # green
    "caption": (0.8, 0, 0.8),  # magenta
    "table": (1, 0.5, 0),  # orange
    "unknown": (0, 0, 0),  # black fallback
}

# Mapping from string class to numeric id for CSV export.
CLASS_ID_MAP: dict[str, int] = {
    "section-header": 0,
    "title": 0,  # treat title same as section-header
    "text": 1,
    "list-item": 1,
    "page-header": 2,
    "page-footer": 2,
    "picture": 3,
    "caption": 4,
    "table": 5,
}


def get_page_boxes(pdf_path: str | Path, pages: Iterable[int] | None = None) -> list[dict[str, Any]]:
    """Extract `page_boxes` for each page from a PDF.

    Args:
        pdf_path: Path to the PDF file.
        pages: Optional iterable of 0-based page indices to process.

    Returns:
        A list of dictionaries, one per page, each with:

        * ``"page_number"`` – 1-based page number.
        * ``"page_boxes"`` – list of bounding boxes on that page.
    """
    pdf_path = Path(pdf_path)

    # Open as a Document so we can pass it to pymupdf4llm.
    with pymupdf.open(str(pdf_path)) as doc:
        # Build kwargs for to_markdown.
        kwargs: dict[str, Any] = {
            "page_chunks": True,
            # Minimize extra work if you don't need text/images/etc.
            "use_ocr": False,
            "force_text": False,
            "write_images": False,
            "embed_images": False,
            "show_progress": True,
        }
        if pages is not None:
            kwargs["pages"] = list(pages)

        chunks = pymupdf4llm.to_markdown(doc, **kwargs)

    # Each chunk is a dict; we only keep page_number + page_boxes.
    results: list[dict[str, Any]] = []
    for chunk in chunks:
        page_number = chunk["metadata"]["page_number"]
        page_boxes = chunk.get("page_boxes", [])
        results.append(
            {
                "page_number": page_number,
                "page_boxes": page_boxes,
            }
        )

    return results


def draw_bboxes_on_pdf(
    pdf_path: str | Path,
    boxes_per_page: Iterable[PageBoxes],
    output_dir: str | Path = "test",
) -> str:
    """Draw bounding boxes onto a copy of the PDF and save it.

    The output is written to ``output_dir/<pdf_stem>/<pdf_stem>_processed.pdf``,
    matching the original behaviour.

    Args:
        pdf_path: Path to the source PDF.
        boxes_per_page: Iterable of page-level dictionaries, each with
            ``"page_number"`` and ``"page_boxes"`` as produced by
            :func:`get_page_boxes`.
        output_dir: Base directory for generated artefacts.

    Returns:
        The path to the processed PDF as a string.
    """
    pdf_path = Path(pdf_path)
    output_dir = Path(output_dir)

    # Create a subfolder inside output_dir named after the PDF (without extension).
    pdf_out_dir = output_dir / pdf_path.stem
    pdf_out_dir.mkdir(parents=True, exist_ok=True)

    # Build a mapping from 1-based page number to list of boxes.
    page_map = {
        int(item["page_number"]): item.get("page_boxes", [])
        for item in boxes_per_page
    }

    with pymupdf.open(str(pdf_path)) as doc:
        for page_index in range(len(doc)):
            page = doc[page_index]
            page_number = page_index + 1  # 1-based to match metadata
            boxes = page_map.get(page_number, [])

            for fallback_order, box in enumerate(boxes, start=1):
                # Support both dicts with 'bbox' and plain [x0, y0, x1, y1] lists.
                if isinstance(box, dict):
                    coords = box.get("bbox") or box.get("rect") or box.get("box")
                    class_name = (
                        box.get("class")
                        or box.get("type")
                        or box.get("label")
                        or box.get("kind")
                        or "unknown"
                    )
                    if isinstance(box.get("index"), int):
                        order = int(box["index"]) + 1
                    else:
                        order = fallback_order
                else:
                    coords = box
                    class_name = "unknown"
                    order = fallback_order

                if not coords or len(coords) != 4:
                    continue

                x0, y0, x1, y1 = coords
                rect = pymupdf.Rect(x0, y0, x1, y1)

                # Pick color based on class.
                color = CLASS_COLOR_MAP.get(class_name, CLASS_COLOR_MAP["unknown"])
                page.draw_rect(rect, color=color, width=0.5)

                # Label positioned just above the top-left corner of the bbox.
                label = f"{order} {class_name}"
                label_pos = pymupdf.Point(rect.x0, rect.y0 - 2)
                page.insert_text(
                    label_pos,
                    label,
                    fontsize=6,
                    color=color,
                    overlay=True,
                )

        out_path = pdf_out_dir / f"{pdf_path.stem}_processed.pdf"
        doc.save(str(out_path))

    return str(out_path)


def save_bboxes_csv(
    pdf_path: str | Path,
    boxes_per_page: Iterable[PageBoxes],
    output_dir: str | Path = "test",
) -> str:
    """Save detections as a CSV with one row per box.

    Columns: ``page_number, order, class_id, x0, y0, x1, y1``.

    ``class_id`` mapping (preserved from original code):

    * 0: section-header / title
    * 1: text / list-item
    * 2: page-header / page-footer
    * 3: picture
    * 4: caption
    * 5: table
    """
    pdf_path = Path(pdf_path)
    output_dir = Path(output_dir)

    # Mirror the same per-PDF subfolder structure as draw_bboxes_on_pdf.
    pdf_out_dir = output_dir / pdf_path.stem
    pdf_out_dir.mkdir(parents=True, exist_ok=True)

    out_csv = pdf_out_dir / f"{pdf_path.stem}_detections.csv"

    with out_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["page_number", "order", "class_id", "x0", "y0", "x1", "y1"])

        for page in boxes_per_page:
            page_number = page.get("page_number")
            boxes = page.get("page_boxes", [])

            for fallback_order, box in enumerate(boxes, start=1):
                if isinstance(box, dict):
                    coords = box.get("bbox") or box.get("rect") or box.get("box")
                    label = (
                        box.get("class")
                        or box.get("type")
                        or box.get("label")
                        or box.get("kind")
                    )
                    if isinstance(box.get("index"), int):
                        order = int(box["index"]) + 1
                    else:
                        order = fallback_order
                else:
                    coords = box
                    label = None
                    order = fallback_order

                if not coords or len(coords) != 4:
                    continue

                x0, y0, x1, y1 = coords

                # Map label to numeric class_id, defaulting to 1 (text/list-item).
                if isinstance(label, str):
                    class_id = CLASS_ID_MAP.get(label, 1)
                else:
                    class_id = 1

                writer.writerow([page_number, order, class_id, x0, y0, x1, y1])

    return str(out_csv)


class PDFLayoutProcessor:
    """Run layout detection on a PDF and persist artefacts.

    This small helper encapsulates the multi-step workflow of:

    * running layout detection,
    * drawing bounding boxes on a copy of the PDF, and
    * exporting detections to a CSV file.
    """

    def process_pdf(self, pdf_path: str | Path, output_dir: str | Path = "test") -> tuple[str, str]:
        """Process ``pdf_path`` and return paths to processed PDF and CSV."""
        boxes_per_page = get_page_boxes(pdf_path)
        processed_pdf = draw_bboxes_on_pdf(pdf_path, boxes_per_page, output_dir=output_dir)
        detections_csv = save_bboxes_csv(pdf_path, boxes_per_page, output_dir=output_dir)
        return processed_pdf, detections_csv


class PDFTextExtractor:
    """Extract cleaned text from a PDF using detection CSV results."""

    def __init__(
        self,
        pdf_path: str | Path,
        output_dir: str | Path = "test",
        results_csv: str | Path | None = None,
        use_store_results: bool = False,
    ) -> None:
        self.pdf_path = Path(pdf_path)
        self.output_dir = Path(output_dir)
        self.pdf_name = self.pdf_path.stem
        self.use_store_results = use_store_results

        # Where processed artifacts (processed PDF + detections CSV) live.
        self.pdf_dir = self.output_dir / self.pdf_name
        self.pdf_dir.mkdir(parents=True, exist_ok=True)

        if results_csv is not None:
            self.results_csv = Path(results_csv)
        else:
            # Default location that matches save_bboxes_csv.
            self.results_csv = self.pdf_dir / f"{self.pdf_name}_detections.csv"

    def _ensure_results_csv(self) -> None:
        """Ensure that a detection CSV exists for this PDF."""
        if self.use_store_results or not self.results_csv.exists():
            logging.info("Generating detection results for PDF: %s", self.pdf_path)
            processor = PDFLayoutProcessor()
            _, output_csv = processor.process_pdf(self.pdf_path, self.output_dir)
            self.results_csv = Path(output_csv)

    def _load_relevant_rows(self) -> list[dict[str, str]]:
        """Load detection rows for class IDs 0 and 1 from the CSV."""
        with self.results_csv.open("r", encoding="utf-8") as f:
            csv_reader = csv.DictReader(f)
            return [row for row in csv_reader if int(row["class_id"]) in (0, 1)]

    def _rect_from_row(self, row: Mapping[str, str]) -> pymupdf.Rect:
        """Build a PyMuPDF Rect from a CSV row."""
        return pymupdf.Rect(
            float(row["x0"]),
            float(row["y0"]),
            float(row["x1"]),
            float(row["y1"]),
        )

    def extract_text(self) -> str:
        """Extract text from PDF using detection results and post-processing logic.

        This method extracts text from title and plain text regions (class_id 0
        and 1) while maintaining proper spacing and formatting between text
        blocks, exactly as in the original implementation.

        Returns:
            The extracted and post-processed text content.
        """
        self._ensure_results_csv()

        output_txt = self.pdf_dir / f"{self.pdf_name}.txt"

        try:
            with pymupdf.open(str(self.pdf_path)) as doc:
                extracted_text: list[str] = []
                rows = self._load_relevant_rows()

                # Process each row.
                for i in range(len(rows)):
                    current_row = rows[i]
                    current_page = doc[int(current_row["page_number"]) - 1]

                    # Our coordinates are already in PDF space, so no DPI scaling needed.
                    current_rect = self._rect_from_row(current_row)

                    current_text = current_page.get_text("text", clip=current_rect, flags=0)
                    current_links = extract_links(current_page.get_links())
                    current_text = process_page_text(current_text, current_links)
                    current_text = remove_unicode(current_text)

                    if not current_text:
                        continue

                    # If this is not the last row, check the next row.
                    if i < len(rows) - 1:
                        next_row = rows[i + 1]
                        next_page = doc[int(next_row["page_number"]) - 1]

                        next_rect = self._rect_from_row(next_row)

                        next_text = next_page.get_text("text", clip=next_rect, flags=0)
                        next_links = extract_links(next_page.get_links())
                        next_text = process_page_text(next_text, next_links)
                        next_text = remove_unicode(next_text)

                        # Check if current and next elements are from the same class.
                        if current_row["class_id"] == next_row["class_id"]:
                            # If next text starts with lowercase, join with space.
                            if next_text and next_text[0].islower():
                                extracted_text.append(current_text + " ")
                            else:
                                extracted_text.append(current_text + "\n")
                        else:
                            # Different classes, join with double newline.
                            extracted_text.append(current_text + "\n\n")
                    else:
                        # Last row, just append the text.
                        extracted_text.append(current_text)

            # Join all text and save to file.
            final_text = "".join(extracted_text)
            final_text = clean_string(final_text)
            # Apply a re.sub rule for each main section term (including multi-word terms):
            for section, terms in {
                "Methods": METHODS_TERMS,
                "Discussion": DISCUSSION_TERMS,
                "Results": RESULTS_TERMS,
                "Data availability": DATA_AVAILABILITY,
            }.items():
                for term in terms:
                    # Normalise and escape the term (handles things like trailing ":" etc.)
                    safe_term = re.escape(term.strip(":").strip())
                    # Insert a newline between the full (possibly multi-word) term and the next capitalised word
                    # Example: "MATERIALS AND METHODS Animal Protocols"
                    #   => "MATERIALS AND METHODS\nAnimal Protocols"
                    # (?mi) => multiline + case-insensitive, allow optional leading whitespace
                    pattern = rf'(?mi)^\s*({safe_term})(\s+)([A-Z][a-zA-Z]*)'
                    final_text = re.sub(pattern, r'\1\n\3', final_text)
            with output_txt.open("w", encoding="utf-8") as f:
                f.write(final_text)

            logging.info("Extracted text saved to: %s", output_txt)
            return final_text

        except Exception as exc:  # noqa: BLE001 - preserve broad error handling
            logging.error("Error extracting text from PDF: %s", exc)
            return ""
        
    def extract_sections(self, section_type=None):
        """
        Extracts specified sections from the given PDF.
        :param section_type: Type of section to extract. If None, extracts all sections.
        
        :return: Dictionary of extracted sections.
        """
        pdf_txt = self.pdf_dir / f"{self.pdf_name}.txt"
        if self.use_store_results or not Path(pdf_txt).exists():
            logging.info("Extracting text from PDF: %s", self.pdf_path)
            self.extract_text()
        
        # Read text and remove references before extracting sections
        with open(pdf_txt, 'r') as f:
            text = f.read()
        
        # Remove references section before extracting any sections
        text_with_no_ref = remove_references_section(text)
       
        # Define terms based on the section type
        section_terms = {
            "methods": METHODS_TERMS,
            "discussion": DISCUSSION_TERMS,
            "results": RESULTS_TERMS,
            "das": DATA_AVAILABILITY  
        }

        # If section_type is None or 'all', extract all sections
        if section_type is None or section_type.lower() == 'all' or not section_type.strip():
            extracted_sections = {}
            for section_name, terms in section_terms.items():
                section_text = extract_section(text_with_no_ref, terms)
                extracted_sections[section_name] = section_text
            return extracted_sections
        
        # Extract the specified section
        terms = section_terms.get(section_type.lower())
        if terms:
            extracted_section = extract_section(text_with_no_ref, terms)
            return extracted_section if extracted_section else ""
        return ""


def extract_text_with_boxes(
    pdf_path: str | Path,
    boxes_per_page: Iterable[PageBoxes] | None = None,
    output_dir: str | Path = "test",
    use_store_results: bool = False,
) -> str:
    """Backward-compatible helper to extract text after box detection.

    The ``boxes_per_page`` argument is accepted for compatibility with older
    call-sites but is not required, since :class:`PDFTextExtractor` reads box
    information from the detections CSV (creating it on demand if missing).
    """
    # Delegate to PDFTextExtractor, which handles ensuring the CSV exists and
    # writing the output .txt file.
    extractor = PDFTextExtractor(
        pdf_path,
        output_dir=output_dir,
        use_store_results=use_store_results,
    )
    extractor.extract_text()

    pdf_path = Path(pdf_path)
    pdf_name = pdf_path.stem
    pdf_dir = Path(output_dir) / pdf_name
    return str(pdf_dir / f"{pdf_name}.txt")


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        # CLI behaviour and usage string kept identical to original.
        print("Usage: python test_bbox.py input.pdf")
        sys.exit(1)

    pdf_path = sys.argv[1]
    boxes_per_page = get_page_boxes(pdf_path)
    out_path = draw_bboxes_on_pdf(pdf_path, boxes_per_page, output_dir="test")
    out_csv = save_bboxes_csv(pdf_path, boxes_per_page, output_dir="test")
    out_txt = extract_text_with_boxes(pdf_path, boxes_per_page, output_dir="test")
    print(f"Saved processed PDF to: {out_path}")
    print(f"Saved detections CSV to: {out_csv}")
    print(f"Saved extracted text to: {out_txt}")