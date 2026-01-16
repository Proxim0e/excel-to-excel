# PDF Processing Documentation

## Overview

The Excel-to-Excel processor now includes automated PDF processing functionality that downloads, extracts, and populates Excel files with data from procurement specification documents.

## Features

### 1. **Automatic PDF Discovery**
- Crawls lot pages to find PDF documents
- Matches documents against predefined patterns:
  - "anexa 22" / "anexa22"
  - "specificații tehnice" / "spec. tehnic"
  - "fișa tehnică"
  - "caiet de sarcini"
  - "propunere tehnică"

### 2. **Smart Text Extraction**
- **Text-based PDFs**: Direct text extraction using PyPDF2
- **Image-based PDFs**: OCR using Tesseract (Romanian + English)
- **Automatic detection**: Tries text extraction first, falls back to OCR if needed

### 3. **Data Extraction**
Extracts key information from PDFs:
- **Producător** (Producer/Manufacturer)
- **Țara de origine** (Country of origin)
- **Denumirea modelului** (Model name)
- **Specificarea tehnică** (Technical specification)

### 4. **Excel Integration**
- Updates operator columns with extracted data
- Preserves existing formatting and styles
- Works seamlessly with the existing workflow

## Installation

### Requirements

1. **Python Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Tesseract OCR** (for image-based PDFs)
   
   **Ubuntu/Debian:**
   ```bash
   sudo apt-get update
   sudo apt-get install tesseract-ocr tesseract-ocr-ron tesseract-ocr-eng
   ```
   
   **macOS:**
   ```bash
   brew install tesseract tesseract-lang
   ```
   
   **Windows:**
   - Download installer from: https://github.com/UB-Mannheim/tesseract/wiki
   - Install and add to PATH
   - Or set pytesseract.pytesseract.tesseract_cmd in your script

3. **poppler-utils** (for PDF to image conversion)
   
   **Ubuntu/Debian:**
   ```bash
   sudo apt-get install poppler-utils
   ```
   
   **macOS:**
   ```bash
   brew install poppler
   ```
   
   **Windows:**
   - Download from: http://blog.alivate.com.au/poppler-windows/
   - Extract and add to PATH

## Configuration

In `updated_processor_with_scrape.py`:

```python
# PDF Processing Configuration
ENABLE_PDF_PROCESSING: bool = True       # Enable/disable PDF processing
ENABLE_OCR: bool = True                  # Enable/disable OCR for image PDFs
PDF_BASE_DIR: str = './resources/pdfs'   # Directory for storing downloaded PDFs
```

## Usage

### Basic Usage

Simply run the main script as before:

```bash
python updated_processor_with_scrape.py
```

The script will:
1. Process lots and participants (existing functionality)
2. Download matching PDFs for each lot/operator
3. Extract data from PDFs
4. Update Excel with extracted information

### PDF Organization

Downloaded PDFs are organized as:
```
resources/
  pdfs/
    {operator_name}/
      lot_{number}/
        document1.pdf
        document2.pdf
    universal/
      lot_{number}/
        shared_document.pdf
```

### Disabling PDF Processing

To disable PDF processing temporarily:

```python
ENABLE_PDF_PROCESSING: bool = False
```

Or remove/skip installation of PDF dependencies - the script will gracefully degrade.

## Module Overview

### `pdf_downloader.py`
- Finds PDF links on lot pages
- Matches against keyword patterns
- Downloads and organizes files by operator

### `pdf_extractor.py`
- Extracts text from PDFs (text-based or OCR)
- Parses structured data using regex patterns
- Handles Romanian and English content

### `excel_pdf_updater.py`
- Updates Excel worksheets with extracted data
- Formats data for display
- Integrates with openpyxl

### `pdf_processor.py`
- Unified pipeline combining all modules
- Handles both operator-specific and universal PDFs
- Manages the complete workflow

## Customization

### Adding New PDF Patterns

Edit `pdf_downloader.py`:

```python
PDF_KEYWORDS = [
    r'anexa\s*22',
    r'specificat[ií][ií]\s+tehnic[eă]',
    # Add your pattern here
    r'your_custom_pattern',
]
```

### Adding New Extraction Fields

Edit `pdf_extractor.py`:

```python
EXTRACT_FIELDS = {
    'producer': [r'Producător[:\s]*([^\n]+)'],
    'your_field': [r'Your Pattern[:\s]*([^\n]+)'],  # Add here
}
```

Then update `excel_pdf_updater.py` to handle the new field.

### Changing Excel Cell Locations

Edit `excel_pdf_updater.py`:

```python
PDF_DATA_CELLS = {
    'producer': 'B12',  # Change cell location
    'country': 'B13',
    # ...
}
```

## Troubleshooting

### PDFs Not Found
- Check if PDFs exist on the lot page
- Verify PDF filenames match the patterns
- Check console output for PDF discovery messages

### OCR Not Working
- Ensure Tesseract is installed: `tesseract --version`
- Verify Romanian language pack: `tesseract --list-langs`
- Check that poppler-utils is installed

### No Data Extracted
- PDFs may have non-standard formats
- Try adjusting regex patterns in `pdf_extractor.py`
- Check console output for extraction messages

### Import Errors
```bash
# Reinstall dependencies
pip install --upgrade -r requirements.txt
```

## Performance Notes

- PDF processing runs after participant scraping
- Downloads are cached (won't re-download existing files)
- OCR is slower than direct text extraction
- Processing time depends on number of lots and PDF complexity

## Security Considerations

- PDFs are downloaded from public procurement sites
- No execution of PDF content (only text extraction)
- Files stored locally in `resources/pdfs/` (excluded from git)
- All dependencies checked for known vulnerabilities

## Examples

### Sample Output

```
[PDF_PROCESSOR] Processing PDFs for lot 1
[PDF_DOWNLOAD] Found matching PDF: anexa22.signed.pdf at https://...
[PDF_DOWNLOAD] Successfully downloaded to ./resources/pdfs/Company_Name/lot_1/anexa22.signed.pdf
[PDF_EXTRACT] Processing ./resources/pdfs/Company_Name/lot_1/anexa22.signed.pdf
[PDF_EXTRACT] Successfully extracted text directly from document
[PDF_EXTRACT] Found producer: Manufacturer XYZ
[PDF_EXTRACT] Found country: România
[PDF_EXTRACT] Found model: Model ABC-123
[EXCEL_PDF] Updated column E with PDF data for Company_Name
```

## Support

For issues or questions:
1. Check this documentation
2. Review console output for error messages
3. Ensure all dependencies are properly installed
4. Check GitHub issues for similar problems
