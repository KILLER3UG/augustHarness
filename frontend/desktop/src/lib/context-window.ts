/** The context window assumed for a model nothing else describes.
 *
 *  Owner directive (2026-11-09): the default is 1M, not 128k — a model August
 *  knows nothing about is not the smallest one on the market. A declared
 *  contextWindow, the provider's /models metadata, and the family heuristics in
 *  estimateContextWindow() all still win; this is only the last resort.
 *
 *  Mirrors backend-py/app/models/config.py::DEFAULT_CONTEXT_WINDOW. The two
 *  cannot share a constant across the language boundary, so they change
 *  together. */
export const DEFAULT_CONTEXT_WINDOW = 1_000_000;
