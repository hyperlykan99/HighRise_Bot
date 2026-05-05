"""
bot.py
------
Workflow entry-point shim.

The workflow command is: python3 bot.py
This file simply delegates to main.py so the workflow config needs no changes.

The real bot logic is in main.py.
"""

from main import run

run()
