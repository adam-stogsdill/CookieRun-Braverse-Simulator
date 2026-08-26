# Changelog

## 0.2.53

Checked the engine against the official **Comprehensive Rules Ver.1.8**
(27 July 2026) — a much longer document than the PLAY GUIDE this project was
first written from. Nine rules were being played differently.

- **You can only support before you act.** The Support Phase runs *before* the
  Main Phase, and the Main Phase is only three things: play cards, activate
  effects, battle. Placing a support card is not one of them. Until now you
  could attack, watch the FLIPs turn over, and only then decide which card to
  spend as energy — the phase strip already drew it as a step, and now it
  really is one. The practice bot supports first accordingly.
- **One 【EXTRA】 card a turn.** "The Turn Player can, once per turn, play an
  【EXTRA】 Cookie card or 【Awakened】 Cookie card." A turn that opened two
  gates used to be able to walk the whole EXTRA deck onto the board.
- **【Special Play】 Cookies now cost what they print.** The five of them —
  Dark Enchantress Cookie, the three BS11 Dough Cookies and promo Licorice
  Cookie — were arriving for free, which made a LV.3 6 HP body a turn-one
  drop. Each now places the Cookies it names into your trash on the way in
  (the trash, not the break area, so your opponent banks no Level for them),
  and cannot be played at all when there is nothing to place. Dark Enchantress
  can be played out of a *full* battle area, because trashing her two hosts is
  what makes room for her.
- **【EXTRA】 Cookies go home instead of into your hand or deck.** One bounced
  or shuffled away is placed face-down back in the EXTRA deck, and one sitting
  in your trash returns there at [refresh]. It can only ever be played from
  that pile, so anywhere else it was a dead card thinning your 60.
- **Running out of deck with no Cookie in the trash loses the game.** The third
  defeat condition, and the one the engine had no answer for: it quietly
  reshuffled and played on forever. [refresh] also now asks for a *Cookie* of
  LV.1 or higher, which is the only thing a break area holds.
- **An attack by a Cookie that is no longer there deals no damage.** If a trap
  or an attack rider removes the attacker from the battle area, the battle ends
  where it stands. It used to swing anyway.
- **【Blocker】 no longer needs an active Cookie**, unless the card's own price
  is resting itself. The rule asks for the activation cost and nothing more.
- **"A Cookie card in your hand to play" now means one you can actually play.**
  An 【EXTRA】 Cookie that found its way into a hand, or a 【Special Play】 one
  whose condition cannot be met, is no longer counted as the Cookie standing
  between you and defeat — nor offered as a replacement it cannot be.
- **Deck check: at least one non-【Special Play】 Cookie**, since a deck of
  nothing but those has no way to put its first Cookie on the board.

## 0.2.52

- **Almost every promo (P) card is now playable — 30 of the 32 that were not.**
  GingerBright's hand-size buff, Strawberry Cookie's faint trigger, Almond
  Cookie pushing a Cookie into the support area, Pumpkin Pie Cookie stripping
  an HP card off everything opposite, Hall of Ancient Heroes making an Ancient
  Cookie's attack payable in any colour, Truffle Cookie trading itself for a
  Cookie out of your support area, and two dozen more.
- **Birthday Cake Cookie asks you.** "If today is your birthday" is not
  something the board knows, so the card puts the question to its controller
  rather than reading your computer's clock — which would also have meant a
  saved replay played back differently on a different day.
- **Fixed: five cards were playing only the first half of a sentence.**
  Anything worded "select ... **and** <do something with them>" stopped at the
  comma. Hermit Crab's Shell (BS2-050) never removed the Cookie it selected and
  so did nothing at all; Essence of Rejuvenation (BS4-040) and Lilybell Cookie
  (BS4-058) never played the Cookie they picked; Prickly Cacti Gloves (BS2-006)
  and Muscle Cookie (P-015) never paid the HP they cost; Time Travel Ticket
  (ST2-018) never showed you the HP pile. All six now play their whole text.
- **Fixed: "[Ancient] Cookies" matched nothing.** The keyword in brackets was
  also being read as a card name, and no Cookie is named "Ancient", so any card
  that targeted them could never find a target.
