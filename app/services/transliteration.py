"""One bounded Russian transliteration candidate, always validated by catalog evidence."""
import re

_PAIRS = {'shch': 'щ', 'sch': 'щ', 'yo': 'е', 'jo': 'е', 'zh': 'ж', 'kh': 'х',
          'ts': 'ц', 'ch': 'ч', 'sh': 'ш', 'yu': 'ю', 'ju': 'ю', 'ya': 'я', 'ja': 'я',
          'ye': 'е', 'iy': 'ий'}
_SINGLE = str.maketrans({
    'a':'а','b':'б','c':'к','d':'д','e':'е','f':'ф','g':'г','h':'х','i':'и',
    'j':'й','k':'к','l':'л','m':'м','n':'н','o':'о','p':'п','q':'к','r':'р',
    's':'с','t':'т','u':'у','v':'в','w':'в','x':'кс','y':'ы','z':'з',
})


def latin_to_cyrillic(text):
    if not re.fullmatch(r'[a-zA-Z0-9\s-]+', text) or not re.search('[a-z]', text, re.I) or re.search('[а-яё]', text, re.I):
        return None
    result = text.casefold()
    for left, right in _PAIRS.items():
        result = result.replace(left, right)
    return result.translate(_SINGLE)
