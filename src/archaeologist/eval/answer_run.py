"""LLM-judge answer-quality eval CLI.

    uv run python -m archaeologist.eval.answer_run
"""

from archaeologist import telemetry
from archaeologist.eval import answer_eval


def main() -> None:
    telemetry.reset()
    rows = answer_eval.evaluate()
    agg = answer_eval.aggregate(rows)

    print("=== Per question ===")
    for r in rows:
        j = r["judge"]
        cv = r["citations"]
        print(f"\nQ: {r['question']}")
        print(f"   verdict={j.get('verdict')}  ground={j.get('groundedness')}  "
              f"cite_support={j.get('citation_support')}  relevance={j.get('relevance')}")
        print(f"   citations: {cv['distinct']} distinct, valid_rate={cv['valid_rate']} "
              f"(over {r['n_evidence']} evidence)")

    print("\n=== Aggregate (LLM-judge, 1-5) ===")
    for k, v in agg.items():
        print(f"  {k:20}: {v}")
    print(f"\n  telemetry: {telemetry.summary()}")


if __name__ == "__main__":
    main()
