# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License.
"""Document -> text extraction (markitdown).

Used by the upload route at API time to turn an arbitrary uploaded file
into storable plain text.
"""

from __future__ import annotations

import io


def read_document(data: bytes, filename: str) -> str:
    """Extract plain text from uploaded bytes via markitdown.

    Never raises: if markitdown rejects the content (or is unavailable),
    fall back to a utf-8 decode (errors replaced) so an unusual file still
    produces storable text.
    """
    try:
        from markitdown import MarkItDown

        result = MarkItDown().convert(io.BytesIO(data))
        text = getattr(result, "text_content", None)
        if text:
            return text
    except Exception:  # noqa: BLE001
        pass
    return data.decode("utf-8", errors="replace")
