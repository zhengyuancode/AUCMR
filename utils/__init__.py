"""Utility package with lazy optional imports.

Importing ``utils.image_merge`` must not require PyMuPDF, scipy, or the retrieval
stack.  Callers that need PDF conversion can still import ``pdfToImage`` from
``utils.pdf_image`` explicitly.
"""

__all__ = [
    "build_state_for_query", "get_query_embedding", "aggregate_query_embedding",
    "pdfToImage", "merge_images", "split_list_into_5_ordered", "build_composite_images",
    "add_page_id_watermarks",
    "retrieval_service", "get_overlap_rate", "deduplication_split",
]


def __getattr__(name):
    if name in {"build_state_for_query", "get_query_embedding", "aggregate_query_embedding"}:
        from . import feature
        return getattr(feature, name)
    if name == "pdfToImage":
        from .pdf_image import pdfToImage
        return pdfToImage
    if name in {"merge_images", "split_list_into_5_ordered", "build_composite_images", "add_page_id_watermarks"}:
        from . import image_merge
        return getattr(image_merge, name)
    if name in {"retrieval_service", "get_overlap_rate", "deduplication_split"}:
        from . import retrieval
        return getattr(retrieval, name)
    raise AttributeError(name)
