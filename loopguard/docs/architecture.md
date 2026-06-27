# Architecture

Events enter `LoopGuard.observe()`, are stored per run in a bounded window, and are checked by budget, exact, ping-pong, and semantic detectors. Semantic matching uses local hashing vectors only. Tripped decisions are handled according to config: raise, warn, or pause with Rich UI.
