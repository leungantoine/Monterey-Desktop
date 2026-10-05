"""Monterey Unicode event pacing, verified with Safari and native fixtures."""

# Retain small event chunks and check focus/pause between chunks. These delays
# allow key transitions to enter the app's event queue without per-character
# waits; replacement verifies the full value before reporting success.
TEXT_KEY_HOLD_SECONDS = 0.005
TEXT_CHUNK_INTERVAL_SECONDS = 0.01
