# PDF libraries

Recipes for `pypdf`, `pdfplumber`, `reportlab`, and OCR. Run them from the
workspace; every writer opens a new output file.

## Setup

```bash
pip install pypdf pdfplumber reportlab
pip install pytesseract pdf2image   # OCR only
```

`pytesseract` needs the Tesseract binary, and `pdf2image` needs poppler
(`pdftoppm`). Check both with `tesseract --version` and `pdftoppm -v`.

## pypdf — structure

### Read and count pages

```python
from pypdf import PdfReader

reader = PdfReader("document.pdf")
print(f"Pages: {len(reader.pages)}")
if reader.is_encrypted:
    reader.decrypt("password")
```

### Extract text

```python
from pypdf import PdfReader

reader = PdfReader("document.pdf")
text = "\n".join(page.extract_text() or "" for page in reader.pages)
```

If every page returns `''`, the PDF is a scan — go to OCR.

### Merge

```python
from pypdf import PdfReader, PdfWriter

writer = PdfWriter()
for pdf_file in ["doc1.pdf", "doc2.pdf", "doc3.pdf"]:
    for page in PdfReader(pdf_file).pages:
        writer.add_page(page)

with open("merged.pdf", "wb") as out:
    writer.write(out)
```

### Split

```python
from pypdf import PdfReader, PdfWriter

reader = PdfReader("input.pdf")
for i, page in enumerate(reader.pages, start=1):
    writer = PdfWriter()
    writer.add_page(page)
    with open(f"page_{i}.pdf", "wb") as out:
        writer.write(out)
```

### Metadata

```python
from pypdf import PdfReader

meta = PdfReader("document.pdf").metadata
if meta:
    print(f"Title: {meta.title}")
    print(f"Author: {meta.author}")
    print(f"Subject: {meta.subject}")
    print(f"Creator: {meta.creator}")
```

### Rotate

```python
from pypdf import PdfReader, PdfWriter

reader = PdfReader("input.pdf")
writer = PdfWriter()
page = reader.pages[0]
page.rotate(90)  # clockwise
writer.add_page(page)

with open("rotated.pdf", "wb") as out:
    writer.write(out)
```

### Watermark

```python
from pypdf import PdfReader, PdfWriter

watermark = PdfReader("watermark.pdf").pages[0]
reader = PdfReader("document.pdf")
writer = PdfWriter()

for page in reader.pages:
    page.merge_page(watermark)
    writer.add_page(page)

with open("watermarked.pdf", "wb") as out:
    writer.write(out)
```

### Add or remove a password

```python
from pypdf import PdfReader, PdfWriter

reader = PdfReader("input.pdf")
if reader.is_encrypted:
    reader.decrypt("current-password")

writer = PdfWriter()
for page in reader.pages:
    writer.add_page(page)
writer.encrypt("user-password", "owner-password")

with open("encrypted.pdf", "wb") as out:
    writer.write(out)
```

## pypdf — forms

Inspect the fields before filling; the field names and checkbox on-states are
part of the form, not something to guess.

```python
from pypdf import PdfReader, PdfWriter

reader = PdfReader("form.pdf")
fields = reader.get_fields() or {}
print(fields)  # names, types, /Yes or /1 checkbox states

writer = PdfWriter()
writer.append(reader)
writer.update_page_form_field_values(
    writer.pages[0],
    {"full_name": "Ada Lovelace", "subscribe": "/Yes"},
)
# Some viewers only render values when this flag is set.
writer.set_need_appearances_writer(True)

with open("form-filled.pdf", "wb") as out:
    writer.write(out)
```

If the consumer still shows empty fields, flatten the form after filling
(pypdf's `flatten` on the writer / page) and verify in a viewer, not only with
`get_fields()`.

## pdfplumber — text and tables

### Text with layout

```python
import pdfplumber

with pdfplumber.open("document.pdf") as pdf:
    for page in pdf.pages:
        print(page.extract_text(layout=True) or "")
```

### Tables

```python
import pdfplumber

with pdfplumber.open("document.pdf") as pdf:
    for i, page in enumerate(pdf.pages, start=1):
        for j, table in enumerate(page.extract_tables(), start=1):
            print(f"Table {j} on page {i}")
            for row in table:
                print(row)
```

### Tables to Excel

```python
import pandas as pd
import pdfplumber

frames = []
with pdfplumber.open("document.pdf") as pdf:
    for page in pdf.pages:
        for table in page.extract_tables():
            if table and len(table) > 1:
                frames.append(pd.DataFrame(table[1:], columns=table[0]))

if frames:
    pd.concat(frames, ignore_index=True).to_excel("tables.xlsx", index=False)
```

Header rows often repeat on every page; deduplicate after the concat if the
output should not carry them.

## reportlab — create PDFs

### Canvas

```python
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

c = canvas.Canvas("hello.pdf", pagesize=letter)
width, height = letter

c.drawString(100, height - 100, "Hello World!")
c.drawString(100, height - 120, "Created with reportlab")
c.line(100, height - 140, 400, height - 140)
c.save()
```

### Multiple pages with Platypus

```python
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

styles = getSampleStyleSheet()
story = [
    Paragraph("Report Title", styles["Title"]),
    Spacer(1, 12),
    Paragraph("Body text. " * 20, styles["Normal"]),
    PageBreak(),
    Paragraph("Page 2", styles["Heading1"]),
    Paragraph("Content for page 2", styles["Normal"]),
]
SimpleDocTemplate("report.pdf", pagesize=letter).build(story)
```

### Subscripts and superscripts

Never put Unicode subscript/superscript characters (`₀₁₂₃₄₅₆₇₈₉`, `⁰¹²³⁴⁵⁶⁷⁸⁹`)
in a ReportLab PDF: the built-in fonts lack those glyphs and render solid black
boxes.

```python
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph

styles = getSampleStyleSheet()
chemical = Paragraph("H<sub>2</sub>O", styles["Normal"])
squared = Paragraph("x<super>2</super> + y<super>2</super>", styles["Normal"])
```

For canvas-drawn text, shift the font size and baseline manually instead.

## OCR

### Text out

```python
import pytesseract
from pdf2image import convert_from_path

images = convert_from_path("scanned.pdf", dpi=300)
text = ""
for i, image in enumerate(images, start=1):
    text += f"Page {i}:\n{pytesseract.image_to_string(image)}\n\n"
print(text)
```

Pass `lang="deu"` (or another trained language) when the page is not English;
the default English model returns plausible-looking garbage on other scripts.

### Searchable PDF

`ocrmypdf` is the reliable path because it rebuilds the document rather than
concatenating per-page PDFs:

```bash
ocrmypdf --skip-text input.pdf searchable.pdf
```

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `extract_text()` returns `''` | Scanned page — rasterize with `pdf2image` and OCR |
| `PdfReadError` on open | Truncated download or missing password — check size, then decrypt |
| Black boxes instead of ₂ / ² | Unicode sub/superscripts in ReportLab — use `<sub>`/`<super>` |
| Table rows merged or split | Heuristic detection — tune `table_settings` or use `layout=True` text |
| Filled form still blank in a viewer | Set `/NeedAppearances`, or flatten the form |
| OCR text is nonsense | Wrong `lang=` or too low DPI — retry at 300 DPI |
