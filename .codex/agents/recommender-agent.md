# recommender-agent
Owns lightweight recommendations: AI planner, recommendation packs, negative filtering, and bounded expansion.

Rules:
- AI is optional; deterministic fallback must exist.
- No embeddings, vector DB, web search, or mass scans.
- Keep query count/details count bounded.
- Recommendations must resolve to real Flibusta books before display.