- **Fixed: "another [Pizza Cookie]" was true of a lone Pizza Cookie**, which
  handed it a permanent attack bonus it had not earned.

## 0.2.51

- **Cards that ask what happened earlier in the turn now work.** Space Doughnut
  (P-098) counts support cards you spent into the trash, Tiger Lily Cookie
  (P-095) checks whether you played an Item, White Peach Cookie (P-093) fires
  when its own HP came off, Choco Cup Cookie (BS9-083) when a Cookie went into
  your deck, TBD Machine Room (BS6-107) when you replayed a Cookie out of your
  trash, Puppet Theater Stage (BS9-070) on support cards trashed, and Dino-Sour
  Cookie (P-109) and Sour Belt Cookie (P-110) on 【Arena】 Cookies reaching your
  break area — each of those two opening on *either* of its two conditions, as
  printed.
- **"Select 1 of the following" cards work.** First Watcher's Bow (BS3-116),
  Passionate Hollyberry Kingdom (BS3-023) and Glorious Crème Republic (BS3-095)
  put both printed lines in front of you and run the one you pick. A line the
  board makes impossible is not offered, so a card cannot be thrown away on an
  option that could never have done anything.
- **Fixed: "if there are no ..." meant its own opposite.** Any card gated on
  something *not* being on the board was reading the condition backwards. Red
  Velvet Dragon (BS11-105) was the card this actually broke — its attack rider
  needed a 【Special Play】 Cookie in your battle area and fired every swing
  instead. It is now held back rather than played wrongly.

## 0.2.50

- **Nine more cards are playable.** Cards that pay with a Cookie's own HP now
  work: Spicy Power Juice (BS1-023) and Desert Oasis (BS1-026) drain a Cookie
  down to its last HP for the attack bonus, and Sniffly Cocoa Palm (BS5-042)
  costs a single HP card. A drain never faints the Cookie paying it, and a
  Cookie already at 1 HP pays nothing.
- **Cards whose numbers scale with the board work too.** Golden City's Control
  Chamber (BS3-048) and Seasick Canoeing (BS5-043) count LV.3 Cookies in your
  break area, Millennial Twig (BS4-041) heals per {Y} LV.3 there, Jelly
  Pom-Poms (BS1-048) pays +1 attack for every *two* {Y} LV.1 Cookies, Old
  Vanilla Orchid Locket (BS3-092) draws per LV.2 Cookie anywhere on the table,
  and Jellied Jellyfish Potion (BS2-048) draws for each of your opponent's
  Cookies that fainted this turn.

## 0.2.49

- **Fixed: a Cookie that pays itself as a cost no longer counts as a knockout.**
  Cards whose cost reads "Place this Cookie in the trash" — Crunchy Chip Cookie
  (BS8-119) among them — were sending themselves to the break area instead of
  the trash, handing the opponent a free Level toward winning. They now go to
  the trash, as printed.
- **Soul Jam: Light of Resolution (BS3-115) now works.** It trims 1 HP from up
  to two of your opponent's LV.2 or lower Cookies, then equips itself to your
  Dark Cacao Cookie — and while it rides there, that Cookie cannot be selected
  by your opponent's effects and cannot be trashed by them. Your own cards can
  still target it.
- **The other four Soul Jams work too.** Light of Passion (BS3-019) burns a
  Cookie for 2 and then rides Hollyberry Cookie for +1 attack damage; Light of
  Abundance (BS3-043) sweeps the other side for 1 and gives Golden Cheese
  Cookie +2 HP; Light of Freedom (BS3-066) cycles a support card and then sets
  1 support card active every time White Lily Cookie attacks; Light of Truth
  (BS3-091) digs 3 for 2, puts the leftover back on top of your deck, and draws
  you a card every time Pure Vanilla Cookie attacks.
- **An equipped card now shows on the board.** A Cookie wearing a Soul Jam
  carries an EQUIP badge naming what is attached — it is public, and it is the
  reason that Cookie is hitting harder or drawing you cards.
- **Fixed: an equipped Soul Jam was also being filed in the trash**, so the
  same card sat in two places at once and came back a second time when its
  Cookie left the battle area. This affected every 【Equip】 card.

## 0.2.48

