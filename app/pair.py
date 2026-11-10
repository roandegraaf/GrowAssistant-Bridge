"""Pair this bridge from the command line: ``python -m app.pair <CODE>``.

The installer runs this with the code the operator reads off the app's
"pair a bridge" screen, so a fresh install comes up already paired without
ever opening the bridge's web UI. It talks to the app at ``api.url`` and
writes the same ``data/credentials.json`` the web UI's pairing form does.

Exit codes: 0 paired, 1 the app rejected the code, 2 the app could not be
reached, 3 any other failure. Messages go to stdout for the installer to show.
"""

import asyncio
import logging
import sys

from app.auth import PairResult, auth_manager
from app.config import config

EXIT_CODES = {
    PairResult.PAIRED: 0,
    PairResult.INVALID_CODE: 1,
    PairResult.UNREACHABLE: 2,
    PairResult.FAILED: 3,
}


def normalize_code(raw: str) -> str:
    """Uppercase and drop spaces/dashes, so ``k7p-q9`` and ``K7PQ9`` both work."""
    return "".join(ch for ch in raw.upper() if ch.isalnum())


async def pair(code: str) -> PairResult:
    await auth_manager.start()
    try:
        return await auth_manager.pair(code)
    finally:
        await auth_manager.stop()


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("Usage: python -m app.pair <PAIRING_CODE>")
        return 3
    code = normalize_code(argv[0])
    if not code:
        print("That is not a pairing code.")
        return 1

    # The installer prints its own messages; keep the bridge's log lines out.
    logging.basicConfig(level=logging.CRITICAL)
    server = config.get("api.url", "")
    result = asyncio.run(pair(code))
    if result is PairResult.PAIRED:
        print(f"Paired with {server}")
    elif result is PairResult.INVALID_CODE:
        print("The app did not accept that code. Check it, or show a new one in the app.")
    elif result is PairResult.UNREACHABLE:
        print(f"Could not reach {server}. Check the Pi's internet connection.")
    else:
        print(f"Pairing failed: {server} returned an error.")
    return EXIT_CODES[result]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
