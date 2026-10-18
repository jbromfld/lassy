"""Optional local cross-encoder scoring; no answer generation."""
import asyncio
import threading


class LocalReranker:
    def __init__(self, model: str, device: str):
        from sentence_transformers import CrossEncoder
        import torch
        if device == 'cuda' and not torch.cuda.is_available():
            device = 'cpu'
        self.model = CrossEncoder(model, device=device)
        self._lock = threading.Lock()

    def _score(self, pairs):
        with self._lock:
            return self.model.predict(pairs, show_progress_bar=False).tolist()

    async def score(self, query: str, contents: list[str]) -> list[float]:
        if not contents:
            return []
        return await asyncio.to_thread(self._score, [(query, text) for text in contents])

    async def close(self):
        self.model = None
