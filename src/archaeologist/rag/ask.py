"""Ask the archaeologist a question from the CLI.

    uv run python -m archaeologist.rag.ask "why does Flask use an application context"
    uv run python -m archaeologist.rag.ask "what changed about async views" --streams commit issue
"""

import argparse

from archaeologist.rag.pipeline import answer_question


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask CoderLens a question about the codebase (Phase 5 RAG).")
    parser.add_argument("question")
    parser.add_argument("--k", type=int, default=8)
    parser.add_argument("--streams", nargs="*", default=None,
                        help="Limit to streams: code doc commit issue")
    args = parser.parse_args()

    result = answer_question(args.question, k=args.k, streams=args.streams)

    print(f"Q: {result.question}\n")
    print("EVIDENCE:")
    for i, e in enumerate(result.evidence, 1):
        print(f"  [{i}] ({e['stream']:6}) {e['citation']:24.24} {e['title'][:50]}")
    print("\nANSWER:\n")
    print(result.answer)


if __name__ == "__main__":
    main()
