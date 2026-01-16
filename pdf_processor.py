#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pdf_processor.py

Integrated PDF processing pipeline that combines downloading, extraction, 
and Excel updates for procurement lot PDFs.

Author: Proxim0e
Date: 2026-01-16
"""

from typing import Dict, List, Optional, Tuple
from openpyxl.worksheet.worksheet import Worksheet

from pdf_downloader import download_pdfs_for_lot, get_pdf_for_operator
from pdf_extractor import extract_pdf_data
from excel_pdf_updater import update_operator_column_with_pdf, update_lot_sheet_with_pdf


def process_lot_pdfs(
    lot_url: str,
    lot_number: int,
    worksheet: Worksheet,
    participants: List[Tuple[str, str]],
    base_pdf_dir: str = './resources/pdfs',
    enable_ocr: bool = True
) -> Dict[str, Dict[str, Optional[str]]]:
    """
    Process PDFs for a single lot and all its participants.
    
    Downloads PDFs, extracts data, and updates the Excel worksheet.
    
    Args:
        lot_url: URL of the lot page
        lot_number: Lot number
        worksheet: The Excel worksheet for this lot
        participants: List of (operator_name, price) tuples
        base_pdf_dir: Base directory for PDF storage
        enable_ocr: Whether to enable OCR for image-based PDFs
        
    Returns:
        Dictionary mapping operator names to extracted PDF data
    """
    print(f"\n[PDF_PROCESSOR] Processing PDFs for lot {lot_number}")
    print(f"[PDF_PROCESSOR] URL: {lot_url}")
    print(f"[PDF_PROCESSOR] Participants: {len(participants)}")
    
    results = {}
    
    # Process PDFs for each participant/operator
    for operator_name, _ in participants:
        if not operator_name:
            continue
        
        print(f"\n[PDF_PROCESSOR] Processing PDF for operator: {operator_name}")
        
        # Download PDFs for this operator
        downloaded = download_pdfs_for_lot(
            lot_url=lot_url,
            lot_number=lot_number,
            operator_name=operator_name,
            base_dir=base_pdf_dir
        )
        
        if not downloaded:
            print(f"[PDF_PROCESSOR] No PDFs downloaded for {operator_name}")
            continue
        
        # Process each downloaded PDF
        operator_data = None
        for pdf_info in downloaded:
            if not pdf_info['success']:
                continue
            
            pdf_path = pdf_info['local_path']
            
            # Extract data from PDF
            pdf_data = extract_pdf_data(pdf_path, force_ocr=False)
            
            # If no data extracted and OCR is enabled, try with OCR
            if enable_ocr and not any(pdf_data.get(k) for k in ['producer', 'country', 'model']):
                print(f"[PDF_PROCESSOR] Retrying with OCR...")
                pdf_data = extract_pdf_data(pdf_path, force_ocr=True)
            
            # Use the first successful extraction
            if any(pdf_data.get(k) for k in ['producer', 'country', 'model', 'specification']):
                operator_data = pdf_data
                break
        
        if operator_data:
            results[operator_name] = operator_data
            
            # Update Excel with extracted data
            update_operator_column_with_pdf(
                worksheet=worksheet,
                operator_name=operator_name,
                pdf_data=operator_data
            )
    
    return results


def process_lot_pdfs_universal(
    lot_url: str,
    lot_number: int,
    worksheet: Worksheet,
    base_pdf_dir: str = './resources/pdfs',
    enable_ocr: bool = True
) -> Optional[Dict[str, Optional[str]]]:
    """
    Process a universal PDF that applies to all operators in a lot.
    
    Some operators upload the same document for all lots.
    This function downloads and extracts from a single PDF and updates
    the worksheet's specification area (not operator-specific columns).
    
    Args:
        lot_url: URL of the lot page
        lot_number: Lot number
        worksheet: The Excel worksheet for this lot
        base_pdf_dir: Base directory for PDF storage
        enable_ocr: Whether to enable OCR for image-based PDFs
        
    Returns:
        Extracted PDF data or None if not found
    """
    print(f"\n[PDF_PROCESSOR] Processing universal PDF for lot {lot_number}")
    
    # Download PDFs (use generic operator name)
    downloaded = download_pdfs_for_lot(
        lot_url=lot_url,
        lot_number=lot_number,
        operator_name="universal",
        base_dir=base_pdf_dir
    )
    
    if not downloaded:
        print(f"[PDF_PROCESSOR] No universal PDFs found for lot {lot_number}")
        return None
    
    # Process the first successful PDF
    for pdf_info in downloaded:
        if not pdf_info['success']:
            continue
        
        pdf_path = pdf_info['local_path']
        
        # Extract data
        pdf_data = extract_pdf_data(pdf_path, force_ocr=False)
        
        # Try OCR if needed
        if enable_ocr and not any(pdf_data.get(k) for k in ['producer', 'country', 'model']):
            print(f"[PDF_PROCESSOR] Retrying with OCR...")
            pdf_data = extract_pdf_data(pdf_path, force_ocr=True)
        
        if any(pdf_data.get(k) for k in ['producer', 'country', 'model', 'specification']):
            # Update worksheet specification area
            update_lot_sheet_with_pdf(
                worksheet=worksheet,
                lot_number=lot_number,
                operator_name="universal",
                pdf_data=pdf_data
            )
            return pdf_data
    
    return None


def process_all_lots_pdfs(
    lot_data: List[Dict],
    worksheets: Dict[int, Worksheet],
    base_pdf_dir: str = './resources/pdfs',
    enable_ocr: bool = True,
    process_universal: bool = True
) -> Dict[int, Dict[str, Dict[str, Optional[str]]]]:
    """
    Process PDFs for all lots in a batch.
    
    Args:
        lot_data: List of lot information dictionaries:
            [{'lot_number': int, 'url': str, 'participants': [(name, price), ...]}, ...]
        worksheets: Dictionary mapping lot numbers to worksheets
        base_pdf_dir: Base directory for PDF storage
        enable_ocr: Whether to enable OCR
        process_universal: Whether to also try processing universal PDFs
        
    Returns:
        Dictionary mapping lot numbers to operator PDF data
    """
    all_results = {}
    
    for lot_info in lot_data:
        lot_number = lot_info.get('lot_number')
        lot_url = lot_info.get('url')
        participants = lot_info.get('participants', [])
        
        if not lot_number or not lot_url:
            continue
        
        worksheet = worksheets.get(lot_number)
        if not worksheet:
            print(f"[PDF_PROCESSOR] No worksheet found for lot {lot_number}")
            continue
        
        # Process operator-specific PDFs
        if participants:
            operator_results = process_lot_pdfs(
                lot_url=lot_url,
                lot_number=lot_number,
                worksheet=worksheet,
                participants=participants,
                base_pdf_dir=base_pdf_dir,
                enable_ocr=enable_ocr
            )
            all_results[lot_number] = operator_results
        
        # Also try universal PDF if enabled
        if process_universal:
            process_lot_pdfs_universal(
                lot_url=lot_url,
                lot_number=lot_number,
                worksheet=worksheet,
                base_pdf_dir=base_pdf_dir,
                enable_ocr=enable_ocr
            )
    
    return all_results
