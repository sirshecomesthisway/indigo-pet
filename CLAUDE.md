# squid-pet

## Signal task completion

When you (Claude) finish a whole task or request in this repo -- not just one
turn, but the actual thing the user asked for -- run:

```
python3 scripts/squid_task_complete.py
```

This tells squid-pet's watcher to celebrate for real, instead of guessing from
elapsed silence (which can't tell "mid-task pause" from "actually done" --
see watcher.py's `claude_task_marked_complete_recently`). Don't run it after
routine intermediate turns, only when the task itself is done.
