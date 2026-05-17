from __future__ import annotations

from dataclasses import dataclass


class ConversionNotAvailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class ConvertedFile:
    content: bytes
    filename: str
    format: str


class ConversionService:
    async def maybe_convert_for_kindle(
        self,
        content: bytes,
        filename: str,
        source_format: str,
        target_format: str,
    ) -> ConvertedFile:
        if source_format == target_format:
            return ConvertedFile(content, filename, source_format)
        raise ConversionNotAvailableError(
            f"Conversion from {source_format} to {target_format} is not available yet."
        )
