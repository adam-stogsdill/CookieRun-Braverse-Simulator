# Changelog

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
