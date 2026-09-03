"""Hermes-independent core of the PLLLA bridge.

Everything in this package runs without Hermes on the path so it can be unit
tested with plain pytest; ``adapter.py`` (one level up) is the only module
that imports Hermes.
"""
