// How long a plan has taken and how long it has left. The fold accrues time
// only at a plan record or a turn boundary (transcript/plan_clock.py), so the
// time running since `plan_clock.at` is added here, against the browser's clock.

export function dur(s) {
  const m = Math.floor(s / 60);
  if (m < 1) return "<1m";
  if (m < 60) return `${m}m`;
  return `${Math.floor(m / 60)}h${m % 60}m`;
}

export function planTimes(m, now = Date.now() / 1000) {
  const c = m.plan_clock;
  if (!c) return null;
  const plan = m.plan || [];
  const run = Math.max(0, now - c.at);
  const working = c.running === "work" ? run : 0;
  const work = c.work_s + working;
  const idle = c.idle_s + (c.running === "idle" ? run : 0);
  const items = plan.map((i) => (i.work_s || 0) + (i.state === "doing" ? working : 0));
  const done = plan.filter((i) => i.state === "done").length;
  const doing = plan.findIndex((i) => i.state === "doing");
  const left = done && done < plan.length ? Math.max(0, (work / done) * (plan.length - done) - (doing < 0 ? 0 : items[doing])) : null;
  return { work, idle, items, left };
}
