"""Automated strategy simulator.

Distinct from the journal's ``backtest`` session mode, which means a *manually*
replayed ATAS session exported to XLSX. This package simulates a rule set over
historical tick data and emits trades in the same shape as real ones, so
``metrics`` and ``charts_data`` score and render them unchanged.
"""
