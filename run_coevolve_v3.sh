#!/bin/bash
# Wide-pool co-evolution: all ten starter sets as the card pool, all ten
# starter decks seeding the gauntlet, mono-colour candidate seeding.
cd /Users/astogsdill/cookie_run_simulator || exit 1
python3 -u coevolve.py \
  --rounds 80 --hours 8.5 --pilot both \
  --sets all-starters --seed-decks all-starters \
  --gauntlet-size 12 --colors 1 \
  --checkpoint rl_agent_v3.pt --out coevolved_v3.json 2>&1 | tee coevolve_v3.log
echo
echo "=== run finished; window stays open ==="
read -r -p "press return to close "
