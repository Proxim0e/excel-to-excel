#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
excel_pdf_updater.py

Module for updating Excel files with data extracted from PDFs.
Integrates with the existing openpyxl-based workflow.

Author: Proxim0e
Date: 2026-01-16
"""

from typing import Dict, Optional, List
from openpyxl.worksheet.worksheet import Worksheet
from openpyxl.styles import Font, Alignment


# Default cell locations for PDF data in the lot sheets
# These can be customized based on the actual template structure
PDF_DATA_CELLS = {
    'producer': 'B12',      # Below specification
    'country': 'B13',
    'model': 'B14',
    'specification_detail': 'B15',  # Additional specification details
}


def format_pdf_data_for_excel(data: Dict[str, Optional[str]]) -> Dict[str, str]:
    """
    Format extracted PDF data for Excel display.
    
    Args:
        data: Dictionary with extracted PDF data
        
    Returns:
        Dictionary with formatted strings for Excel
    """
    formatted = {}
    
    if data.get('producer'):
        formatted['producer'] = f"Producător: {data['producer']}"
    
    if data.get('country'):
        formatted['country'] = f"Țara de origine: {data['country']}"
    
    if data.get('model'):
        formatted['model'] = f"Model: {data['model']}"
    
    if data.get('specification'):
        # Truncate if too long
        spec = data['specification']
        if len(spec) > 500:
            spec = spec[:500] + "..."
        formatted['specification_detail'] = f"Specificare tehnică propusă: {spec}"
    
    return formatted


def update_worksheet_with_pdf_data(
    worksheet: Worksheet,
    pdf_data: Dict[str, Optional[str]],
    column_letter: str = 'B',
    start_row: int = 12,
    label_font: Optional[Font] = None
) -> None:
    """
    Update a worksheet with extracted PDF data.
    
    Args:
        worksheet: The openpyxl worksheet to update
        pdf_data: Dictionary with extracted PDF data
        column_letter: Column to write data to (default: 'B')
        start_row: Starting row for PDF data (default: 12, below specification)
        label_font: Optional font style for labels
    """
    if not pdf_data or not any(pdf_data.values()):
        print(f"[EXCEL_PDF] No PDF data to write for worksheet '{worksheet.title}'")
        return
    
    formatted_data = format_pdf_data_for_excel(pdf_data)
    
    if label_font is None:
        label_font = Font(size=10, italic=True)
    
    current_row = start_row
    
    # Write each field
    for field_key in ['producer', 'country', 'model', 'specification_detail']:
        if field_key in formatted_data:
            cell_ref = f"{column_letter}{current_row}"
            worksheet[cell_ref] = formatted_data[field_key]
            
            # Apply formatting
            try:
                worksheet[cell_ref].font = label_font
                worksheet[cell_ref].alignment = Alignment(wrap_text=True, vertical='top')
            except Exception:
                pass
            
            print(f"[EXCEL_PDF] Wrote {field_key} to {worksheet.title}!{cell_ref}")
            current_row += 1


def update_lot_sheet_with_pdf(
    worksheet: Worksheet,
    lot_number: int,
    operator_name: str,
    pdf_data: Dict[str, Optional[str]]
) -> None:
    """
    Update a lot sheet with PDF data for a specific operator.
    
    Args:
        worksheet: The lot worksheet to update
        lot_number: Lot number
        operator_name: Name of the operator
        pdf_data: Extracted PDF data
    """
    print(f"[EXCEL_PDF] Updating lot {lot_number} worksheet with PDF data for operator: {operator_name}")
    
    # Update with PDF data in column B (specification area)
    update_worksheet_with_pdf_data(worksheet, pdf_data)


def find_operator_column(worksheet: Worksheet, operator_name: str) -> Optional[str]:
    """
    Find the column letter for a specific operator in the worksheet.
    
    Args:
        worksheet: The worksheet to search
        operator_name: Name of the operator to find
        
    Returns:
        Column letter (e.g., 'E', 'F') or None if not found
    """
    from openpyxl.utils import get_column_letter
    
    # Search row 2 for operator names (format: "Ofertant: {name}")
    for col_idx in range(5, worksheet.max_column + 1):  # Start from column E
        cell = worksheet.cell(row=2, column=col_idx)
        if cell.value and isinstance(cell.value, str):
            # Check if this is the operator
            if operator_name.lower() in cell.value.lower():
                return get_column_letter(col_idx)
    
    return None


def update_operator_column_with_pdf(
    worksheet: Worksheet,
    operator_name: str,
    pdf_data: Dict[str, Optional[str]],
    start_row: int = 11
) -> bool:
    """
    Update an operator's column with PDF data.
    
    This adds PDF information in the operator's column, below the price.
    
    Args:
        worksheet: The worksheet to update
        operator_name: Name of the operator
        pdf_data: Extracted PDF data
        start_row: Row to start writing data (default: 11)
        
    Returns:
        True if updated successfully, False otherwise
    """
    # Find the operator's column
    col_letter = find_operator_column(worksheet, operator_name)
    
    if not col_letter:
        print(f"[EXCEL_PDF] Could not find column for operator: {operator_name}")
        return False
    
    print(f"[EXCEL_PDF] Found operator {operator_name} in column {col_letter}")
    
    # Format data for display
    formatted = format_pdf_data_for_excel(pdf_data)
    
    current_row = start_row
    font = Font(size=9, italic=True)
    
    # Write each field
    for field_key in ['producer', 'country', 'model']:
        if field_key in formatted:
            cell_ref = f"{col_letter}{current_row}"
            worksheet[cell_ref] = formatted[field_key]
            
            try:
                worksheet[cell_ref].font = font
                worksheet[cell_ref].alignment = Alignment(wrap_text=True, vertical='top')
            except Exception:
                pass
            
            current_row += 1
    
    print(f"[EXCEL_PDF] Updated column {col_letter} with PDF data for {operator_name}")
    return True


def batch_update_worksheets(
    worksheets: Dict[int, Worksheet],
    pdf_data_map: Dict[tuple, Dict[str, Optional[str]]]
) -> int:
    """
    Batch update multiple worksheets with PDF data.
    
    Args:
        worksheets: Dictionary mapping lot numbers to worksheets
        pdf_data_map: Dictionary mapping (lot_number, operator_name) to PDF data
        
    Returns:
        Number of successful updates
    """
    success_count = 0
    
    for (lot_number, operator_name), pdf_data in pdf_data_map.items():
        if lot_number not in worksheets:
            print(f"[EXCEL_PDF] No worksheet found for lot {lot_number}")
            continue
        
        worksheet = worksheets[lot_number]
        
        # Try to update the operator's column
        if update_operator_column_with_pdf(worksheet, operator_name, pdf_data):
            success_count += 1
        else:
            # Fallback: update general specification area
            update_lot_sheet_with_pdf(worksheet, lot_number, operator_name, pdf_data)
            success_count += 1
    
    return success_count
