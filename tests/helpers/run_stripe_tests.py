import subprocess
import sys
from pathlib import Path

here = Path(__file__).parent
cases = ["valid", "invalid", "stale"]

failed = 0
for case in cases:
    print(f"\n{'=' * 60}\nRunning: {case}\n{'=' * 60}")
    result = subprocess.run(
        [sys.executable, str(here / "send_stripe_webhook.py"), case],
        capture_output=True,
        text=True,
    )
    print(result.stdout)
    if result.returncode != 0:
        failed += 1
        print(result.stderr, file=sys.stderr)

if failed:
    print(f"\n{failed} test(s) failed.", file=sys.stderr)
    sys.exit(1)

print("\nAll Stripe signature tests passed.")
