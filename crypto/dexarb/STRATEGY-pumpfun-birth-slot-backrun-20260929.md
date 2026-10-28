# Strategy — pump.fun birth-slot backrun

## Frozen rule

- Detect the first TradeEvent of a virgin pump.fun bonding curve from a low-latency transaction feed.
- Submit only while the current leader slot is still the mint's birth slot; cancel instead of entering
  in `birth_slot + 1`.
- Target a **0.05 SOL** curve-moving entry budget.
- Enter immediately after the birth transaction through the Frankfurt Jito Block Engine.
- Track executable liquidation proceeds, not displayed price.
- Trigger exit when proceeds fall 15% below their running maximum.
- Submit the exit with two-slot maximum reaction latency.
- Force exit after 300 seconds.

## Why this rule exists

The archived population earns positive realised ROI only before the slot rolls. Later transactions in
the birth slot earn +5.73%; `+1 slot` entries lose −6.00%. A concrete 0.05 SOL insertion after the
birth transaction earns +10.23% before unobserved Jito tips, while clips at 2 SOL lose from impact.

## Shadow fields

For every virgin mint record: feed receive timestamp, birth slot, transaction signature and order,
current slot at decision, build/sign/send timestamps, chosen Jito endpoint, observed tip floor,
simulated tip, bundle response, landed slot/order when available, entry reserves, exit trigger, and
net result after the exact simulated tip.

## Capital gate

Capital remains disabled until a fresh live sample demonstrates that a transaction created only after
the birth event can land later in that same slot and that simulated net return remains positive after
the actual tip distribution. A slot rollover is a hard no-trade condition, not a slower fallback.
