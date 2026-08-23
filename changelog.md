# Changelog

## 0.2.26

- **New: a Replays tab, and every game you finish is kept.** Any of them can be
  watched again on the real board — the same cards, the same animations, the
  same log — with Pause, Step and the speed slider working on it exactly as
  they do while two bots play.
- What is saved is the list of decisions both seats took, not a video: watching
  one plays the game again from the same decks, seed and answers, so it comes
  out identical rather than approximately right. A whole game is about 15 KB.
- **Save the game in progress** keeps a match that is still being played. It
  replays up to the moment you saved it and then says so.
- **Download** a replay and send it to someone: they drop the file onto their
  own Replays tab and watch your game, without it being saved on their machine.
- A replay from an older version that this one no longer plays the same way
  stops and says which decision it disagreed about, instead of quietly showing
  you a game that never happened.

## 0.2.28

- **There is a title screen now.** Opening the player lands on a front door
  rather than an empty table: play a bot, watch two bots play each other, or
  play someone on your network. Each one opens the usual New match dialog with
  that mode already set, so decks and seeds are still yours to pick. The guided
  first game and the deck builder are one click away from it too.
- Refreshing in the middle of a game still comes back to the game, and a
  `?room=` link still goes straight to the room — the menu only appears when
  there is nothing to come back to.
- **When a match ends you are asked what next**: a card over the board offers a
  new match on the same two decks (fresh shuffle) or a trip back to the title
  screen. *Stay and look at the board* puts it away so you can read how it
  finished. In a room the new-match button is the rematch both players agree to.
- Leaving a room now returns to the title screen instead of the New match
  dialog.

## 0.2.27

- **Misclicks cost you a move less often.** The moves you cannot take back — an
  attack, a block, End turn, Pass, declining an effect, and answering a question
  by pointing at a card — are now *held* rather than clicked: press and hold for
  a moment, a ring fills over the pointer, and the move goes. A quick click does
  nothing but tell you to hold. Everything else still takes one click, so a turn
  is no slower than it was.
- Controls that want a hold say so up front: a dashed edge and a small "hold"
  tag. The number-key shortcuts hold too — keep the key down.
- **How long the hold takes is yours to set** — the *hold* dropdown beside
  *confirm*, from 0.2s to 0.8s, default 0.35s. Slow hands and fast hands want
  different numbers, so it is a setting rather than a decision.
- **A click that lands while the board is still moving is ignored.** Answering a
  question redraws the table under your pointer, and a click already on its way
  used to land on whatever slid into that spot. Anything sent in the first
  quarter second of a new question is dropped with a hint instead.
- New **confirm** setting in the header: *key moves* (the above; the default),
  *every move* if you want plays, supports and the mulligan held as well, or
  *off* for the old one-click game. Your browser remembers it.
- The guided first game teaches whichever one is turned on.

## 0.2.26

- **A discarded card now flies to the trash where both players can see it.** It
  leaves the hand face up, hangs for a beat with a "discarded" tag on it, and
  lands on the owner's trash pile. Paying a `<Discard a card>` cost used to be
  completely silent — a hand quietly got shorter, a pile quietly got taller —
  so the only person who knew a price had been paid was the person who paid it.
- Every way a card reaches the trash from a hand plays it: a cost you chose to
  pay, an opponent's "discard 1", or a card that made you throw something away.
  Items and Traps keep their own spotlight instead, since they already show
  themselves on the way through.

## 0.2.25

- **Your turn now stops on the support phase.** Instead of the free support card
  being one option among twenty that you could scroll past and forget, the turn
  opens by asking for it: your hand is the only thing you can act on, and the
  prompt reads "place 1 card from your hand as support, or pass to your main
  phase".
- **Pass to main phase** sits in the middle of the table where End turn usually
  is, and takes one click to move on with the card kept in hand. Place a support
  instead and the step closes the same way — either way the rest of the turn
  comes straight back.
- The round track follows along: **Support** lights up as *now* while the step
  is open, and settles to *done* when you place one or *passed* when you skip it.
- Changed your mind after passing? The card's own menu still offers **Place as
  support** for the rest of that turn — the step is a reminder, not a lock.

## 0.2.24

- **New: a Learn button that teaches you the game while you play it.** It deals
  a real match against the bot and walks you through it — the toss, the
  mulligan, your opening Cookie, what each part of the mat is for, then one
  instruction at a time: place a support, put a second Cookie out, end the turn,
  swing. Each step waits for you to actually do the thing rather than for a
  Next button.