- **You can now set how big the board is drawn.** Settings has a **Sizes**
  section with a slider for each part of the table — the battle area's cards,
  your hand and the support rows, how wide the playmat spreads, and the side
  panel with the log and the card viewer. They move the board as you drag them,
  so you can see where you are landing, and **Reset sizes** puts everything
  back. What you pick is remembered in this browser.
- **The deck builder zooms, with − and + buttons in its own filter bar.** Click
  for a small step, or hold one down and it keeps going — faster the longer you
  hold it, so crossing the whole range takes a couple of seconds rather than
  forty clicks.
- **It zooms much further than before**, from 40% up to 400%: small enough to
  fit twenty-one cards across for skimming a set, large enough to read a card's
  rules text without leaning in. The decklist's thumbnails follow along, the
  zoom is remembered, and **Reset sizes** puts it back with the others.
- **Fixed: the game could not see cloudflared (or ngrok) even though it was
  installed.** Opening the game by double-clicking it gives it a much shorter
  list of places to look for programs than a terminal does — and on a Mac that
  list leaves out exactly where Homebrew installs things, so a perfectly good
  install came back as "no tunnel client found". It now looks in the usual
  install folders too, on both Mac and Windows.
- If you install one while the game is open, close and reopen
  **Settings → Playing online** and it will be there — no restart needed.

## 0.2.47

- **The seats are called Player 1 and Player 2** on the board, in the turn
  line, in the match set-up and in the replay shelf, instead of Seat 0 and
  Seat 1. Counting players from one is what everybody does.
- **You can choose which service online play uses.** Settings → Playing online
  has a picker listing cloudflared, ngrok and playit, marking which of them are
  installed. It is left on **choose for me** by default, which prefers
  cloudflared because it needs no account — worth changing only if you
  installed one of the others on purpose.
- Picking one you have not installed says so and keeps working with whatever
  you do have, rather than quietly using something else or refusing to play.
- The same choice is available as `--tunnel` for anyone starting the game from
  a terminal, and the two agree: the flag sets the setting.

## 0.2.46

- **playit.gg now works as well**, alongside cloudflared and ngrok, on both
  Windows and macOS. It is free and is built for people behind the kind of
  home internet connection the other two struggle with. Two things are done
  once on your playit account rather than here — claiming the agent, and adding
  a TCP tunnel pointed at `127.0.0.1:8071` — and Settings tells you both.
- **Invitations are now sealed, whichever service you use.** The offer and reply
  that set up a game are encrypted with the code itself, so what travels only
  ever contains a meaningless name and a locked box. Nobody in between can read
  your invitation or swap themselves in as your opponent — including the tunnel
  service. This is what makes playit safe to use, since its free tunnels have
  no encryption of their own.
- **Codes are longer, because the code is now the key.** Still one line, still
  something you can send in a message — but keep sending it the way you would
  send an invitation, since whoever has it can take the seat.

## 0.2.45

- **Setting up online play is now a screen, not a command line.** Settings has a
  **Playing online** section that tells you what this computer is missing and
  fixes it: one button installs what is needed, and a box takes your ngrok
  authtoken if you use ngrok. **Check it works** actually opens a connection
  rather than guessing, and **Start a game** takes you straight there.
- **The install button shows the command it is about to run** and the output
  while it runs, so nothing happens to your computer that you cannot see. If
  there is no package manager it can drive, it offers the download page instead
  of fetching anything itself.
- **Your authtoken is saved into ngrok itself**, the same as running
  `ngrok config add-authtoken` by hand, so it is set up once and everything on
  the machine can use it. It is never displayed again, never sent back to the
  page, and never appears in an error message.
- Trying to start a game on a computer that is not set up now offers to set it
  up, instead of only reporting what is missing.

## 0.2.44

- **ngrok now works without setting it up by hand first.** If you use ngrok
  rather than cloudflared, you can hand the game your authtoken with
  `--ngrok-authtoken <token>`, or leave it in `NGROK_AUTHTOKEN`, and add
  `--save-ngrok-authtoken` once to have it remembered for future runs.
  `--forget-ngrok-authtoken` removes it again. If you already ran
  `ngrok config add-authtoken`, nothing changes and you need none of this.
