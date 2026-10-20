# frankentanker changelog

## 1.0.2 (2026-10-18)

- **Fix: the 12 call sites that trigger an MCDU repaint
  (`asyncio.create_task(self.repaint_all_mcdus())`) never kept a
  reference to the resulting Task** - if `repaint_all_mcdus()` raised an
  exception, it would be silently dropped until (and unless) the Task
  was garbage-collected. Routed through a new `_track_task()` helper
  instead, which keeps the Task alive in `self._fire_and_forget_tasks`
  and logs any exception via a done-callback.

## 1.0.1 (2026-10-03)

- The `name=`/`clientName=` messages sent to PSX now include the addon's
  version number, matching every other addon - makes it easier to tell
  what's actually running from the PSX client list.
