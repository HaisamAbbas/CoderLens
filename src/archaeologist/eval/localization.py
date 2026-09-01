"""Run the localization eval: for each instance, retrieve and score which files
the system surfaces against the gold files the commit actually changed.
"""

from archaeologist.eval import metrics
from archaeologist.indexing.opensearch_client import get_client
from archaeologist.retrieval.embeddings import get_embedder
from archaeologist.retrieval.multi import search_all

K_VALUES = (1, 3, 5, 10)


def _predicted_files(hits: list[dict]) -> list[str]:
    """Ranked, de-duplicated code files from the hits (by citation 'path:line')."""
    files: list[str] = []
    for h in hits:
        if h["stream"] != "code":
            continue
        path = h["citation"].rsplit(":", 1)[0]
        if path not in files:
            files.append(path)
    return files


def evaluate(instances: list[dict], repo_id: int, candidates: int = 15) -> list[dict]:
    client = get_client()
    embedder = get_embedder()
    rows: list[dict] = []
    for inst in instances:
        hits = search_all(client, embedder, inst["question"], repo_id, k=candidates, streams=["code"])
        pred = _predicted_files(hits)
        gold = inst["gold_files"]
        row = {"id": inst["id"], "question": inst["question"], "pred": pred, "gold": gold,
               "mrr": metrics.mrr(pred, gold)}
        for k in K_VALUES:
            row[f"recall@{k}"] = metrics.recall_at_k(pred, gold, k)
            row[f"hit@{k}"] = metrics.hit_at_k(pred, gold, k)
        rows.append(row)
    return rows


def aggregate(rows: list[dict]) -> dict:
    agg: dict = {"n": len(rows), "MRR": round(metrics.mean([r["mrr"] for r in rows]), 3)}
    for k in K_VALUES:
        agg[f"recall@{k}"] = round(metrics.mean([r[f"recall@{k}"] for r in rows]), 3)
        agg[f"hit@{k}"] = round(metrics.mean([r[f"hit@{k}"] for r in rows]), 3)
    return agg
