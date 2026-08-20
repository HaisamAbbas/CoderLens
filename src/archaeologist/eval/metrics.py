"""Ranking metrics for file localization."""


def recall_at_k(predicted: list[str], gold: list[str], k: int) -> float:
    if not gold:
        return 0.0
    hit = len(set(predicted[:k]) & set(gold))
    return hit / len(set(gold))


def hit_at_k(predicted: list[str], gold: list[str], k: int) -> float:
    """1.0 if at least one gold file appears in the top k, else 0.0."""
    return 1.0 if set(predicted[:k]) & set(gold) else 0.0


def mrr(predicted: list[str], gold: list[str]) -> float:
    """Reciprocal rank of the first correct file."""
    gold_set = set(gold)
    for i, p in enumerate(predicted, 1):
        if p in gold_set:
            return 1.0 / i
    return 0.0


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
