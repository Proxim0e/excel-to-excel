#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_pdf_modules.py

Basic unit tests for PDF processing modules.
Tests the module structure and basic functionality without requiring actual PDFs.

Author: Proxim0e
Date: 2026-01-16
"""

import sys
import os

def test_imports():
    """Test that all PDF modules can be imported."""
    print("Testing module imports...")
    
    try:
        import pdf_downloader
        print("✓ pdf_downloader imported successfully")
    except ImportError as e:
        print(f"✗ Failed to import pdf_downloader: {e}")
        return False
    
    try:
        import pdf_extractor
        print("✓ pdf_extractor imported successfully")
    except ImportError as e:
        print(f"✗ Failed to import pdf_extractor: {e}")
        return False
    
    try:
        import excel_pdf_updater
        print("✓ excel_pdf_updater imported successfully")
    except ImportError as e:
        print(f"✗ Failed to import excel_pdf_updater: {e}")
        return False
    
    try:
        import pdf_processor
        print("✓ pdf_processor imported successfully")
    except ImportError as e:
        print(f"✗ Failed to import pdf_processor: {e}")
        return False
    
    return True


def test_pdf_pattern_matching():
    """Test PDF filename pattern matching."""
    print("\nTesting PDF pattern matching...")
    
    from pdf_downloader import matches_pdf_pattern
    
    test_cases = [
        ("anexa22.signed.pdf", True),
        ("Anexa 22 - Specifications.pdf", True),
        ("specificatii tehnice.pdf", True),
        ("spec_tehnic_document.pdf", True),
        ("fisa_tehnica.pdf", True),
        ("random_document.pdf", False),
        ("invoice.pdf", False),
        ("", False),
    ]
    
    passed = 0
    failed = 0
    
    for filename, expected in test_cases:
        result = matches_pdf_pattern(filename)
        if result == expected:
            print(f"✓ '{filename}' -> {result} (expected {expected})")
            passed += 1
        else:
            print(f"✗ '{filename}' -> {result} (expected {expected})")
            failed += 1
    
    print(f"\nPattern matching: {passed} passed, {failed} failed")
    return failed == 0


def test_field_extraction():
    """Test field extraction from text."""
    print("\nTesting field extraction...")
    
    from pdf_extractor import extract_field_value
    
    # Test producer extraction
    text = """
    Producător: Company XYZ Ltd
    Țara de origine: România
    Model: ABC-123
    """
    
    patterns = [r'Producător[:\s]*([^\n]+)']
    result = extract_field_value(text, patterns)
    
    if result and "Company XYZ" in result:
        print(f"✓ Extracted producer: '{result}'")
        passed = True
    else:
        print(f"✗ Failed to extract producer, got: '{result}'")
        passed = False
    
    return passed


def test_excel_data_formatting():
    """Test Excel data formatting."""
    print("\nTesting Excel data formatting...")
    
    from excel_pdf_updater import format_pdf_data_for_excel
    
    test_data = {
        'producer': 'Test Company',
        'country': 'România',
        'model': 'Model-123',
        'specification': 'Test specification details'
    }
    
    formatted = format_pdf_data_for_excel(test_data)
    
    checks = [
        ('producer' in formatted, "Producer field present"),
        ('Producător' in formatted.get('producer', ''), "Producer label correct"),
        ('country' in formatted, "Country field present"),
        ('model' in formatted, "Model field present"),
    ]
    
    passed = 0
    failed = 0
    
    for check, description in checks:
        if check:
            print(f"✓ {description}")
            passed += 1
        else:
            print(f"✗ {description}")
            failed += 1
    
    print(f"\nFormatting: {passed} passed, {failed} failed")
    return failed == 0


def test_dependencies():
    """Test that required dependencies are available."""
    print("\nTesting dependencies...")
    
    dependencies = {
        'requests': 'HTTP library',
        'bs4': 'BeautifulSoup (HTML parser)',
        'openpyxl': 'Excel library',
        'PyPDF2': 'PDF text extraction',
        'pytesseract': 'OCR library',
        'PIL': 'Image processing (Pillow)',
        'pdf2image': 'PDF to image conversion',
    }
    
    available = []
    missing = []
    
    for module, description in dependencies.items():
        try:
            __import__(module)
            available.append((module, description))
            print(f"✓ {module} ({description})")
        except ImportError:
            missing.append((module, description))
            print(f"✗ {module} ({description}) - NOT INSTALLED")
    
    print(f"\nDependencies: {len(available)} available, {len(missing)} missing")
    
    if missing:
        print("\nTo install missing dependencies:")
        print("  pip install -r requirements.txt")
    
    return len(missing) == 0


def run_all_tests():
    """Run all tests and report results."""
    print("="*70)
    print("PDF Module Test Suite")
    print("="*70)
    
    tests = [
        ("Module Imports", test_imports),
        ("PDF Pattern Matching", test_pdf_pattern_matching),
        ("Field Extraction", test_field_extraction),
        ("Excel Data Formatting", test_excel_data_formatting),
        ("Dependencies", test_dependencies),
    ]
    
    results = []
    
    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"\n✗ {test_name} crashed: {e}")
            import traceback
            traceback.print_exc()
            results.append((test_name, False))
    
    print("\n" + "="*70)
    print("Test Summary")
    print("="*70)
    
    passed = sum(1 for _, result in results if result)
    failed = len(results) - passed
    
    for test_name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status}: {test_name}")
    
    print(f"\nTotal: {passed}/{len(results)} tests passed")
    
    if failed == 0:
        print("\n🎉 All tests passed!")
        return 0
    else:
        print(f"\n⚠️  {failed} test(s) failed")
        return 1


if __name__ == "__main__":
    sys.exit(run_all_tests())
