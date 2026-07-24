"""Economy-aware loot-filter block generation.

Pure text transformation: snapshot rows in -> a top-of-filter "economy
block" of Show rules out. Nothing here touches the game client; in-game
filter reloading is always a manual player action. See filtergen/economy.py
and tools/filter_update.py.
"""
