# Notification Consumer Lag Rising

**Alert:** `notifications-consumer-lag-rising` -- `queue.consumer_lag` climbing.

## Symptom

`queue.consumer_lag` for the notifications service rises steadily. This metric is the
gap between what's been produced to the topic and what's been consumed, so a rising
value means consumption has slowed or stopped relative to production -- it does not
by itself tell you which side is the problem.

## Common causes

1. **Consumer stalled or paused.** The consumer process is alive but not pulling
   messages (crashed poll loop, deliberately paused, stuck on a poison message).
   Producer-side metrics stay completely normal in this case.
2. **Consumer is slow, not stopped.** Each message is taking longer to process
   (a downstream call regressed), so lag grows more slowly and unevenly rather than
   climbing as a clean, steady ramp.
3. **Producer surge.** Upstream production rate increased faster than steady-state
   consumer throughput can keep up with. Lag rises because the numerator grew, not
   because the consumer got worse.

## How to tell these apart

- Check whether the producer side (checkout's publish activity) is steady or has
  changed -- `query_metrics` / `find_traces` on checkout around the same window. A
  steady producer with rising lag points at the consumer; a producer spike explains
  the lag on its own.
- `search_logs` on notifications for consumption activity or errors -- an actively
  stalled consumer often has a gap in its own log output entirely, versus a slow
  consumer which keeps logging but at a reduced rate.
- Look at the shape of the lag series (`query_metrics` with `bucket_seconds`): a clean
  linear ramp from zero consumption looks different from a bumpy, gradually-worsening
  ramp from a slow consumer.

## Remediation

If the consumer is simply paused or stalled, resuming/restarting it is the direct
fix and should drain the backlog once it's consuming again. Restarting the message
broker itself is not an appropriate response to a single consumer group falling
behind -- it doesn't address a stalled consumer and risks disrupting every other
producer/consumer on the broker.
