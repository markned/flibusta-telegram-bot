"""Small expiring web references to already observed catalog editions."""

from collections import OrderedDict
from dataclasses import dataclass, replace
from secrets import token_urlsafe
from time import monotonic

from app.flibusta import SearchResult
from app.services.works import editions


@dataclass(frozen=True)
class WorkReference:
    user_id: int
    work: SearchResult
    return_url: str
    created: float


class WorkRegistry:
    def __init__(self, max_entries=128, max_editions=8192, ttl=1800):
        self.entries = OrderedDict()
        self.max_entries, self.max_editions, self.ttl = max_entries, max_editions, ttl

    def _prune(self, now):
        for key, entry in list(self.entries.items()):
            if now - entry.created >= self.ttl:
                self.entries.pop(key)

    def add(self, user_id, work, return_url, *, now=None):
        now = monotonic() if now is None else now
        self._prune(now)
        if len(editions(work)) > self.max_editions:
            raise ValueError("Too many editions")
        while self.entries and (
            len(self.entries) >= self.max_entries
            or sum(len(editions(e.work)) for e in self.entries.values())
            + len(editions(work))
            > self.max_editions
        ):
            self.entries.popitem(last=False)
        token = token_urlsafe(12)
        self.entries[token] = WorkReference(user_id, work, return_url, now)
        return token

    def set_work(self, token, user_id, work, *, now=None):
        ref = self.get(token, user_id, now=now)
        if ref is None:
            return None
        if len(editions(work)) > self.max_editions:
            raise ValueError("Too many editions")
        self.entries.pop(token)
        while (
            self.entries
            and sum(len(editions(e.work)) for e in self.entries.values())
            + len(editions(work))
            > self.max_editions
        ):
            self.entries.popitem(last=False)
        ref = replace(ref, work=work)
        self.entries[token] = ref
        return ref

    def get(self, token, user_id, *, now=None):
        self._prune(monotonic() if now is None else now)
        ref = self.entries.get(token)
        return ref if ref and ref.user_id == user_id else None