- **A token you save is kept in your home folder, readable only by you**, and
  is never written next to the game — where it would be copied along with it if
  you ever sent someone a build.
- **"ngrok needs an authtoken" now says so.** Starting a tunnel with an
  unauthenticated ngrok used to fail with "printed no address in 30s", which is
  true and useless; it now names the problem and links where to get a token.
  The token is also kept out of any error message the game shows or logs.
- **Playing someone directly now takes one short code.** Whoever starts the
  game gets something like `c.K7QP9X.neither-founded-marks-suse` — one line,
  short enough to type — and the other player just enters it. That is the whole
  exchange: no walls of text, and nothing to send back. The game starts by
  itself once they use it.
- **Only the person starting the game needs anything installed.** Hosting opens
  its own connection when you click, so there is no flag to have remembered,
  and whoever joins needs nothing at all. If the host is missing the piece it
  needs, the game now says so in terms of what you were trying to do, and names
  the one command that fixes it.
- A code is an invitation and works like one: anyone you send it to can take the
  seat, the first person to use it gets it, and it stops working after half an
  hour so an old message in a chat log is not still a way in.

## 0.2.43

- **The game wears its own icon while it is running.** The window in the
  taskbar on Windows and in the Dock on macOS now shows Ginger Brave instead of
  a blank placeholder — the icon was on the shortcut, but the window itself had
  never been told about it.
- **The icon is sharp at every size.** It is now drawn from a square,
  multi-resolution source, so the Start Menu, the Dock and the browser tab stop
  showing a stretched 32-pixel version of it.
- **Re-installing over an older copy no longer keeps the old blank icon** on
  macOS, and the Windows shortcut names the icon rather than hoping the shell
  finds it.

## 0.2.42

- **The log names every card by its card number.** Lines now read "Peppermint
  Cookie (ST9-007)" rather than just the name, and hovering one previews that
  exact card. Hundreds of names are printed on more than one card, so a hover
  used to show whichever printing came first — often a different Cookie than
  the one that just attacked.

## 0.2.41

- **You can see when the other player is thinking.** Their side of the table
  shows a small pulse next to their name while they are being asked something,
  so a quiet moment reads as a turn in progress rather than a game that has
  stopped.
- **An attack now says that it can still be answered.** When you swing, their
  side turns amber and says they may trap or block before it lands — and the
  player being attacked is told that is what the question is, instead of just
  "your move" in the middle of your turn.

## 0.2.40

- **Playing from the second seat drags cards to the right place.** Dropping a
  card onto your support area, battle area or stage now works on your own half
  of the table whichever seat you are in. Sitting in seat 2, those drops only
  landed on your *opponent's* half of the board — so placing your own support
  meant dragging the card across the table into their area.

## 0.2.39

- **Playing someone directly now checks the version on both sides.** Before,
  only the player who joined was told about a version gap; the one who started
  the game just saw the other person stop responding. Now whoever is on the
  older build is named, to both people, before a card is dealt — and the side
  that refuses tells the other why instead of going quiet.
- **A page left open across an update says so.** If the game restarts on a newer
  version while you have it open, a bar appears with a Reload button rather than
  letting you join a game with a stale page.

## 0.2.38

- **Export saves a file again.** Exporting a deck writes it straight into your
  `decks/` folder next to the game and tells you the path, instead of opening
  the decklist as a page you could not get out of. If a name is taken it
  numbers the new file rather than writing over the old one.

## 0.2.37

- **The installer is now the only file you need to send anyone.** It carries
  the game inside it, so there is no second file to keep next to it and nothing
  to unzip in the right order — one download, double-click, done.
- **The game has an icon.** GingerBrave shows up on the browser tab, on the
  shortcut the installer makes, and on the window itself.

## 0.2.36

- **You can import decks.** The deck builder has an **Import** button: choose a
  file, drop one (or several) onto the deck panel, or paste a list straight in.
  It reads decklists this game exported, lists copied from its Copy button, and
  ordinary lists written by hand or copied off a website — `4x ST9-007`,
  `ST9-007 x4`, `3 ST9-007 Sea Fairy Cookie`, or just the card's name.
- **It tells you what it could not read.** Any line it did not understand is
  shown back to you exactly as you wrote it, so a list that came in four cards
  short says which four lines to fix instead of quietly playing wrong.
