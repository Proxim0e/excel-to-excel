from dataclasses import dataclass, field
from typing import List, Optional
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
@dataclass
class Participant:
    """
    Класс, описывающий участника тендера.
    """
    name: str
    price_str: str  # Сырая строка цены с сайта (например, "30 833,35 MDL")
    price_val: Optional[float] = None  # Парсинговое число (например, 30833.35)
    # Список всех документов: (имя_файла, ссылка)
    docs: List[Tuple[str, str]] = field(default_factory=list)

@dataclass
class LotData:
    """
    Класс, описывающий данные, собранные со страницы лота.
    """
    number: int          # Номер лота (извлеченный из URL или титула)
    url: str             # Ссылка на страницу
    title: Optional[str] = None  # Заголовок страницы лота (H1)
    participants: List[Participant] = field(default_factory=list)
    error: Optional[str] = None   # Если при парсинге произошла ошибка