- It never takes the controls off you. Nothing is blocked, nothing is played for
  you, and doing something else instead is fine — the tutorial notices and moves
  on with you. **Skip this** walks past any step, **End tutorial** closes it.
- It also speaks up the first time the game asks something new: a block, a trap
  window, a strip of cards to pick from, an optional cost in angle brackets.
- It copes with a first game going its own way — losing the toss, or never
  drawing a second Cookie — instead of getting stuck waiting for a moment that
  is not coming.

## 0.2.23

- **A Cookie arriving on the board is now a real moment.** Its card comes up
  large in the middle of the board, long enough to actually read the name and
  the text, then dives into the slot it is about to fill and slams into it —
  spheres in the Cookie's own colour thrown off the card's edges and dust
  dragged out along the table. Every arrival gets it,
  whether the Cookie came from your hand, the trash, the break area, the
  support area or the 【EXTRA】 deck.
- The card lands at exactly the size and place of the slot it is going into, so
  it hands over to the real board without a blink.
- A Cookie being called in has its own sound: a rising swell while the card is
  up, and a thump when it lands.
- Bots wait for the new animation to finish before moving again, so nothing is
  played over the top of an arriving Cookie.

## 0.2.22

- **The seven 【EXTRA】 cards now play their full text.** Last version gave them
  their entry conditions; this one writes the rest — 【On Play】, 【Activate】,
  attack riders and Dark Enchantress Cookie's "cannot be moved by your
  opponent's effects". They are no longer vanilla bodies with a gate.
  - Shadow Milk Cookie {R} takes a card out of your opponent's hand as HP, and
    steals HP off a Cookie it swings at.
  - Shadow Milk Cookie {Y} bins a LV.1 Cookie out of your break area to buy
    back room on the clock, and can discard a FLIP from hand mid-attack to fire
    its effect from the trash.
  - Shadow Milk Cookie {G} feeds its own support area off the deck, and can
    bounce a support card for a point of damage.
  - Shadow Milk Cookie {P} trades a {P} card from your hand for one of your
    opponent's, and chips in an extra damage once their trash passes 20.
  - Pure Vanilla Cookie reveals the top card and pays out on a {B} LV.2 Cookie.
  - Avatar of Destiny and Dark Enchantress Cookie were complete with their
    gates.
- Shadow Milk Cookie {R} takes its card from the opponent's hand at random,
  matching every other card in the game that reaches into a hand — you get no
  look at it first, so choosing would mean seeing their whole hand.
- Dark Enchantress Cookie's 【Awaken】 still cannot happen: it needs a LV.3
  Dark Enchantress Cookie with 【Special Play】 on the board, and 【Special
  Play】 is not modelled. The condition is written correctly and will start
  working when that is.

## 0.2.21

- **Seven 【EXTRA】 cards were being treated as ordinary Cookies.** Avatar of
  Destiny, Dark Enchantress Cookie, Pure Vanilla Cookie and four of BS9's
  Shadow Milk Cookies all print 【EXTRA】 on the card, but the card data filed
  them as plain Cookies. They now go where they belong — a second pile of at
  most 6, played through the condition printed on them — and they show up in
  the deck builder.
- **They could be cheated into play, and can't be any more.** Because they
  counted as ordinary Cookies, they sat in the main 60 and could be played from
  hand for free with their condition ignored: BS9-102 says "can be played if
  there are 20 cards or more in each player's trash" and could be dropped on
  turn one for nothing. Deck validation keeps them out of the 60, and an EXTRA
  card in hand is never offered as a play.
- Their entry conditions are all written, including 【Awaken】 for Pure Vanilla
  Cookie and Dark Enchantress Cookie. Avatar of Destiny is complete. The other
  six still play as vanilla bodies — their 【On Play】 and 【Activate】 text is
  next.
- Deck builder: the "playable" filter now counts an EXTRA card's entry
  condition as part of its text, so a gated card is no longer hidden.

## 0.2.20

- **"View the top 3 cards of your deck" now shows you all three.** Aloe Cookie
  (BS2-040) says to view the top three and take a {B} card from among them; it
  was only ever *offering* you the blue ones, so a card whose whole point is
  looking at three showed you one. All three are laid out now, with the ones
  the condition rules out drawn dimmed beside the one you can take. If none of
  them qualifies you still get to see what went past.
- **Blueberry Pie Cookie (BS9-101) works.** Its 【Activate】 — pay {P}, bin the
  Cookie, view three, reveal a {P} card to your hand and trash the rest — had a
  stub whose card number was mistyped, so it had never done anything. It is
  implemented, and a test now catches any effect registered against a card
  number that does not exist.