- **A card named without its id is a guess, and says so.** Many cards share a
  name across sets; the importer picks one, tells you how many it had to choose
  between, and you can swap it in the builder.
- **A complete deck is saved as you import it**, named after the file it came
  from, and is in the New match menu straight away. A half-finished list opens
  in the builder instead so you can finish it.

## 0.2.35

- **The download now comes with an installer.** Run `install-braverse` and it
  asks where to put the game, sets it up there, and offers to make a shortcut —
  in the Start Menu on Windows, in your Applications folder on a Mac, where you
  can drag it to the Dock. It never asks for an administrator password, and
  uninstalling is deleting the folder it made.
- **It makes folders for the things you add.** `decks/` for decklists you or
  your friends make, `card_images/` for card art, `profiles/` for players and
  `replays/` for games you kept. Each one has a short note in it saying what
  goes there, so you can add a deck by dropping the file in as well as by using
  the deck builder.
- **You can add art for cards the game does not have a picture of.** Put a
  `.webp` named after the card — `ST9-007.webp` — in `card_images/` and the game
  uses it instead of its own. That covers cards printed since the version you
  downloaded, and your own scans.
- **Installing a newer version keeps your decks, profiles and replays.** Install
  it over the old one; only the game itself is replaced.
- On a Mac, the installer clears the download flag on the copy it installs, so
  the game opens normally instead of being refused as an unidentified developer.
- **Fixed: opening the deck builder from the title screen bounced straight
  back to the title screen.** The Replays button did the same thing. Both now
  open and stay open, and going back to Play brings the menu straight back.
- **The Mac download now opens in its own window instead of launching Chrome.**
  Earlier builds borrowed a Chrome window when they had no window of their own,
  which is why a browser appeared alongside the game. If a window still cannot
  be drawn the game says so and falls back to a browser window rather than
  failing to start.

## 0.2.34

- **One command now builds the game into something you can hand to someone.**
  `python build_release.py` produces a single file for the computer it runs on
  — a `.exe` on Windows, a binary on macOS — zipped with a short read-me that
  says how to open it past the "unknown publisher" and "unidentified developer"
  prompts. The person you send it to needs no Python and no card art; they
  double-click it and the game opens.
- The build carries only what playing needs. The training half of the project
  is left out, and `--no-images` makes a 11 MB build for a slow connection,
  where cards show their text instead of their art.

## 0.2.33

- **You can now play someone directly, with no host and no open port.** The new
  **Play someone directly** entry on the title screen connects the two
  computers straight to each other. One of you starts and gets a code, sends it
  over however you already chat, and pastes the reply that comes back; the game
  begins on its own. Nobody has to run a server for the other, nothing is
  forwarded on a router, and neither of you learns the other's address.
- Both computers run the game and swap only the moves, so it stays in step over
  a bad connection and costs almost nothing to keep open.
- **It will tell you if the two games ever disagree.** If you are on different
  versions, or one of you has an edited card, the match stops and says which
  decision went wrong instead of leaving you playing two slightly different
  games. Mismatched versions are caught before a card is dealt.
- **The dialog is explicit that a direct game is not private from the person you
  are playing.** Their computer runs the rules too, so it holds your hand and
  only their screen hides it. Play it with people you trust; for a game that
  keeps your hand secret from your opponent, **Play someone on your network** is
  still the mode that does that.
- **Closing the dialog on a game that never connected takes you back to the
  title screen**, so an opponent who never pasted their code costs you nothing
  but the time — you are back at the menu and can start something else. Closing
  it on a game that *is* under way just gets you back to the board, which is
  still there.
- The connection panel now gets out of the way by itself once the game starts,
  and a code that did not survive the trip says so — "that code did not arrive
  in one piece" rather than an error about the network, which is what a
  half-copied code used to look like. Pasting one tells you straight away
  whether it is complete, and **Copy** takes the whole thing even where the
  clipboard is not available.
- Closing the tab now tells the other player, instead of leaving them waiting on
  a seat that will never move again.
- **There is a profile now, and it is yours alone.** The chip in the corner of
  the title screen keeps the games you have played, how each of your decks has
  done, and a level that climbs as you play. It is one encrypted file on your
  own machine — no account, nothing sent anywhere, no port opened.
