def resolve_runtime_config(
    settings,
    *,
    top_k: int | None = None,
    confidence_threshold: float | None = None,
    min_recall_candidates: int | None = None,
    recall_multiplier: int | None = None,
    query_variant_limit: int | None = None,
    context_neighbor_window: int | None = None,
    context_max_chunks: int | None = None,
) -> dict:
    resolved_top_k = max(1, int(top_k if top_k is not None else settings.RAG_TOP_K))
    resolved_confidence_threshold = max(
        0.0,
        min(1.0, float(confidence_threshold if confidence_threshold is not None else settings.RAG_CONFIDENCE_THRESHOLD)),
    )
    resolved_min_recall = max(
        resolved_top_k,
        int(min_recall_candidates if min_recall_candidates is not None else settings.RAG_MIN_RECALL_CANDIDATES),
    )
    resolved_recall_multiplier = max(
        1,
        int(recall_multiplier if recall_multiplier is not None else settings.RAG_RECALL_MULTIPLIER),
    )
    resolved_query_variant_limit = max(
        1,
        int(query_variant_limit if query_variant_limit is not None else settings.RAG_QUERY_VARIANT_LIMIT),
    )
    resolved_context_neighbor_window = max(
        0,
        int(context_neighbor_window if context_neighbor_window is not None else settings.RAG_CONTEXT_NEIGHBOR_WINDOW),
    )
    resolved_context_max_chunks = max(
        resolved_top_k,
        int(context_max_chunks if context_max_chunks is not None else settings.RAG_CONTEXT_MAX_CHUNKS),
    )
    return {
        "top_k": resolved_top_k,
        "confidence_threshold": resolved_confidence_threshold,
        "min_recall_candidates": resolved_min_recall,
        "recall_multiplier": resolved_recall_multiplier,
        "query_variant_limit": resolved_query_variant_limit,
        "context_neighbor_window": resolved_context_neighbor_window,
        "context_max_chunks": resolved_context_max_chunks,
    }
