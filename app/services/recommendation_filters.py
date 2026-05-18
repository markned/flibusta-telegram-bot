from __future__ import annotations
from app.services.search_logic import norm
BAD_TITLE_STEMS=('инструкция','пособие','учебник','руководство','как написать','методичка','справочник')
EXPLICIT_MANUAL_STEMS=('инструкц','учебник','пособ','руководств','manual','guide')
def is_bad_recommendation_candidate(title:str,query:str,negative_keywords:list[str]|None=None)->bool:
 text=norm(title); q=norm(query); negative_keywords=negative_keywords or []
 if any(norm(k) in text for k in negative_keywords): return True
 if any(word in q for word in EXPLICIT_MANUAL_STEMS): return False
 return any(norm(word) in text for word in BAD_TITLE_STEMS)

WEAK_ANCHORS={'подборка','подбери','посоветуй','порекомендуй','книга','книги','литература','хорошего','хорошая книга','что почитать','похожее','типа','как','автор','авторы','роман'}
def is_weak_recommendation_anchor(anchor:str,original_query:str)->bool:
 text=norm(anchor)
 return text in {norm(item) for item in WEAK_ANCHORS}
