"""Chat with Trolley in the terminal: `trolley` (offline) or `LLM_PROVIDER=anthropic trolley`."""

from __future__ import annotations

import argparse

from .agent.agent import Session, TrolleyAgent


def main() -> None:
    ap = argparse.ArgumentParser(description="Chat with the Trolley shopping agent")
    ap.add_argument("--trace", action="store_true", help="print tool calls and guardrail flags")
    args = ap.parse_args()
    session, agent = Session(), TrolleyAgent()
    print(f"Trolley ({agent.provider.model}). {len(session.store.cat.products)} products. Ctrl-D to quit.")
    while True:
        try:
            msg = input("\nyou> ").strip()
        except EOFError:
            break
        if not msg:
            continue
        turn = agent.run(session, msg)
        if args.trace:
            for e in turn.tool_events:
                print(f"  · {e.name}({e.arguments}) {'ok' if e.ok else 'ERROR ' + str(e.error)}")
            if turn.flags:
                print(f"  · flags: {turn.flags}")
        print(f"trolley> {turn.reply}")


if __name__ == "__main__":
    main()
