import pytest

from src.utils.file_helpers import sanitize_filename


@pytest.mark.parametrize("name, expected", [
    ("", "unknown"),
    ("a:b/c*?.pdf", "abc.pdf"),
    ("  ", "unnamed"),
    ("Oferta.pdf", "Oferta.pdf"),
])
def test_basic_cleanup(name, expected):
    assert sanitize_filename(name) == expected


def test_long_name_keeps_full_extension_with_dot():
    result = sanitize_filename("Declaratie privind valabilitatea ofertei semnata.docx")
    assert result.endswith(".docx")
    assert not result.endswith("..docx") or result.endswith("...docx")


def test_long_pdf_name_is_backward_compatible():
    name = "Specificatie tehnica semnata electronic Lot 12.pdf"
    old_behavior = name[:40] + ".." + name[-4:]
    assert sanitize_filename(name) == old_behavior  # уже скачанные файлы не перекачаются


def test_long_folder_name_is_backward_compatible():
    title = "Lotul nr. 10 Cateter WIT Mandrin pentru hemodializa"
    assert sanitize_filename(title) == title[:40] + ".." + title[-4:]
