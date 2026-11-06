# PDF command-line tools

Shell recipes for bulk work and quick inspection. Prefer these when the task is
one operation on a large file; use the Python recipes when the result needs
reshaping.

## Install

| Tool | Package | Windows |
|---|---|---|
| `pdftotext`, `pdfinfo`, `pdfimages` | poppler-utils | `choco install poppler`, then add its `bin` to `PATH` |
| `qpdf` | qpdf | `choco install qpdf` |
| `pdftk` | pdftk-java | `choco install pdftk` (optional) |
| `tesseract` | tesseract-ocr | `choco install tesseract` |
| `ocrmypdf` | ocrmypdf | `pip install ocrmypdf` |

Check what is present before promising a command:

```bash
pdfinfo -v; qpdf --version; pdftk --version; tesseract --version
```

## Inspect

```bash
# Page count, page size, encryption state, metadata
pdfinfo input.pdf

# Is there a text layer? Empty output = scan
pdftotext -f 1 -l 1 input.pdf -

# Validate a file's structure
qpdf --check input.pdf
```

## Extract text

```bash
pdftotext input.pdf output.txt

# Preserve the visual layout (columns survive better)
pdftotext -layout input.pdf output.txt

# Pages 1-5 only
pdftotext -f 1 -l 5 input.pdf output.txt
```

## Merge

```bash
# qpdf
qpdf --empty --pages file1.pdf file2.pdf -- merged.pdf

# pdftk
pdftk file1.pdf file2.pdf cat output merged.pdf
```

## Split

```bash
# qpdf: pages 1-5 into one file, 6-10 into another
qpdf input.pdf --pages . 1-5 -- pages1-5.pdf
qpdf input.pdf --pages . 6-10 -- pages6-10.pdf

# pdftk: one file per page
pdftk input.pdf burst
```

## Rotate

```bash
# qpdf: page 1 by 90 degrees clockwise; repeat with 1-z for all pages
qpdf input.pdf output.pdf --rotate=+90:1

# pdftk
pdftk input.pdf rotate 1east output rotated.pdf
```

## Encrypt / decrypt

```bash
# Remove a password (qpdf needs the current one)
qpdf --password=mypassword --decrypt encrypted.pdf decrypted.pdf

# Add a password with 256-bit AES
qpdf --encrypt userpass ownerpass 256 -- input.pdf encrypted.pdf
```

## Images

```bash
# List embedded images with page numbers and dimensions
pdfimages -list input.pdf

# Extract as JPEG when possible; output prefix-000.jpg, prefix-001.jpg, ...
mkdir -p images
pdfimages -j input.pdf images/prefix
```

`-j` only helps when the embedded image is DCT/JPEG; other images come out as
`.ppm`/`.png`. Use `-all` to get one file per image instead of per frame pair.

## OCR a scan

```bash
# Text sidecar
ocrmypdf --skip-text input.pdf searchable.pdf
pdftotext searchable.pdf -

# If ocrmypdf is unavailable: render then OCR with tesseract
pdftoppm -r 300 -png scanned.pdf page
for f in page-*.png; do tesseract "$f" "${f%.png}"; done
```

## Verify

```bash
pdfinfo output.pdf | grep -E 'Pages|Encrypted|Page size'
pdftotext output.pdf - | head -20
```

Report the command and the real counts; a zero exit status from a broken pipe or
a missing tool is not proof the operation happened.