- Captain Caviar Cookie (ST4-013) gets the same treatment and reads the same
  way as the other two.
- Only the card you reveal is named in the log. What you looked at and put back
  is yours; the log is shared.

## 0.2.19

- **The log says what hit you, not just that something did.** "takes 2 effect
  damage" covered a trap sprung on your turn, an 【Activate】 skill, an item and
  the "Then, …" rider on an attack — four different things, one line. Every log
  line written by an effect now names the card *and* what sort of thing it was
  being: `[Piercing Arrow of Purity · trap]`, `[Blue Slushy Cookie · FLIP]`,
  `[Wind Archer Cookie · 【Activate】]`. Attacks name themselves the same way:
  `Sea Fairy Cookie attacks Leek Cookie with Sea of Stars for 3`, and the damage
  line repeats the source so it stands on its own.
- **The damage number on the board is labelled.** The red `-3` floating off a
  Cookie now carries the name of whatever dealt it underneath, which matters
  most when a swing, its rider and a trap all land in the same second.
- **Hover any card name in the log to see the card.** The log names cards that
  are in a trash, still in a deck, or already gone — none of them anything you
  could look at. Now every name in it previews in the panel exactly as a card on
  the table does.
- **Revealed HP cards get a proper strip.** The record of what a hit turned over
  moves to the middle of the right-hand panel and is drawn at hand size instead
  of as 46-pixel slivers, so you can actually read the FLIP you blinked through.
  It scrolls sideways when a big hit turns over six of them.
- **The log is shorter**, giving that height to the reveal strip and the card
  viewer above it.

## 0.2.18

- **The move list on the right is gone; the moves are on the cards.** The
  stack of "Play Wave Drop / Support: Wave Drop / Attack →…" buttons down the
  right-hand side was the biggest thing on screen, listed cards you were
  already looking at, and took the room the card viewer should have had.
  Instead: cards that can do something carry a thin ring, cards the game has
  actually stopped for glow green and stand up out of your hand, and clicking
  a card opens a menu of what that card can do — attacks named the way the card
  names them, one row per target. It works the same on both sides of the table.
- **End turn and anything with no card to point at sit in the middle.** End
  turn is where it always was, between the two mats, now joined by Pass, a
  standalone 【EXTRA】 play and any other move that names no card you can
  click. Nothing can fall through: a move with nowhere to be clicked lands
  there automatically.
- **The card viewer got the space back.** The panel on the right is now the
  prompt, the card you are hovering at nearly twice the size, one line telling
  you where to click, and the log. Hovering a row in a card's menu shows the
  card that row is about — for an attack, the Cookie you are about to hit.
- **A "pick these cards" question can be declined from the strip itself.** The
  decline used to live at the bottom of the move list, which no longer exists.
- Number keys now take rows from an open card menu, or the buttons in the
  middle of the table when no menu is open.

## 0.2.17

- **The card you are hovering shows up beside the board, not on top of it.**
  The enlargement used to follow the cursor, which meant it covered whatever
  you had just leaned in to look at — hover a Cookie in your battle area and
  the preview sat over the row it was standing in. It now lands in a fixed
  panel at the top right, directly above the move list, so the board is never
  hidden and the card is always in the same place. Hovering a move in that list
  puts its card in the same panel, so the list and the table answer a hover the
  same way. The deck builder and the full-screen deck view still preview under
  the cursor — they have no panel to dock to.
- **Pick your opening Cookie by clicking it in your hand.** The Cookies you can
  open with now stand up out of your hand and glow, exactly the way an armed
  trap does during an attack, and clicking one plays it. The separate list of
  Cookies to choose from is gone — it was naming cards you were already looking
  at.
- **A hand with no Cookie in it can keep redrawing.** Your first mulligan is
  free and unchanged. If the hand you draw into still has no Cookie, you are
  now asked again rather than having the redraw done for you, and you can keep
  going until you find one — each of those extra redraws lets your opponent
  draw 1 card. Mulliganing a hand that *does* have a Cookie is still a one-off:
  the free redraw is the whole allowance for shopping around.

## 0.2.16

- **Blocker Cookies are back in the deck builder.** Every Cookie whose
  【Blocker】 is priced in energy — Kiwi Cookie, Mystic Opal Cookie, Milk
  Cookie, Churro Cookie, Wizard Cookie and eleven more — was hidden by the
  builder's "playable only" filter, and was skipped by deck evolution for the
  same reason. The engine has always charged that price correctly; it was the
  card list that counted 【Blocker】 as text nobody had implemented. Sixteen
  Cookies are now searchable and deck-legal again. A Blocker priced in
  something the engine cannot charge still stays hidden, since it could not
  block at all.