- Give the profile a passphrase and nothing but that passphrase opens it. Leave
  it blank and the file is still encrypted, by a key kept beside it — which
  keeps it private in a backup or a synced folder, though not from someone
  sitting at your computer. The chooser says which of the two a profile is
  rather than calling both of them safe.
- **Playing a person is 1 XP, and winning is 3 more.** A bot pays nothing, and
  two bots playing each other pay nothing to anybody — so a level is a record of
  games against people. Bot games are still listed, and each row says why it
  paid what it did. A level costs four times the level you are on, so the first
  one is a single won game.
- Every deck you play gets its own line: games, won, lost, drawn and a win rate.
- **The last thirty games are kept, and each one can still be watched.** Star a
  game and it is kept for good and stops counting against the thirty. A game
  that drops off the list takes its replay with it; deleting one yourself does
  the same, and leaves the win itself standing — deleting the log is not a way
  to un-play a game.
- **Pick a profile picture:** any card's art, or a picture of your own, shrunk
  in the browser so the profile stays small.
- A game is recorded the moment it ends, on its own thread — so a game you
  walked away from still counts, and the guided first game, which is a lesson
  rather than a game, is not recorded at all.

## 0.2.32

- **The guided first game is now a set piece, and it always plays out.** The
  tutorial deals both decks from the top instead of shuffling them, so the hand
  it talks about is the hand you get: a Cookie to open with, two Items to spend
  as support, a 【Blocker】 for your second slot, a Trap, and a cheap attacker.
  No more being told to play a Cookie you were never dealt.
- Take the free mulligan and you get a second hand with all the same lessons in
  it, rather than whatever was next in the box.
- **The opponent is a new pilot, "tutorial", that plays to teach.** It lays at
  most two supports and swings once a turn, so it can never open on a
  three-damage Cookie and grind you out while you are still reading — the old
  practice bot had the game by turn 9 against someone following along. It also
  goes for your *sturdiest* Cookie, which is what keeps your 【Blocker】 free to
  step in front of the attack.
- It also throws differently each round of the toss instead of repeating one
  throw, so rock no longer ties over and over, and it hands you the first turn
  when it wins — the course is written from the opening player's seat.
- The step that waits for your first attack now says what is missing ("place
  another card as support") instead of just "waiting".
- The block tip no longer says blocking rests your Cookie. Most 【Blocker】s pay
  energy — including the one the tutorial deals you — and only a few rest
  themselves; it now says so.
- A game that ends mid-tutorial no longer dims the result behind the tutorial's
  own shading.

## 0.2.31

- **The header is down to the buttons you actually press mid-game.** Speed,
  reveal, flip opponent's mat, sound, confirm and hold have moved out of the
  top bar into a **Settings** window, opened with the gear next to *Learn*.
  Every setting behaves exactly as it did and is still remembered per browser —
  there is just far less furniture above the board.

## 0.2.30

- **Windows support.** The game starts, plays and saves on Windows the same way
  it does on macOS and Linux, from the same checkout.
- It no longer quits on startup there, the board no longer comes up blank
  (Windows was telling the browser the game's own code was plain text), and
  card names, decklists and replays are no longer mangled by the system's
  default text encoding.
- A deck or replay saved on Windows is now the same file as one saved on a Mac,
  so the two can be swapped freely.
- A copy installed somewhere it cannot write to — Program Files, say — now
  keeps your replays and saved decks in your user folder instead of failing to
  save them.
- If the port is already in use, the message names the process holding it and
  the Windows command to stop it.
- Long training or deck-generation runs whose output is sent to a log file no
  longer stop partway through with an encoding error the first time a card name
  is printed.

## 0.2.29

- **The game now opens in its own window** instead of a browser tab — no
  address bar, no tabs, and closing the window quits the game. Nothing about
  playing it changed otherwise.
- It uses a native window if `pywebview` is installed (`pip install
  pywebview`), otherwise a chromeless Chrome/Edge/Brave window, otherwise a
  browser tab exactly like before.
- `--browser` asks for the old tab on purpose; `--window` insists on a real
  window and tells you what to install if it cannot open one.

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
