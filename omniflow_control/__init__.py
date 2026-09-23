"""Omni Flow account control centre.

One local server that holds every Google account the team uses for Omni Flow,
tracks each account's monthly credits, queues video prompts and runs them
against the account an operator has activated. Account selection is always a
human decision: when the active account is out of credits the queue pauses
and waits for an operator to activate another one.
"""

__version__ = "0.2.0"
