from __future__ import annotations
from app.services.intent_router import IntentDecision

def build_recommendation_clarification(query:str,intent:IntentDecision)->str:
 q=query.lower(); topic=(intent.topic or '').strip()
 if 'бдсм' in q or 'мастер/слейв' in q or 'мастер слейв' in q:
  return 'Правильно понял: составить подборку книг и пособий по БДСМ-практикам и динамике мастер/слейв?'
 if 'русск' in q and 'постмодерн' in q and any('пелевин' in a.lower() for a in intent.reference_authors):
  return 'Правильно понял: составить подборку книг от сильных представителей русского постмодерна, близких по духу к Виктору Пелевину?'
 if 'антиутоп' in q and ('xxi' in q or '21' in q):
  return 'Правильно понял: составить подборку антиутопий XXI века?'
 if 'немецк' in q and 'постмодерн' in q and 'первого лица' in q:
  return 'Правильно понял: составить подборку книг немецкого постмодерна с повествованием от первого лица?'
 if topic:
  return f'Правильно понял: составить книжную подборку по теме «{topic}»?'
 return 'Правильно понял: составить книжную подборку по этой теме?'