## 0.2.15

- **A full-screen view of the deck, for sharing.** The deck builder's new
  **View** button opens the whole deck as a picture: one section per card
  category — Cookies, FLIP, items, traps, stages, then the EXTRA pile — with
  every distinct card at full size and its copies as a badge instead of a
  repeated row. The header carries the deck name, the card and Cookie counts
  and the colour spread. A size slider shrinks the cards until the whole deck
  fits in one screenshot, and **names** can be turned off for art only.
  Hovering a card still previews it; Esc or Close puts it away.
- **The deck buttons wrap instead of squeezing.** The row of deck actions had
  outgrown the pane, crushing "load a deck…" down to a few characters.

## 0.2.14

- **Export your decklist as a text file.** A new Export button in the deck
  builder, next to Copy, downloads the deck grouped by card type — a `--COOKIE--`
  header, then a line per card reading `4x Sea Fairy Cookie ST9-006 LV3`, and
  the same for FLIP, ITEM, TRAP, STAGE and NPC. Cards with no printed Level end
  after the ID. Lines come in the order the pane lists them, and your EXTRA deck
  is included as its own section at the end. The file is named after the deck.

## 0.2.13

- **"When your turn ends, set N cards as active" joins the end-of-turn queue.**
  An attack rider that banks an untap used to fire on its own after every
  【End of Turn】 effect had resolved. It is now one more event in the same
  queue, listed under the card that banked it, so you order it against your
  other end-of-turn effects — untapping support before or after an effect that
  wants to spend it is your call. It is left out of the question when you have
  no rested support for it to set active.

## 0.2.12

- **Parsley Tea of Invigoration (ST3-018) works.** The item was in the CSV but
  had no implementation, so it played for {G}{G} and did nothing. It now asks
  which Cookie in your trash to bring back — any Cookie, any Level — and puts
  it into your battle area with a fresh HP pile. "Play 1", not "up to 1": with
  the cost already spent the question is which one, not whether. It is not
  offered at all when your trash holds no Cookie or your battle area is full.

## 0.2.11

- **You choose the order of your end-of-turn effects.** When two or more of
  your Cookies or Stage cards have an 【End of Turn】 effect, the game asks
  which one resolves next instead of firing them in board order — it matters
  when one effect would trash, buff or heal what another was about to use.
  Effects that would fizzle anyway are not offered. Bots keep board order.

## 0.2.10

- **Ancient Healer's Gaze (ST3-016) works.** The card was in the CSV but not in
  the deck builder's default (playable) list, because the engine had no way to
  move a Cookie out of the battle area and into the support area. It does now:
  select one of your LV.2-or-lower Cookies and it goes to your support area as
  active. Its HP pile — and anything 【Awaken】ed under it — is spent, and it
  never touches the break area, so your opponent banks no Level for it.

## 0.2.9

- **Item cards go off in the middle of the table**, the way traps do — gold
  rather than red, since an item is your own play. Stage cards and Cookie
  skills keep the small pop over the card that used them.
- **Playing a Cookie lands it.** The card drops in with a squash and throws a
  burst of dust and a shockwave out from under it, in the Cookie's own colour.
  Every way a Cookie can arrive gets it — from hand, trash, break area, support
  or the EXTRA deck.
- **You can supply the real card back.** Drop the image in as
  `card_images/card_back.webp` (or `python fetch_images.py --card-back <url>`)
  and every face-down card uses it. Without it, nothing changes — the viewer
  keeps drawing its own sleeve, and the Table tab still picks between them.

## 0.2.8

- **The trash, break area and EXTRA deck read as face-up piles.** The two cards
  drawn behind the top one were card backs sticking out to its right, so the
  edge of a face-up pile looked face down. They are plain card edges now, and
  hovering anywhere on the pile — including that edge — previews the top card
  instead of nothing. The deck still shows its back, because it is face down.

## 0.2.7

- **Hero Cookie (BS9-018) only shields you on your own turn.** Its 【Your Turn】
  was being ignored, so "your Cookies take no damage from your opponent" was
  true all the time — including on your opponent's turn, which is when almost
  all damage is dealt. One of them on the board made you immune to everything.
- Golden Cheese Cookie (BS3-025) pays the `{Y}` its revival prints. It was
  bringing itself back for free.

## 0.2.6

- **Click your own Cookies to answer a prompt.** "Select up to 1 of your
  Cookies" and the like can be answered by clicking the Cookie on the board,
  the way your opponent's side already worked, instead of going to the list on
  the right. Dragging cards to play them is unchanged.
