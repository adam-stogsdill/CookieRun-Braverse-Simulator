# Cookie Run: Braverse Simulator

[Cookie Run: Braverse Website](https://cookierunbraverse.com/en)

A rules engine for CookieRun: Braverse plus a practice bot to play against. This is an **unofficial** project and much of the code was generated using Claude (I feel like this should be explicityly disclosed). However, this was initially to create an simulator for deck building and agent training.

If there are any issues (as I'm sure there will be), please create an issue or message me on discord: \_\_set\_\_. 

I am a Python programmer specializing in ML, so game development is not my forte and thus please excuse some more amateur attempts at design in this project. This is just for fun and with my downtime. I am also a very new CookieRun: Braverse player so I know the rules in this sim are not perfectly implemented.

## Roadmap

There are many things that are still needing to be updated and reworked, but I intend to get more feedback and critical features moving forward. Below are some of the things I'm considering in no particular order:

- Adding multiplayer. Of course TCG games are nothing without community and so this feature is one of my top priorities.

- Introducing customizability for sleeves and mats. Right now the placeholder images are a bit ugly and I'd like to make the game feel more fun.

- Adding extra deck features. This is an important game feature and requires refactoring to allow for this to be included.

- There is code to ensure we allow for as many cards as possible, however the newer sets have cards that are still missing and so this needs to be fixed.

- I would like to add a deck builder to this implementation. I think text base deck building can be easy to implement but is not as fun and requires more work.

- Better animations and sounds. This bit is lacking and I know this needs to be improved to make the gameplay more enjoyable.

### Contents

- [Cookie Run: Braverse Simulator](#cookie-run-braverse-simulator)
  - [Roadmap](#roadmap)
    - [Contents](#contents)
  - [Repository layout](#repository-layout)
  - [Install](#install)
    - [Card art](#card-art)
  - [Quick start](#quick-start)
  - [What works](#what-works)
  - [The visual player](#the-visual-player)
  - [The effect compiler](#the-effect-compiler)
    - [Removal that skips the break area](#removal-that-skips-the-break-area)
    - [A FLIP can save its own host from the break area](#a-flip-can-save-its-own-host-from-the-break-area)
    - [Who goes first](#who-goes-first)
    - [Costs in angle brackets are a decision](#costs-in-angle-brackets-are-a-decision)
  - [Rules fidelity](#rules-fidelity)
  - [Self-play RL](#self-play-rl)
  - [Deck generation](#deck-generation)
    - [Not overfitting the shuffles](#not-overfitting-the-shuffles)
    - [Using the RL agent as the pilot](#using-the-rl-agent-as-the-pilot)
    - [Co-evolution](#co-evolution)
  - [Extending it](#extending-it)
  - [Source](#source)

## Repository layout

```
fetch_cards.py        downloads the card database from DotGG -> braverse_cards.csv
braverse/             the engine (library, UI-agnostic)
  cards.py            CSV -> typed CardDef, with the dump's typos normalised
  cost.py             {B}{B}{N} cost parsing and support-area payment
  state.py            zones, Cookies, GameState (plain data, deep-copyable)
  engine.py           phases, legal actions, combat, win conditions
  effects.py          the trigger registry and the vocabulary effects are written in
  effect_ir.py        effect IR + interpreter (ops, conditions, targets)
  compiler.py         rules text -> effect IR, for the bulk of the pool
  impl/               hand-written card effects, one module per set
  agents.py           RandomAgent, HeuristicAgent
  features.py         (state, action) encoding for learning agents
  rl.py               self-play RL: policy net, league, REINFORCE trainer
  deckgen.py          evolutionary deck generation
  decks.py            starter decklists + deck validation
  rps.py              the opening rock-paper-scissors for turn order
  config.py           every tunable rule, cited to the PLAY GUIDE
play_server.py        the visual player: play a bot, or watch two bots play
viewer/               its browser front end (no build step, no dependencies, no assets)
selfplay.py           bulk self-play harness and win-rate report
train_rl.py           train / evaluate the RL agent
evolve_deck.py        evolve a decklist against a gauntlet
coverage_report.py    which cards the engine can play, and what to build next
coevolve.py           alternate deck evolution and agent training
compare_decks.py      round-robin decks under a chosen pilot
tests/                157 tests
requirements-play.txt just play the game (numpy only)
requirements.txt      the above plus RL, tests and tooling
```

## Install

**Python 3.9 or newer.** Check what you have:

```bash
python3 --version
```

If that errors, or prints something older than 3.9, install Python:

- **macOS** — `brew install python@3.12`, or the installer from
  [python.org/downloads](https://www.python.org/downloads/). The `python3` that
  ships with macOS works too if it is 3.9+.
- **Windows** — `winget install Python.Python.3.12`, or the installer from
  [python.org/downloads](https://www.python.org/downloads/); tick **Add
  python.exe to PATH**. Use `python` instead of `python3` in every command
  below.
- **Linux** — `sudo apt install python3 python3-venv python3-pip` (Debian /
  Ubuntu) or `sudo dnf install python3 python3-pip` (Fedora).

Then clone the repo and set up a virtual environment:

```bash
git clone https://github.com/<you>/cookie_run_simulator.git
cd cookie_run_simulator
python3 -m venv .venv
source .venv/bin/activate
```

On Windows the last line is `.venv\Scripts\activate` instead.

Then pick one of the two dependency sets.

**Just to play the game** — this is the one you want if you came here to sit
down and play a match. One small package, no PyTorch, a few seconds to install:

```bash
pip install -r requirements-play.txt
python play_server.py
```

That covers the whole game: the engine, the browser player against the
`heuristic` and `random` bots, `selfplay.py`, `evolve_deck.py`,
`compare_decks.py` and `coverage_report.py`. The only dependency is `numpy`,
which `braverse.features` imports. The browser front end has no build step and
no dependencies of its own — no npm, no bundler.

The one thing you give up is the RL pilots: the `rl_agent*.pt` checkpoints will
not be selectable as opponents in the visual player, and `train_rl.py` /
`coevolve.py` will not run. Everything else behaves identically.

**Everything, including the RL** — training, co-evolution, the test suite and
the Tabletop Simulator exporter. This pulls in PyTorch, so it is a few hundred
MB:

```bash
pip install -r requirements.txt
```

| Package  | In which file            | Needed by                                          |
| -------- | ------------------------ | -------------------------------------------------- |
| `numpy`  | `requirements-play.txt`  | required — `braverse.features`, imported by the package itself |
| `torch`  | `requirements.txt`       | `braverse.rl`, `train_rl.py`, `coevolve.py`, RL pilots in the visual player, `tests/test_learning.py` |
| `tqdm`   | `requirements.txt`       | progress bars in `train_rl.py` / `coevolve.py`     |
| `pytest` | `requirements.txt`       | running `tests/`                                    |
| `pillow` | `requirements.txt`       | `build_tts_sheets.py` (Tabletop Simulator sheets)  |

`requirements.txt` includes `requirements-play.txt`, so it is the superset —
never install both.

### Card art

The card database `braverse_cards.csv` is checked in, but the ~2000 card images
are not — they are 190 MB and belong to Devsisters, not this repo. Download
them once:

```bash
python3 fetch_images.py            # everything -> card_images/
python3 fetch_images.py --sets ST8 ST9   # just the two starter decks (fast)
```

It skips files it already has, so it is safe to interrupt and re-run. Without
them the visual player still works — cards just render as name plates instead
of art.

To refresh the card database itself: `python3 fetch_cards.py`.

## Quick start

If you installed the full `requirements.txt`, run the tests to confirm it
(157 tests, about 30 seconds — they need `pytest`, and `test_learning.py` needs
`torch`):

```bash
python -m pytest -q tests/
```

Play 200 games of bot-vs-bot and print the win rates:

```bash
python selfplay.py -n 200
```

Drive the engine yourself:

```python
from braverse import Game, HeuristicAgent, SeatedAgent, STARTER_DECKS

agents = [SeatedAgent(HeuristicAgent(), 0), SeatedAgent(HeuristicAgent(), 1)]
game = Game([STARTER_DECKS["st9_sea_fairy"], STARTER_DECKS["st8_wind_archer"]],
            agents, seed=1)
game.setup()
while not game.state.over:
    options = game.legal_actions()
    seat = game.to_move()
    game.step(game.controller(seat).choose_action(game.state, options))
print(game.state.win_reason)
```

Or play a game yourself in the browser:

```bash
python play_server.py
```

Opens a board at `http://localhost:8080` — see [The visual player](#the-visual-player).

`legal_actions()` returns fully-specified moves (targets included) and
`game.clone()` gives an independent deep copy, so a search agent can be dropped
in without touching the engine.

## What works

- Full turn structure: active → draw → support → main → end.
- Support area as energy, coloured and generic (`{N}`) costs, resting to pay.
- Cookies played free from hand with a face-down HP pile drawn off the deck.
- Damage flips HP cards into the trash; FLIP cards fire on reveal.
- Fainting, the break area, [refresh], and both loss conditions.
- 【On Play】, 【Activate】, 【Once Per Turn】, 【Blocker】, attack riders, FLIP
  effects, faint triggers, Items, Traps (with a real defender response window),
  and Stages.
- Both ST8 and ST9 starter decks are fully implemented and legal.

- **1465 of 1540 cards are fully playable**: 244 hand-written, 865 compiled
  from their printed text, 356 genuinely vanilla. `coverage_report.py` shows
  the split.
- **19 of 22 sets are 100% implemented** — every ST starter set plus BS1–BS8
  and BS10. The 75 cards still outstanding sit in BS9, BS11 and the promo set,
  and rest on four mechanics the engine does not model: 【Special Play】,
  【Awaken】, the Extra Deck, and replacement effects that rewrite an
  opponent's abilities.

Cards whose text the compiler does not fully understand stay vanilla and are
kept out of the generated deck pools. `coverage_report.py` shows exactly where
that line falls.

## The visual player

```bash
python play_server.py            # --port 8080 --no-browser
```

Stop it with ctrl-c. It also shuts down on SIGHUP and SIGTERM, so closing the
terminal takes the server with it rather than leaving something holding port
8080; if the port is busy anyway it names the PID still on it.

A playmat with the printed card art: the two battle areas with their face-down
HP piles, the support area, the stage, deck and trash, the break-area clock, and
both hands. Pick a pilot and a deck for each seat — `human`, `heuristic`,
`random`, or any `rl_agent*.pt` checkpoint in the directory — plus any decklist
`evolve_deck.py` has written. Two bots means spectating, with pause,
single-step, and a speed slider; a `human` seat means you play that side.

**Click a card and it tells you what it can do.** The menu lists that card's
legal moves by the name the card prints for them — "Bike Blast", "Tracker's
Arrow" — because 980 of the Cookies name their attack. The 220 that use the
older `<{P}{P}> Deals 2 damage.` printing have no name to show, and no
【Activate】 skill in the whole pool is named, so those fall back to the marker
itself: "Attack" and "Activate". The move list on the right tags the same
names.

**Questions about your hand are answered with your hand.** Discarding, opening
with a Cookie, fielding a replacement when one faints — all the same gesture:
your hand comes up as a strip, you toggle up to the number asked for, and the
confirm button says what you are about to do (*Discard*, *Play Cookie*).
Choosing from a list on the far side of the screen while your hand sits at the
bottom was the wrong way round. With one card to pick, clicking a second moves
the choice rather than refusing it.

Which questions get the strip is decided structurally — every option is a card
that is in your hand — rather than by matching prompt text, so a new prompt of
the same shape gets it for free; only the verb reads from the prompt.

That needed one engine change. `Game.discard` asked its controller once *per
card*, which is the natural shape for a greedy bot and the wrong shape for a
person. `effects.ask_many` now asks a controller that implements `choose_many`
for the whole selection in one question and loops `choose` for everyone else —
so every scripted agent behaves exactly as before, bit for bit, and only the
human seat sees the difference. It also normalises a short, padded or repeated
answer, because "discard 2" has to remove exactly two cards however the client
phrases it.

**Drag and drop.** Pick up one of your cards and the legal drops light up:
a Cookie card onto your battle area to play it, any card onto your support area
to place it, one of your Cookies onto an enemy Cookie to attack it. Dropping
somewhere illegal does nothing, and a drag that goes nowhere falls back to
being a click.

Dragging is only ever a second way to name an option the server already sent:
a drop resolves to (subject, target) and then looks for the one legal action
that matches, so the drag layer cannot invent a move the engine did not offer.
When an effect asks a question mid-resolution — "Damage which Cookie?" — the
cards it is choosing between are outlined and clicking one answers it.

Hover any card for its full text; click one to filter the move list down to the
moves that use it; click the trash or break area to search through it, where the hover preview
now paints over the browser rather than behind it — a modal `<dialog>` lives in
the browser's top layer, which no z-index can beat, so the preview moves inside
it while it is open; keys
`1`–`9` take the numbered option and `space` / `→` pause and step. `reveal`
shows both hands, which is what you want when watching two bots.

`flip opp.` (on by default, remembered per browser) turns the opponent's half
through a full 180°, the way their mat would sit across the table from you:
rows swap so their support area is above their battle area, columns swap, each
column's contents reverse — so their stage, deck and trash mirror yours across
the centre line — and their cards face them, so you see the art upside down. A
rested card of theirs sits at 270° rather than 90°, for the same reason. Zone
labels, counters and the reveal animation stay upright, because those are the
interface rather than the board; hover any card to read it the right way up.
Turn the option off to see both halves in the same orientation.

**Actions play out.** One action can be a whole little scene, and the browser
plays it as a sequence: the attacker steps out of its slot, turns side-on
alongside the Cookie it is hitting and settles back; the defender takes the hit
and shakes; damage turns HP cards face up; a Cookie that breaks snaps in two
along a jagged seam and the halves fall apart. Both halves carry the whole card
art, each clipped to one side of the *same* randomly generated polygon, so the
crack matches tooth for tooth and no two breaks look alike. `sound` (on by default) adds card flicks, the swing and its impact,
and a crunch when a Cookie breaks — all synthesised with WebAudio at play time,
so there are no audio files to ship and nothing to download. Browsers will not
start audio without a user gesture, so the first click or key press unlocks it.

The attack comes from the *action*, not a state diff — an attack is a thing a
player did, and by the time it resolves the target may be off the board
entirely — while faints come from a diff of the battle area, which catches
"placed in the trash" and effect bounces as well as ordinary fainting, and marks
which of them actually banked Level in the break area.

**The board is deliberately a beat behind the server.** Events describe what an
action *did*, so they have to be animated against the board as it was before
it: the attacker must lunge at a Cookie that is still standing, and an HP card
must turn face up over the pile it came off. Rendering the new state first left
the attacker swinging at an empty slot every time it killed something. So the
browser plays the scene, *then* commits the state, holding the newest snapshot
until it finishes and refusing clicks in the meantime — an option index from the
old list would answer the question the server has since moved on to. The bot
seats wait out the same scene, so nothing happens off screen: the pause is
computed from the events themselves (`scene_seconds`) rather than being a fixed
beat, and is capped so a pathological batch cannot stall the match. It counts
each animation through to its *end* rather than its start — a bot must not move
again while a revealed card is still being read — so an attack that turns three
HP cards face up and breaks a Cookie holds the board for about six seconds.

**Damage is animated.** An HP card turning face up is the swing moment of a
battle — a FLIP fires the instant it is revealed, and can bounce its own host
mid-attack — so each revealed card turns face up over the Cookie it came off
rather than a number quietly ticking down. It is held enlarged for about a
second and a half, which is the difference between seeing that something
happened and reading *what*, and the bot seats wait an extra beat after a reveal
so the board does not move on underneath it. The panel also keeps the last
reveal as a strip of thumbnails, so one you blinked through is still there —
worth having, because the log only records FLIPs, not ordinary HP cards.

That needs no engine callback: an HP card moves straight from the face-down pile
to the trash, so diffing those two zones between snapshots *is* the reveal, and
a card an effect bounces out of the pile correctly does not count. Two things
that bit, both now pinned by tests or comments: several `publish` calls can
share one game state, and diffing them against each other erased the reveal
before the browser polled it; and the obvious way to write the flip —
`backface-visibility` on a `rotateY` — is not honoured everywhere, so the card
stayed back-side up through the whole animation. It is a 2D squash-and-swap
instead.

**Why it is a server and not a script.** The engine calls its controllers
*re-entrantly*: the defender's trap window opens inside `game.step`, and so does
every mid-effect decision ("discard a card", "damage which Cookie?"). A human
seat therefore cannot be a function that returns a move and unwinds. The match
runs on its own thread and a human seat blocks there until the browser answers
the question the engine is currently asking, so the UI drives the *same*
`Controller` protocol the bots implement — no special human path in the engine,
and traps and effect targeting work for a human for free.

Bot seats pass through the same gate, which is what pause and step are: the gate
simply does not open. Only turn-level decisions are paced, so an attack and
everything it triggers reads as one beat rather than a stutter.

Hidden information is filtered **server-side**, on the way out: your opponent's
hand never reaches the page, so there is nothing in the browser to leak. Every
HP pile is stripped for *everyone*, including its owner and including under
`reveal` — the pile is face down in the real game and the not-knowing is the
point. Public zones (trash, break area) are sent in full, which is what makes
them searchable. The `reveal` toggle needs no replay, because the filter runs
over a snapshot rather than being baked into it.

## The effect compiler

Hand-writing 1200 cards was never going to happen, and the text is templated
enough not to need it. `braverse/compiler.py` parses rules text into the ops in
`braverse/effect_ir.py`, which the interpreter runs against the same `Ctx` a
hand-written card would have called.

It is compositional rather than template-matched — a clause is an optional run
of `<...>` costs, an optional `If ...,` guard, and a verb phrase from a small
vocabulary — because the same atoms recombine endlessly across the pool.
`That Cookie receives N damage.` alone appears on 177 cards.

Two rules keep it honest:

- **All or nothing.** A card is registered only if *every* clause in its text
  compiles. A card that half-resolves is worse than a vanilla one, because it
  silently misreports what the game does. 45 cards currently compile partially
  and are deliberately held back.
- **Hand-written always wins.** The compiler skips any card already in the
  registry, so it can only ever add coverage. ST8/ST9 self-play results are
  bit-identical before and after compilation, which is the regression check.

Finding the grammar also surfaced two real bugs in the card parser: the older
`<{P}> Deals 3 damage.` printing left a bare `" damage."` as rider text on 223
cards, and the clause splitter was breaking `LV.2` at the period.

Coverage reached **93.7%** of effect-bearing cards by alternating two passes:
structural compiler work, then hand-writing whatever a set had left. Working
set by set was what surfaced the engine's real gaps — each set needed fewer new
mechanics than the last (BS1 needed six, BS10 two), and the compiler carried a
growing share of each one.

The earlier growth came from targeting *structural* shapes rather than
individual phrases, once the per-phrase tail went flat (top blocker: 5 cards):

| fix | cards unlocked |
|---|---|
| strip the `When this Cookie faints,` trigger prefix | 59 |
| parse `During this turn, if X, ...` as a guard | 43 |
| one generic `MoveCards` op for trash/break/deck movement | 64 |

The last one replaced a family of clauses — "return X from your trash to your
hand", "select X from your break area and place it in the trash", "place X on
the bottom of the deck" — with a single op plus a card-level filter. Two of
these needed new engine state: per-turn faint and break-area counters, reset for
*both* players each turn, since "during this turn" clauses routinely ask about
the opponent's losses.

### Removal that skips the break area

Implementing the ST sets surfaced a rules distinction the engine did not have:
**"place that Cookie into the trash" is not fainting.** A trashed Cookie never
reaches the break area, so its owner's opponent banks no Level for it. That is
why those effects are priced the way they are, and it is now a separate
`trash_cookie` path with tests contrasting it against a normal faint.

Three other mechanics came with it: a `TRASHED` trigger (distinct from `FAINT`),
`skip_next_active` for "not set as active during your opponent's next Active
Phase", and an `ATTACK_START` hook so static 【Your Turn】 buffs read the board
as the attack is declared rather than being stored.

### A FLIP can save its own host from the break area

"Return this Cookie to your hand" (Muscle Cookie ST8-002, Blue Whale Cookie
ST9-003) sits in an HP pile and fires the instant damage reveals it — including
the damage that empties the pile. The Cookie goes back to its owner's hand
rather than to the break area, so the attacker banks no Level and the rest of
the damage has nothing left to hit. That is the printed card working as
written, and it is what the deck search found and maxed out.

The one part the guide does not settle is what happens when the revealed card
is the *last* HP card: the Cookie is at 0 HP, so does the flip rescue it or does
it faint first? `flip_bounce_beats_faint` in `config.py` picks a reading. It
defaults to the flip winning, because a FLIP fires the moment it is revealed and
because every measured result here was produced under that reading; set it False
and a Cookie already at 0 HP faints instead. Over 60 ST9-vs-ST8 games the switch
turns 46 bounces into faints and does not move the win rate either way.

### Who goes first

`braverse/rps.py` plays the guide's opener: both controllers throw, ties are
re-thrown, and the winner picks who starts. Each round is logged *as it
happens* rather than in one block at the end — a tie sends you straight back for
another throw, and being re-asked with no explanation is baffling. The browser
puts the whole toss in the middle of the table, big enough to hit, instead of in
the panel on the far right. `Game(first_player=...)` takes the
answer — the engine no longer assumes seat 0 opens, so the skipped first draw,
the no-attacks-on-turn-one rule and the round counter all follow whoever
actually won.

It is deliberately *not* inside `Game.setup`. Bulk self-play and RL training run
millions of games where the ritual would only burn RNG and wall time, and every
harness here wants seat 0 to start so its numbers stay comparable; the visual
player calls it, the training loops do not. `HeuristicAgent` throws at random —
a fixed throw is free to read — and always takes the first turn.

### Costs in angle brackets are a decision

`<...>` on a card is a cost you *may* pay: pay it and the effect happens,
decline and nothing does. The engine used to pay it automatically the moment it
could afford to, which is wrong in the case that matters most — a FLIP turning
over mid-attack would quietly rest its controller's support or bin a card from
their hand, on the opponent's turn, without asking.

`Ctx.wants_to_pay` now gates every bracketed cost, on both card paths: the
compiled clause (386 cards carry a `<...>`) and the hand-written bodies, whose
`ctx.discard(n, optional=True)` *is* the printed `<Discard N cards.>`. The rule
is who asked for the effect. Triggers the controller opted into by choosing an
action — 【Activate】, an Item, a Trap — pay without a prompt, because taking
the action was the decision. Everything that merely happens to them asks first.
A cost that cannot be met is never offered, and a clause whose cost *is* an op
(a discard) asks once rather than once per op.

`HeuristicAgent` answers yes to every cost, so bot play and the self-play
numbers above are unchanged; it is a human seat that gains the choice.

## Rules fidelity

The engine follows the official English PLAY GUIDE. The rules that matter most,
and that are easy to get wrong:

- **Cookies are free and there is no level-up.** "When a Cookie card plays, you
  do not [rest] the cost in the support area." Any Cookie goes from hand into a
  free battle slot (max two). Level is not a cost or a gate — it is purely the
  number your opponent banks in your break area when that Cookie faints.
- **Running out of deck does not lose the game.** It triggers a **[refresh]**:
  you put one LV.1-or-higher Cookie from your trash into your *own* break area,
  then shuffle the trash back into the deck. Decking yourself advances your
  opponent's clock instead of ending the game.
- **You lose only when both** your battle area is empty **and** you have no
  Cookie card in hand to place there. When a Cookie faints you may immediately
  bring one from hand, [On Play] included.
- **No summoning sickness.** A revealed Cookie enters [active] and may attack
  the turn it arrives. You cannot attack (or draw) on the very first turn of the
  game.
- **Damage** reveals HP cards from the top of the pile — "in order of the most
  recently used" — into the trash; a revealed FLIP fires immediately, one by
  one. At 0 HP the Cookie card goes face up to the break area; 10 total Level
  there loses.
- **Deck**: 60 cards, up to 4 per *card number* (so alt arts share the cap), up
  to 16 FLIP, at least one Cookie.
- **Turn order** is decided by rock-paper-scissors and the winner chooses. It
  is worth choosing: in mirror matches under the heuristic, whoever goes first
  wins **68%** of the time (ST9 68.7%, ST8 67.3% over 150 games each), despite
  the opener skipping their first draw and being unable to attack.
- **Setup**: draw 6, optional full mulligan, and a forced reveal-and-redraw
  until you hold a Cookie (your opponent draws 1 each time you do). Each player
  places one Cookie face down, reveals it and builds its HP pile from the deck.
  [On Play] does not fire during setup.

One deliberate simplification, marked NOT IN GUIDE in `braverse/config.py`: every
【Activate】 skill is capped at once per turn per source. Printed
【Once Per Turn】 skills already are, and without the cap the legal-action list
does not terminate for repeatable skills whose cost the engine cannot yet prove
was paid.

With these rules, 100% of self-play games end on the break-area condition at a
mean of ~9 rounds — the shape you would expect, and a useful regression signal
if a rules change is ever wrong.

## Self-play RL

```bash
python train_rl.py --games 40000 --random-decks 0.5
```

```bash
python train_rl.py --eval-only rl_agent.pt
```

The action set is variable-length and heterogeneous — "attack with A into B",
"place this card as support", "end turn" — so there is no fixed action index to
softmax over. Instead every legal action is encoded as its own `(state, action)`
row (`braverse/features.py`, 55 features: 22 state, 9 action-type, 24
action-detail), scored independently by an MLP, and the softmax runs *across
rows*. That handles any number of actions of any kind without padding tricks at
inference time.

Training is REINFORCE with a learned value baseline and an entropy bonus.
Opponents are drawn from a league — frozen snapshots of past selves plus the
scripted heuristic (40% of games by default) — so the policy cannot drift into
a strategy that only beats its current self. Seats alternate every game, and
evaluation alternates them too, so turn-order advantage cannot flatter a score.

Mid-effect decisions (which card to discard, which Cookie an effect targets) are
delegated to `HeuristicAgent`. Only the turn-level action choice is learned.
That is a deliberate scope cut: it is where nearly all the decision weight sits,
and it keeps credit assignment clean.

Crucially the encoding is over card **stats and abilities**, never card
identity — no per-card one-hot anywhere. That is what lets a policy read a card
it has never seen before, and it is why widening the pool helps rather than
just adding noise.

`--random-decks 0.5` plays half of all training games on freshly generated legal
decks drawn from the whole 788-card playable pool, so the policy sees far more
than two starter lists.

**Measured result** — 40,000 self-play games, 8.7 minutes, 77 games/s, half of
them on randomly generated decks:

| opponent | untrained | trained |
|---|---|---|
| heuristic, starter decks | 0.0% | **64.0%** |
| heuristic, **decks it never trained on** | — | **55.4%** |
| random, starter decks | — | 97.5% |

Evaluation runs on a seed range disjoint from training, greedy, seats
alternated. The middle row is the one that matters for "seeing more cards": the
policy is still ahead of the heuristic on freshly generated decks built from
cards it never met in training, which is the generalisation claim the
stats-and-abilities encoding was designed to support.

For comparison, the earlier run on two fixed starter decks and the smaller card
pool reached 58.5%. Widening the pool improved both numbers.

64% is a genuine but modest edge over a decent scripted opponent. It is not a
strong player. The obvious next steps are PPO instead of REINFORCE, learning the
mid-effect decisions too, and search at inference time.

## Deck generation

```bash
python evolve_deck.py --generations 40 --pop 32 --games 60
```

A genetic algorithm over legal 60-card lists. Fitness is the only honest measure
available — a decklist is good if it wins games — so every candidate is scored
by actually playing a gauntlet in the engine. Tournament selection, single-point
crossover, random-substitution mutation, elitism, and a fitness cache.

`repair()` is the important piece: crossover and mutation freely produce illegal
lists, so every candidate is forced back inside the construction rules (60 cards,
≤4 per card number, ≤16 FLIP, at least one Cookie) before it is ever evaluated.
Tests assert legality holds even from adversarial input like an all-FLIP pool.

The pool defaults to ST8+ST9, the sets whose effects are fully implemented.
`--pool implemented` widens it to every card the engine plays correctly (coded
effects plus genuinely vanilla bodies); `--pool all` opens the whole database,
but every uncoded card plays as a vanilla body there, so the search would be
optimising against a fiction.

### Not overfitting the shuffles

The first version of this scored every candidate on one fixed set of seeds. It
reported a 78.3% win rate; the same deck managed 45–52% on fresh shuffles. It had
evolved against the shuffle order, not the game.

Two changes fix it, and both are pinned by tests:

- **Each generation is scored on a fresh seed block.** Candidates within a
  generation share a block so the comparison between them stays fair, but elites
  are re-scored every generation — a deck cannot ride one lucky evaluation.
- **The winner is chosen on a validation block**, not on the best training score.
  Taking the maximum of many noisy estimates is biased high; the per-generation
  champions are re-scored on shuffles none of them were selected on.

`evolve_deck.py` then reports a **holdout** number from a seed block the search
never touched at all, next to the hand-built starters measured on that same
block, so the comparison is like-for-like.

**Measured result** — 40 generations, population 32, 7.2 minutes:

| deck | holdout win rate (500 unseen games) |
|---|---|
| **evolved** | **56.4%** |
| ST9 starter (hand-built) | 54.2% |
| ST8 starter (hand-built) | 44.4% |

So the evolved list beats both hand-built starters, but narrowly — and note the
validation score was 63.3% against a 56.4% holdout, so a few points of
selection bias survive even now. Trust the holdout column.

### Using the RL agent as the pilot

`--agent rl` scores candidates with the trained policy instead of the heuristic;
`--agent both` scores under both and keeps the **worst** of the two.

That last mode exists because of a measured pathology. Deck strength is not a
property of the deck alone — it is a property of the deck *and* whoever flies
it. Two decks evolved on identical budgets, one under each pilot, then played
round robin (`compare_decks.py`, 200 games per pairing, seats alternated):

| deck | avg under heuristic | avg under RL |
|---|---|---|
| ST9 starter (hand-built) | 57.3% | **53.3%** |
| evolved under heuristic | **57.5%** | 47.0% |
| evolved under RL | 43.8% | 51.7% |
| ST8 starter | 44.0% | 39.0% |

**Each evolved deck is strongest under the pilot that evolved it, and the
ranking flips when you swap pilots.** The heuristic-evolved deck drops 10.5
points when the RL agent flies it; the RL-evolved deck gains 7.9. Neither GA
clearly beat the hand-built ST9 list, which is the most robust deck in the
table. Evolving under one pilot partly optimises for that pilot's blind spots,
which is what `--agent both` with `min` aggregation is designed to reject.

Note that head-to-head round robin and "win rate vs gauntlet" are different
metrics: the gauntlet number includes a mirror match when the deck under test is
itself in the gauntlet. `compare_decks.py` excludes self-pairings.

### Co-evolution

`coevolve.py` alternates: evolve a deck against the current agent, add it to the
pool, train the agent further on the widened pool, repeat.

**It did not work well**, and the numbers are worth keeping:

| round | deck holdout | agent vs heuristic | agent vs unseen decks |
|---|---|---|---|
| 1 | 2.5% | 50.5% | 60.5% |
| 2 | 25.0% | 53.5% | 52.5% |
| 3 | 30.5% | 51.5% | 64.0% |
| 4 | 21.0% | 51.0% | 63.5% |

Deck quality stayed far below the 56.4% a single-pilot run reaches, and the
agent regressed against the heuristic (62% → 51%). The diagnosis is search
budget, not the idea: co-evolution ran on the full 776-card pool with 12
generations × 16 population, against 40 × 32 on a 40-card pool for the
successful run — a vastly larger space with less search. The controlled test
supports this: at equal budget on the same small pool, the RL pilot produced a
57.4% deck against the heuristic pilot's 56.4%. Give co-evolution a real budget
before trusting it.

What the deck search found is more interesting than the margin: it maxes FLIP cards at the
legal 16 and runs 8 copies of "Return this Cookie to your hand" flips (Muscle
Cookie, Blue Whale Cookie). Those sit in the HP pile and, when revealed by
damage, bounce their own host mid-attack — cancelling the rest of the damage and
denying the opponent the Level they would have banked in the break area. It also
plays 4-of both HP6 LV3 bodies and ten traps. Nobody told it any of that.

## Extending it

**Adding a card.** Drop a function in `braverse/impl/<set>.py`:

```python
@effect("ST9-007", Trigger.ACTIVATE)
def peppermint_activate(ctx):
    """If there are 3 cards or less in your hand, draw up to 1 card."""
    if ctx.hand_size <= 3:
        ctx.draw(1)
```

The docstring is the printed text; the body is the same sentences in `Ctx`
primitives (`ctx.draw`, `ctx.discard`, `ctx.deal_damage`, `ctx.gain_hp`,
`ctx.select_enemy`, `ctx.rest_support`, …). Import the module from
`braverse/impl/__init__.py` and it registers itself.

**Adding a set.** `python coverage_report.py --phrases` ranks the rules
sentences by how many cards use them. The top of that list is very templated —
`That Cookie receives N damage.` (177 cards), `Draw up to N card from your
deck.` (77), `That Cookie gains +N HP.` (51) — which is what makes a
text→effect compiler viable as the next step. It would emit exactly the same
`Ctx` calls the hand-written cards use, so the two can coexist and the
hand-written version always wins.

**A stronger bot.** `HeuristicAgent` is one-ply and greedy: take lethal, kill
what it can, keep a Cookie on board, prefer bodies whose Level it can afford to
lose, keep the support count climbing. Because the state is
cloneable and actions are fully specified, determinized MCTS is a drop-in
replacement — `game.clone()`, shuffle the unknown zones, roll out with
`RandomAgent`.

Known gaps: 【Awaken】, 【Special Play】, 【Equip】, 【Skill】 and the EXTRA deck
are not modelled, and ST9-009 Wave Drop's "discarded by Sea Fairy" trigger is
handled inside Sea Fairy's effect rather than as a trigger of its own.

## Source

Rules are taken from the official English **CookieRun: Braverse PLAY GUIDE**
(Devsisters Corp.). Quoted phrases in `config.py` and the card modules come from
that document.
