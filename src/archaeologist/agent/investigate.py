"""Run an investigation from the CLI.

    uv run python -m archaeologist.agent.investigate "what would break if I removed the application context"
"""

import argparse

from archaeologist.agent.graph import investigate


def main() -> None:
    parser = argparse.ArgumentParser(description="Investigate the codebase (Phase 6 agent).")
    parser.add_argument("question")
    parser.add_argument("--max-iterations", type=int, default=2)
    args = parser.parse_args()

    result = investigate(args.question, max_iterations=args.max_iterations)

    print("=== INVESTIGATION TRACE ===")
    for step in result["trace"]:
        print(f"  • {step}")
    print("\n=== EVIDENCE ===")
    for i, e in enumerate(result["evidence"], 1):
        print(f"  [{i}] ({e['stream']:6}) {e['citation']:26.26} {e['title'][:44]}")
    print("\n=== ANSWER ===\n")
    print(result["answer"])


if __name__ == "__main__":
    main()
