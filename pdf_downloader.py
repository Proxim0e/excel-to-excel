#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pdf_downloader.py

Module for downloading PDF documents from procurement lot pages.
Supports matching specific document patterns and organizing files by operator.

Author: Proxim0e
Date: 2026-01-16
"""

import os
import re
from typing import List, Optional, Tuple, Dict
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup


# PDF keyword patterns to match (case-insensitive)
PDF_KEYWORDS = [
    r'anexa\s*22',
    r'anexa22',
    r'specificat[iíî]+\s+tehnic[eăa]',  # Romanian: "specificatii tehnice", "specificații tehnice"
    r'spec[\s._-]?tehnic',              # Abbreviated forms: "spec tehnic", "spec_tehnic"
    r'fisa[\s._-]?tehnica',             # "fisa tehnica", "fisa_tehnica"
    r'fișa\s+tehnic[ăa]',               # Romanian with diacritics
    r'caiet\s+de\s+sarcini',           # "caiet de sarcini" (requirements document)
    r'propunere\s+tehnic[ăa]',         # "propunere tehnica" (technical proposal)
]

# Compile regex patterns for efficiency
PDF_PATTERNS = [re.compile(pattern, re.IGNORECASE) for pattern in PDF_KEYWORDS]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; scraper/1.0; +https://example.com/bot)"
}


def matches_pdf_pattern(filename: str) -> bool:
    """
    Check if filename matches any of the predefined PDF patterns.
    
    Args:
        filename: The filename to check
        
    Returns:
        True if filename matches any pattern, False otherwise
    """
    if not filename:
        return False
    
    # Check against all patterns
    for pattern in PDF_PATTERNS:
        if pattern.search(filename):
            return True
    
    return False


def find_pdf_links(lot_url: str, soup: Optional[BeautifulSoup] = None) -> List[Tuple[str, str]]:
    """
    Find PDF links on a lot page that match our patterns.
    
    Args:
        lot_url: URL of the lot page
        soup: Optional BeautifulSoup object (will fetch if not provided)
        
    Returns:
        List of tuples (pdf_url, pdf_filename)
    """
    if soup is None:
        try:
            resp = requests.get(lot_url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
        except Exception as e:
            print(f"[PDF_DOWNLOAD] Error fetching {lot_url}: {e}")
            return []
    
    pdf_links = []
    
    # Find all links
    for link in soup.find_all("a", href=True):
        href = link["href"].strip()
        
        # Check if it's a PDF link
        if not href.lower().endswith('.pdf'):
            continue
        
        # Get full URL
        full_url = urljoin(lot_url, href)
        
        # Extract filename from URL or link text
        filename = os.path.basename(href)
        if not filename or filename == '.pdf':
            # Try to get filename from link text
            link_text = link.get_text(strip=True)
            if link_text and link_text.lower().endswith('.pdf'):
                filename = link_text
            else:
                filename = f"document_{len(pdf_links)}.pdf"
        
        # Check if filename matches our patterns
        if matches_pdf_pattern(filename) or matches_pdf_pattern(link.get_text(strip=True)):
            pdf_links.append((full_url, filename))
            print(f"[PDF_DOWNLOAD] Found matching PDF: {filename} at {full_url}")
    
    return pdf_links


def download_pdf(pdf_url: str, save_path: str, timeout: int = 30) -> bool:
    """
    Download a PDF file from URL to specified path.
    
    Args:
        pdf_url: URL of the PDF to download
        save_path: Local path where PDF should be saved
        timeout: Request timeout in seconds
        
    Returns:
        True if download successful, False otherwise
    """
    try:
        print(f"[PDF_DOWNLOAD] Downloading {pdf_url} to {save_path}")
        
        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        
        # Download the file
        resp = requests.get(pdf_url, headers=HEADERS, timeout=timeout, stream=True)
        resp.raise_for_status()
        
        # Save to file
        with open(save_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        
        print(f"[PDF_DOWNLOAD] Successfully downloaded to {save_path}")
        return True
        
    except Exception as e:
        print(f"[PDF_DOWNLOAD] Error downloading {pdf_url}: {e}")
        return False


def download_pdfs_for_lot(
    lot_url: str,
    lot_number: int,
    operator_name: Optional[str],
    base_dir: str = './resources/pdfs'
) -> List[Dict[str, str]]:
    """
    Download all matching PDFs for a specific lot and operator.
    
    Args:
        lot_url: URL of the lot page
        lot_number: Lot number
        operator_name: Name of the operator (used for folder organization)
        base_dir: Base directory for storing PDFs
        
    Returns:
        List of dictionaries with download information:
        [{'url': pdf_url, 'filename': filename, 'local_path': path, 'success': True/False}, ...]
    """
    # Find PDF links
    pdf_links = find_pdf_links(lot_url)
    
    if not pdf_links:
        print(f"[PDF_DOWNLOAD] No matching PDFs found for lot {lot_number}")
        return []
    
    # Create operator directory
    operator_folder = operator_name or f"operator_unknown"
    # Sanitize folder name
    operator_folder = re.sub(r'[<>:"/\\|?*]', '_', operator_folder)
    operator_folder = operator_folder.strip()[:100]  # Limit length
    
    lot_dir = os.path.join(base_dir, operator_folder, f"lot_{lot_number}")
    
    # Download each PDF
    results = []
    for pdf_url, filename in pdf_links:
        # Sanitize filename
        safe_filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
        local_path = os.path.join(lot_dir, safe_filename)
        
        # Check if file already exists
        if os.path.exists(local_path):
            print(f"[PDF_DOWNLOAD] File already exists: {local_path}")
            results.append({
                'url': pdf_url,
                'filename': filename,
                'local_path': local_path,
                'success': True,
                'cached': True
            })
            continue
        
        # Download
        success = download_pdf(pdf_url, local_path)
        results.append({
            'url': pdf_url,
            'filename': filename,
            'local_path': local_path,
            'success': success,
            'cached': False
        })
    
    return results


def get_pdf_for_operator(
    lot_url: str,
    lot_number: int,
    operator_name: str,
    base_dir: str = './resources/pdfs'
) -> Optional[str]:
    """
    Download PDF for a specific operator and return the local path.
    
    This is a convenience function that downloads and returns the first matching PDF.
    
    Args:
        lot_url: URL of the lot page
        lot_number: Lot number
        operator_name: Name of the operator
        base_dir: Base directory for storing PDFs
        
    Returns:
        Local path to the downloaded PDF, or None if not found/failed
    """
    results = download_pdfs_for_lot(lot_url, lot_number, operator_name, base_dir)
    
    for result in results:
        if result['success']:
            return result['local_path']
    
    return None
