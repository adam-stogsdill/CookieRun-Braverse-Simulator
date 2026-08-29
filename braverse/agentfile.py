"""Where trained agents live on disk.

Agents are to `agents/` what decklists are to `decks/`: a folder beside the
game that a player can drop a file into, and that the pilot menu reads at
startup. Keeping them out of the top level is cosmetic; keeping *both* places
readable is not — a `.pt` sitting loose beside the executable has always been
offered as an opponent, and a command line naming one by bare name predates the
folder existing.

Stdlib only, and no torch: `selfplay.py` resolves a checkpoint name while
parsing arguments, long before it decides whether to pay the second of import
time that `braverse.rl` costs.
"""

from __future__ import annotations

from pathlib import Path

#: Folder name, beside the script or beside a frozen binary.
AGENT_DIR = "agents"

#: What a checkpoint file is called. The sidecars `rl.Trainer.save` writes
#: (`.json`, `.summary.json`) travel with it.
AGENT_GLOB = "*.pt"


def find_checkpoint(name: str | Path, *, base: Path | None = None) -> Path:
    """Resolve a checkpoint name to a real file.

    A path that exists is taken as given. A bare name is looked up in
    `agents/` as well, so `--checkpoint rl_agent.pt` keeps working now that
    the file has moved into the folder. Raises rather than returning a path
    that is not there, because the caller's next move is to load it and a
    missing-file error naming the folders searched is the useful one.
    """
    path = Path(name)
    if path.exists():
        return path

    root = Path.cwd() if base is None else Path(base)
    if path.parent in (Path(""), Path(".")):
        candidate = root / AGENT_DIR / path.name
        if candidate.exists():
            return candidate

    raise FileNotFoundError(
        f"no checkpoint {str(name)!r}: looked for it as given and in "
        f"{root / AGENT_DIR}")
