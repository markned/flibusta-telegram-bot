from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
import re
from app.services.query_analyzer import analyze_query
from app.services.search_logic import clean_query, norm

class IntentKind(str, Enum):
    EXACT_SEARCH='exact_search'; AUTHOR_SEARCH='author_search'; AUTHOR_TITLE_SEARCH='author_title_search'; RECOMMENDATION='recommendation'; DISCOVERY_OPTIONAL='discovery_optional'; UNKNOWN_FALLBACK='unknown_fallback'
@dataclass(frozen=True)
class IntentDecision:
    kind:IntentKind; confidence:float; original_query:str; cleaned_query:str; search_query:str|None; author_part:str|None; title_part:str|None; topic:str|None; reference_authors:list[str]; format_hint:str|None; reasons:list[str]

INSTRUCTION_PATTERNS=(r'\bподбери\w*',r'\bпосовет\w*',r'\bпорекомендуй\w*',r'\bчто\s+почитать',r'\bхочу\s+почитать',r'\bчто-то\s+похож',r'\bпохож\w*\s+на',r'\bв\s+духе',r'\bподборка\s+\w',r'\bкниг[аи]?\s+(о|об|про)')
GENRE_PHRASES=('антиутопия','киберпанк','постмодерн','попаданцы','литрпг','боярка','хоррор','ужасы','мрачное фэнтези','магический реализм')
DISCOVERY_MARKERS=('лучшие','топ','новые','современные','популярные','неочевидные')
DROP_WORDS=('подборка','подбери','посоветуй','порекомендуй','книга','книги','хорошего','хорошая','хорошую','литература','что почитать','что-то','пожалуйста')
TITLE_CUES={'исповедь','дневник','идиот','дюна','мы'}
KNOWN_SURNAMES={'толстой','достоевский','пелевин','сорокин','булгаков','оруэлл','хаксли','замятин'}
FIRST_NAMES={'эдит','лев','федор','фёдор','михаил','джордж','виктор','харуки','пол','томас','петр','пётр'}

def route_intent(query:str)->IntentDecision:
 a=analyze_query(query); cleaned=clean_query(a.cleaned or query); low=norm(cleaned); reasons=[]; topic=None; refs=_reference_authors(cleaned)
 recommendation=any(re.search(p,low,re.I) for p in INSTRUCTION_PATTERNS) or low in GENRE_PHRASES
 # "Подборка стихотворений" is title-like, not instruction-like.
 if low.startswith('подборка ') and len(cleaned.split()) <= 3 and not any(x in low for x in ('как ','русск','хорош','лучшие','постмодерн')):
  recommendation=False; reasons.append('title_like_podborka')
 if recommendation:
  topic=extract_recommendation_topic(cleaned)
  discovery=any(re.search(rf'\b{re.escape(marker)}\b',low) for marker in DISCOVERY_MARKERS) or bool(re.search(r'\bкак\s+[А-ЯЁA-Z]',cleaned)) or ('постмодерн' in low and len(cleaned.split())>1)
  kind=IntentKind.DISCOVERY_OPTIONAL if discovery else IntentKind.RECOMMENDATION
  return _d(kind,.9,query,cleaned,topic or None,None,None,topic,refs,a.format_hint,['recommendation_pattern'])
 if a.likely_author and cleaned.split()[0].lower() in FIRST_NAMES:
  return _d(IntentKind.AUTHOR_SEARCH,.84,query,cleaned,cleaned,None,None,None,refs,a.format_hint,['person_name'])
 detected=detect_author_title_query(cleaned)
 if detected:
  author,title=detected; return _d(IntentKind.AUTHOR_TITLE_SEARCH,.86,query,cleaned,cleaned,author,title,None,refs,a.format_hint,['author_title_heuristic'])
 if a.quoted_title:
  return _d(IntentKind.EXACT_SEARCH,.98,query,cleaned,cleaned,None,None,None,refs,a.format_hint,['quoted_title'])
 if len(cleaned.split()) <= 5:
  return _d(IntentKind.EXACT_SEARCH,.7,query,cleaned,cleaned,None,None,None,refs,a.format_hint,reasons or ['short_title_like'])
 return _d(IntentKind.UNKNOWN_FALLBACK,.4,query,cleaned,cleaned,None,None,None,refs,a.format_hint,['fallback'])

def extract_recommendation_topic(query:str)->str:
 text=clean_query(query)
 for phrase in sorted(DROP_WORDS,key=len,reverse=True):
  text=re.sub(rf'\b{re.escape(phrase)}\b',' ',text,flags=re.I)
 text=re.sub(r'\s+',' ',text).strip(' ,.-')
 return text

def _reference_authors(text:str)->list[str]:
 return [m.group(1).strip() for m in re.finditer(r'\bкак\s+([А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+)?)',text)]
def detect_author_title_query(query:str)->tuple[str,str]|None:
 text=clean_query(query); words=text.split(); low=[w.lower() for w in words]
 if not 2 <= len(words) <= 6: return None
 for sep in (' - ',': '):
  if sep in text:
   left,right=text.split(sep,1); return (left.strip(),right.strip()) if _looks_surname(left.split()[-1].lower()) else ((right.strip(),left.strip()) if _looks_surname(right.split()[-1].lower()) else None)
 if _looks_surname(low[-1]): return words[-1], ' '.join(words[:-1])
 if _looks_surname(low[0]): return words[0], ' '.join(words[1:])
 return None
def _looks_surname(word:str)->bool:
 return word in KNOWN_SURNAMES or any(word.endswith(s) for s in ('ов','ев','ёв','ин','ын','ский','цкий','ой','ая'))
def _d(kind,conf,orig,cleaned,search,author,title,topic,refs,hint,reasons): return IntentDecision(kind,conf,orig,cleaned,search,author,title,topic,refs,hint,reasons)
