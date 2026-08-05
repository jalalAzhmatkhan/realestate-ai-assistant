"""The five MVP tools, each a narrow single-purpose function with an explicit schema.

All five are registered on the agent up front (``app/agent/orchestrator.py``); the model
chooses among them autonomously via native function calling on every turn. Nothing in
this package — or anywhere else — inspects the user's message to decide which tool runs.
"""
