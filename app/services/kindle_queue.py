from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Callable

from aiogram import Bot

from app.services.kindle import KindleService
from app.services.smtp_errors import classify_smtp_error, is_transient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KindleQueueJob:
    delivery_id: int
    user_id: int
    chat_id: int
    book_id: str
    status_message_id: int


class KindleQueue:
    def __init__(
        self,
        *,
        service: KindleService,
        worker_concurrency: int = 2,
        user_concurrency: int = 1,
        error_message_for_exception: Callable[[Exception], str] | None = None,
        max_attempts: int = 3,
        retry_base_delay_seconds: int = 10,
    ):
        self.service = service
        self.worker_concurrency = max(1, worker_concurrency)
        self.user_concurrency = max(1, user_concurrency)
        self._queue: asyncio.Queue[KindleQueueJob] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._user_semaphores: dict[int, asyncio.Semaphore] = {}
        self._bot: Bot | None = None
        self.active_jobs = 0
        self.error_message_for_exception = error_message_for_exception or (
            lambda exc: "Failed to send this book to Kindle. Try again later."
        )
        self.max_attempts=max(1,max_attempts); self.retry_base_delay_seconds=retry_base_delay_seconds

    @property
    def size(self) -> int:
        return self._queue.qsize()

    async def start(self, bot: Bot) -> None:
        self._bot = bot
        if not self._workers:
            self._workers = [asyncio.create_task(self._worker()) for _ in range(self.worker_concurrency)]

    async def stop(self) -> None:
        for worker in self._workers:
            worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    async def enqueue(self, *, user_id: int, chat_id: int, book_id: str, status_message_id: int, retry_of_delivery_id: int | None = None) -> int:
        delivery_id = await self.service.create_queued_delivery(user_id, book_id, retry_of_delivery_id=retry_of_delivery_id)
        await self._queue.put(
            KindleQueueJob(
                delivery_id=delivery_id,
                user_id=user_id,
                chat_id=chat_id,
                book_id=book_id,
                status_message_id=status_message_id,
            )
        )
        return delivery_id

    async def _worker(self) -> None:
        while True:
            job = await self._queue.get()
            semaphore = self._user_semaphores.setdefault(job.user_id, asyncio.Semaphore(self.user_concurrency))
            async with semaphore:
                self.active_jobs += 1
                try:
                    for attempt in range(1,self.max_attempts+1):
                        await self.service.deliveries_repo.increment_attempt(job.delivery_id)
                        try:
                            await self.service.process_delivery(delivery_id=job.delivery_id,user_id=job.user_id,book_id=job.book_id,on_progress=lambda text: self._edit_status(job,text))
                            await self._edit_status(job,"Sent to Kindle. It usually appears in a few minutes."); break
                        except Exception as exc:
                            cat=classify_smtp_error(exc)
                            if attempt < self.max_attempts and is_transient(cat):
                                await self._edit_status(job,self.error_message_for_exception(exc))
                                await asyncio.sleep(self.retry_base_delay_seconds*(3**(attempt-1))); continue
                            logger.error("Kindle queue job failed user_id=%s book_id=%s error_type=%s",job.user_id,job.book_id,type(exc).__name__)
                            await self._edit_status(job,self.error_message_for_exception(exc)); break
                finally:
                    self.active_jobs -= 1
                    self._queue.task_done()

    async def _edit_status(self, job: KindleQueueJob, text: str) -> None:
        if self._bot is None:
            return
        try:
            await self._bot.edit_message_text(text,chat_id=job.chat_id,message_id=job.status_message_id)
        except Exception as exc:
            logger.warning("Kindle status edit failed error_type=%s",type(exc).__name__)
