"""Optional local cross-encoder scoring; no answer generation."""

from .concurrency import InferenceGate
from .models import offline_snapshot


class LocalReranker:
    def __init__(
        self,
        model: str,
        device: str,
        revision: str | None = None,
        queue_timeout_seconds=30,
        max_pending=16,
    ):
        import torch
        from sentence_transformers import CrossEncoder

        if device == "cuda" and not torch.cuda.is_available():
            device = "cpu"
        self.model = CrossEncoder(
            offline_snapshot(model, revision),
            device=device,
            local_files_only=True,
            trust_remote_code=False,
            model_kwargs={"use_safetensors": True},
        )
        self.gate = InferenceGate(queue_timeout_seconds, max_pending)

    def _score(self, pairs):
        return self.model.predict(pairs, show_progress_bar=False).tolist()

    async def score(self, query: str, contents: list[str]) -> list[float]:
        if not contents:
            return []
        scores = []
        for start in range(0, len(contents), 32):
            scores.extend(
                await self.gate.run(
                    self._score,
                    [(query, text) for text in contents[start : start + 32]],
                )
            )
        return scores

    async def close(self):
        await self.gate.close()
        self.model = None
