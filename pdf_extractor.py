#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pdf_extractor.py

Module for extracting text and data from PDF documents.
Supports both text-based PDFs and image-based PDFs (using OCR).

Author: Proxim0e
Date: 2026-01-16
"""

import os
import re
from typing import Dict, Optional, List
from PyPDF2 import PdfReader
import pytesseract
from PIL import Image
from pdf2image import convert_from_path


# Fields to extract from PDFs
EXTRACT_FIELDS = {
    'producer': [
        r'Producător(?:ul)?[:\s]*([^\n]+)',
        r'Producer[:\s]*([^\n]+)',
        r'Fabricant[:\s]*([^\n]+)',
    ],
    'country': [
        r'[ȚŢT]ara\s+de\s+origine[:\s]*([^\n]+)',
        r'Țara\s+de\s+origine[:\s]*([^\n]+)',
        r'Country\s+of\s+origin[:\s]*([^\n]+)',
        r'Origine[:\s]*([^\n]+)',
    ],
    'model': [
        r'Denumirea\s+modelului\s+bunului[/\s]*serviciului[:\s]*([^\n]+)',
        r'Model[:\s]*([^\n]+)',
        r'Denumirea\s+modelului[:\s]*([^\n]+)',
    ],
    'specification': [
        # Multi-line specification pattern:
        # Matches "Specificarea tehnică deplină propusă de către ofertant:"
        # followed by text that continues until an empty line is found
        r'Specificarea\s+tehnic[ăa]\s+deplin[ăa]\s+propus[ăa]\s+de\s+c[ăa]tre\s+ofertant[:\s]*([^\n]+(?:\n(?!\s*$)[^\n]+)*)',
        # Shorter variants
        r'Specificare\s+tehnic[ăa][:\s]*([^\n]+(?:\n(?!\s*$)[^\n]+)*)',
        r'Technical\s+specification[:\s]*([^\n]+(?:\n(?!\s*$)[^\n]+)*)',
    ],
}


def extract_text_from_pdf(pdf_path: str) -> str:
    """
    Extract text from a text-based PDF.
    
    Args:
        pdf_path: Path to the PDF file
        
    Returns:
        Extracted text as a string
    """
    try:
        reader = PdfReader(pdf_path)
        text = ""
        
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
        
        return text.strip()
    
    except Exception as e:
        print(f"[PDF_EXTRACT] Error extracting text from {pdf_path}: {e}")
        return ""


def extract_text_with_ocr(pdf_path: str, lang: str = 'ron+eng') -> str:
    """
    Extract text from an image-based PDF using OCR.
    
    Args:
        pdf_path: Path to the PDF file
        lang: Tesseract language(s) to use (default: Romanian + English)
        
    Returns:
        Extracted text as a string
    """
    try:
        print(f"[PDF_EXTRACT] Converting PDF to images for OCR: {pdf_path}")
        
        # Convert PDF to images
        images = convert_from_path(pdf_path, dpi=300)
        
        text = ""
        for i, image in enumerate(images):
            print(f"[PDF_EXTRACT] Processing page {i+1}/{len(images)} with OCR")
            page_text = pytesseract.image_to_string(image, lang=lang)
            text += page_text + "\n"
        
        return text.strip()
    
    except Exception as e:
        print(f"[PDF_EXTRACT] Error during OCR on {pdf_path}: {e}")
        return ""


def is_text_pdf(pdf_path: str, min_text_length: int = 100) -> bool:
    """
    Check if a PDF contains extractable text (not just images).
    
    Args:
        pdf_path: Path to the PDF file
        min_text_length: Minimum text length to consider it a text PDF
        
    Returns:
        True if PDF has extractable text, False otherwise
    """
    try:
        text = extract_text_from_pdf(pdf_path)
        return len(text.strip()) >= min_text_length
    except Exception:
        return False


def extract_text_smart(pdf_path: str, force_ocr: bool = False) -> str:
    """
    Smart text extraction that tries direct extraction first, then OCR if needed.
    
    Args:
        pdf_path: Path to the PDF file
        force_ocr: If True, skip text extraction and go directly to OCR
        
    Returns:
        Extracted text as a string
    """
    if not os.path.exists(pdf_path):
        print(f"[PDF_EXTRACT] File not found: {pdf_path}")
        return ""
    
    if not force_ocr:
        # Try direct text extraction first
        text = extract_text_from_pdf(pdf_path)
        
        if text and len(text.strip()) >= 100:
            print(f"[PDF_EXTRACT] Successfully extracted text directly from {pdf_path}")
            return text
        
        print(f"[PDF_EXTRACT] Direct extraction yielded little text, trying OCR...")
    
    # Fall back to OCR
    text = extract_text_with_ocr(pdf_path)
    return text


def extract_field_value(text: str, field_patterns: List[str]) -> Optional[str]:
    """
    Extract a field value from text using regex patterns.
    
    Args:
        text: Text to search in
        field_patterns: List of regex patterns to try
        
    Returns:
        Extracted value or None if not found
    """
    for pattern in field_patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            value = match.group(1).strip()
            # Clean up the value
            value = re.sub(r'\s+', ' ', value)
            if value:
                return value
    
    return None


def extract_data_from_text(text: str) -> Dict[str, Optional[str]]:
    """
    Extract structured data from PDF text.
    
    Args:
        text: Extracted text from PDF
        
    Returns:
        Dictionary with extracted fields:
        {
            'producer': str or None,
            'country': str or None,
            'model': str or None,
            'specification': str or None
        }
    """
    data = {}
    
    for field_key, patterns in EXTRACT_FIELDS.items():
        value = extract_field_value(text, patterns)
        data[field_key] = value
        
        if value:
            # Truncate for display
            display_value = value[:100] + '...' if len(value) > 100 else value
            print(f"[PDF_EXTRACT] Found {field_key}: {display_value}")
    
    return data


def extract_pdf_data(pdf_path: str, force_ocr: bool = False) -> Dict[str, Optional[str]]:
    """
    Main function to extract data from a PDF file.
    
    Args:
        pdf_path: Path to the PDF file
        force_ocr: If True, use OCR even if text extraction works
        
    Returns:
        Dictionary with extracted data:
        {
            'producer': str or None,
            'country': str or None,
            'model': str or None,
            'specification': str or None,
            'raw_text': str (full extracted text)
        }
    """
    print(f"[PDF_EXTRACT] Processing {pdf_path}")
    
    # Extract text
    text = extract_text_smart(pdf_path, force_ocr=force_ocr)
    
    if not text:
        print(f"[PDF_EXTRACT] No text extracted from {pdf_path}")
        return {
            'producer': None,
            'country': None,
            'model': None,
            'specification': None,
            'raw_text': ''
        }
    
    # Extract structured data
    data = extract_data_from_text(text)
    data['raw_text'] = text
    
    return data


def extract_pdf_data_batch(pdf_paths: List[str], force_ocr: bool = False) -> Dict[str, Dict[str, Optional[str]]]:
    """
    Extract data from multiple PDF files.
    
    Args:
        pdf_paths: List of PDF file paths
        force_ocr: If True, use OCR for all files
        
    Returns:
        Dictionary mapping file paths to extracted data
    """
    results = {}
    
    for pdf_path in pdf_paths:
        data = extract_pdf_data(pdf_path, force_ocr=force_ocr)
        results[pdf_path] = data
    
    return results
