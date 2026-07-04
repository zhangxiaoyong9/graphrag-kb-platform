# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License.
"""Document -> text extraction (markitdown).

Used by the upload route at API time to turn an arbitrary uploaded file
into storable plain text.
"""

from __future__ import annotations

import io
import logging

logger = logging.getLogger(__name__)


def read_document(data: bytes, filename: str) -> str:
    """Extract plain text from uploaded bytes via markitdown.

    Never raises: if markitdown rejects the content (or is unavailable),
    fall back to a utf-8 decode (errors replaced) so an unusual file still
    produces storable text.
    """
    text = ""
    try:
        from markitdown import MarkItDown

        result = MarkItDown().convert(io.BytesIO(data))
        text = getattr(result, "text_content", None) or ""
    except Exception:  # noqa: BLE001
        pass
    if not text:
        text = data.decode("utf-8", errors="replace")
    logger.info("parsed %d chars from %s", len(text), filename)
    return text
