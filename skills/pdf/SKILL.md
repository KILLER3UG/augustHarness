---
name: pdf
description: Read, create, and modify PDF files — extract text and tables, merge or split, rotate and watermark pages, fill forms, encrypt/decrypt, extract images, and OCR scanned pages. Use when the user mentions a .pdf file or asks to produce one; do not use it for Office documents, plain images, or turning a web page into a PDF.
category: artifacts
version: 1.0.0
platforms: [linux, macos, windows]
license: MIT — adapted from Anthropic's Claude PDF skill; see NOTICE.md
metadata:
  hermes:
    tags: [pdf, text-extraction, tables, ocr, forms, documents]
    related_skills: []
---

# PDF Processing

A PDF is one of two files: text-native, where a text layer exists, or a scan,
where every page is a picture. Decide which one you have before choosing a
tool — `extract_text()` on a scan returns an empty string, not an error.

## When to Use

- The user names a `.pdf`, asks to read one, or asks for a PDF as output.
- Extracting text, tables, metadata, or embedded images from a PDF.
- Combining, splitting, rotating, watermarking, or encrypting/decrypting PDFs.
- Filling an existing PDF form, or OCR-ing a scan to make it searchable.

## When NOT to Use

- Office documents (`.docx`, `.xlsx`, `.pptx`) — convert to PDF first.
- Plain images with no PDF container — use the image tools directly.
- Web page to PDF — use the browser's print-to-PDF path, not reportlab.

## Prerequisites

- Python: `pypdf` (structure), `pdfplumber` (layout and tables), `reportlab`
  (creation), `pytesseract` + `pdf2image` (OCR).
- CLI, optional but faster for bulk work: poppler-utils (`pdftotext`, `pdfinfo`,
  `pdfimages`), `qpdf`, `pdftk`.
- OCR needs the Tesseract binary; `pdf2image` also needs poppler.
- Check the install before a long job:
  `python -c "import pypdf, pdfplumber, reportlab; print('ok')"`.

## Choose the tool first

| Task | Best tool |
|---|---|
| Extract text or tables | pdfplumber |
| Merge, split, rotate, watermark, encrypt | pypdf, or qpdf from the shell |
| Create a new PDF | reportlab |
| OCR a scanned PDF | pdf2image → pytesseract |
| Extract embedded images | `pdfimages -j` |
| Page count, metadata, encryption state | `pdfinfo` or pypdf |

## Procedure

1. **Classify the file.** `pdfinfo file.pdf` for page count and size, then
   `pdftotext -f 1 -l 1 file.pdf -` to test for a text layer. Empty output on
   page 1 means a scan.
2. **Pick the smallest operation that answers the request** from
   `references/libraries.md` (Python) or `references/cli.md` (shell). Open only
   the file the current task needs.
3. **Write to a new path.** Never overwrite the user's input; every recipe
   opens a fresh output file.
4. **Verify** with the checklist below, then report the command, output path
   and real counts.

## Pitfalls

- **Scanned PDF, empty text.** `extract_text()` returning `''` is not a parse
  failure — rasterize and OCR instead.
- **ReportLab Unicode sub/superscripts** (`H₂O`, `x²`) render as black boxes in
  the built-in fonts. Use `<sub>`/`<super>` inside `Paragraph`, or shift the
  font size and baseline for canvas text.
- **Encrypted input.** `PdfReader` raises or reports no pages until
  `reader.decrypt(password)` succeeds. Ask for the password; do not write an
  empty output and call it done.
- **Form fields that look unchanged.** Some viewers ignore `/NeedAppearances`.
  Set it with `set_need_appearances_writer(True)` and flatten if the consumer
  cannot render widgets.
- **Table extraction is heuristic.** `extract_tables()` can merge cells or split
  one table into several; check the row count before exporting.
- **`pdftk rotate` argument style** (`1east`, `1-5even`) differs from qpdf's
  `--rotate=+90:1`. Prefer qpdf unless pdftk is already installed.

## Verification

- Output page count equals the expectation (`pdfinfo` or `PdfReader`).
- Text extraction is non-empty for a text-native PDF and contains the first and
  last expected strings.
- A merge contains every source page once; a split produces one file per range.
- OCR output contains a line you can read on the page — spot-check one.
- Encrypted output reopens only with the password you set.
- Report what actually ran; an exit code alone is not evidence.

## Reference files

- `references/libraries.md` — pypdf, pdfplumber, reportlab, OCR and form-filling
  recipes. Read when the task needs Python code.
- `references/cli.md` — pdftotext, qpdf, pdftk, pdfimages and install notes.
  Read when a shell one-liner is enough or the file is large.
- `NOTICE.md` — attribution for the upstream skill this port follows.

---

Adapted from Anthropic's Claude PDF skill. See `NOTICE.md`.
