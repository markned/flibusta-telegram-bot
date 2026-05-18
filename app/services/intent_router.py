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
TITLE_CUES={'исповедь','дневник'}
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
 if a.author_part and a.title_part:
  return _d(IntentKind.AUTHOR_TITLE_SEARCH,.96,query,cleaned,cleaned,a.author_part,a.title_part,None,refs,a.format_hint,['explicit_author_title'])
 if _looks_two_part_title_author(cleaned):
  words=cleaned.split(); title,author=(words[0],words[1]) if words[0].lower() in TITLE_CUES else (words[1],words[0])
  return _d(IntentKind.AUTHOR_TITLE_SEARCH,.86,query,cleaned,cleaned,author,title,None,refs,a.format_hint,['title_surname_pair'])
 if a.quoted_title:
  return _d(IntentKind.EXACT_SEARCH,.98,query,cleaned,cleaned,None,None,None,refs,a.format_hint,['quoted_title'])
 if a.likely_author and cleaned.split()[0].lower() in FIRST_NAMES:
  return _d(IntentKind.AUTHOR_SEARCH,.84,query,cleaned,cleaned,None,None,None,refs,a.format_hint,['person_name'])
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
def _looks_two_part_title_author(text:str)->bool:
 words=text.split()
 return len(words)==2 and ((words[0].lower() in TITLE_CUES and words[1].istitle()) or (words[1].lower() in TITLE_CUES and words[0].istitle()))
def _d(kind,conf,orig,cleaned,search,author,title,topic,refs,hint,reasons): return IntentDecision(kind,conf,orig,cleaned,search,author,title,topic,refs,hint,reasons)