- **Yes/no questions are asked in the middle of the table**, with a Yes and a
  No button, instead of a one-item list in the corner.
- **Energy symbols are drawn as gems.** `{G}` and friends now appear as the
  coloured energy the card prints — in card text, attack costs, prompts, the
  move list and the deck builder.

## 0.2.5

- **Divine Light Crystal actually works now.** The last fix implemented the
  card but charged you its `{G}{G}` twice — once when you played the trap, and
  again inside the effect — so unless you had four green support up it quietly
  did nothing and your Cookie died anyway. It now costs what it prints.
  (Heart Stained With Lies had the same double charge.)
- **"HP cannot reach 0" also stops HP being placed into the trash.** Effects
  that strip cards off a Cookie's HP pile without dealing damage were going
  straight through the floor.

## 0.2.4

- **Traps go off in the middle of the table.** A sprung trap now dims the board
  and lands double size in the centre with its name, instead of a small pop
  over on its owner's half where you were never looking. Yours comes up gold,
  your opponent's red, and whatever the trap did waits for the card to land
  before it plays.

## 0.2.3

- **A Blocker priced `<Rest this card.>` now actually rests.** Blue Lily
  Cookie, Peperoncino Cookie, Moon Rabbit Cookie, Captain Ice Cookie and Space
  Doughnut were redirecting every attack in a turn for free and still standing
  afterwards to attack on their own.
- **A trap and a block are alternatives.** Once you spring a trap you cannot
  also block that attack, and once you block you cannot spring a trap.
- **The log says when a swing got shaved.** An attack is announced at its
  printed damage before you get the chance to respond; if a trap or a
  defensive skill reduces it, the log now reads `attack is reduced to 1
  (from 3)` instead of quietly landing for less.

## 0.2.2

- **Mulligan added.** After the opening hand is dealt you are asked once
  whether to send the whole hand back, shuffle, and draw six new cards. It runs
  before the forced "no Cookie in hand" redraw, so a mulligan into a bad hand
  still gets that safety net. Bots are not offered one.
- **Mystic Flour Cookie (BS8-059) does what it says.** Its 【Activate】 was
  implemented as "place 2 cards from the top of your deck into your support
  area" — text from no card at all. It now places 2 cards from the top of
  *each of your opponent's Cookies' HP* into the trash, and it is only offered
  when it can actually be paid for and there is something to hit.
- **Hero Cookie (BS5-063) counts active support again.** "If there are 2 active
  cards or more in your support area" was being read as "2 cards or more", so
  it drew every single turn. Longan Dragon Cookie (BS5-056) had the same bug on
  its end-of-turn ping.
- **Flip effects no longer play before the card is shown.** A FLIP that heals
  its host, draws, or bounces itself used to animate its result and only then
  turn the card face up. The whole scene is now played in the order it actually
  happened: the swing, the card turning over, then what it did.
- **A faint waits for a revealed card to clear**, so the board never changes
  under a card you are still reading.
- **Attack riders that print `<can be used as {R}.>` now cost that energy.**
  They were firing for free — Pitaya Dragon Cookie was adding a point of damage
  after every swing it made, all game, at no cost. 72 cards carry that line.
- Mystic Flour Cookie (BS11-053) asks before trashing itself on its attack —
  that is a bracketed cost, so it is your call, not automatic.

## 0.2.1

- **The log now says which card did it.** Every line written while a card's
  effect resolves is stamped with that card's name — `[Blue Slushy Cookie]
  Leek Cookie gains +1 HP`. A FLIP that fires in the middle of an attack names
  the FLIP, not the attack.
- **"Rest up to N cards" asks you which ones.** You get the active support
  cards as a pick-and-confirm and choose both which go down and how many —
  including none. Previously the game silently rested the first N for you.
- **Divine Light Crystal (ST3-020) works.** It did nothing at all before: you
  paid the `{G}{G}` and the Cookie fainted anyway. It now protects the Cookie
  you pick for the rest of the battle.
- **"This Cookie's HP cannot reach 0" no longer swallows the damage.** The hit
  turns every card it paid for — so every FLIP in the pile still goes off — and
  a fresh card comes off your deck each time the pile would empty. The Cookie
  survives; it just costs deck to do it.
- **Healing no longer inflates a Cookie's max HP.** A Cookie healed above its
  printed HP reads `8/6 HP (+2)`, with the spare cards shown as green ticks on
  the end of the HP bar instead of a longer bar.
- **Healing is animated.** A green glow around the card and a green `+N`
  rising off it, played on the same beat as the damage that caused it — so a
  FLIP handing its host HP back mid-attack is something you can see happen.
