// Dependency-free node unit test for tab reconciliation.
// Run: node tests/web/tabs.test.mjs
import assert from "node:assert";
import {
  reconcileTabs, cycleHandle, gotoHandle, swipeDirection,
} from "../../src/aegis/web/static/js/tabs.js";

const sess = (handle, agent_slug = "opus") => ({ handle, agent_slug });

// new session → added
{
  const { added, removed } = reconcileTabs(["a"], [sess("a"), sess("b")]);
  assert.deepEqual(added.map((s) => s.handle), ["b"]);
  assert.deepEqual(removed, []);
}

// vanished session → removed
{
  const { added, removed } = reconcileTabs(["a", "b"], [sess("a")]);
  assert.deepEqual(added, []);
  assert.deepEqual(removed, ["b"]);
}

// unchanged → neither
{
  const { added, removed } = reconcileTabs(["a"], [sess("a")]);
  assert.deepEqual(added, []);
  assert.deepEqual(removed, []);
}

// empty existing + two sessions → both added (order preserved)
{
  const { added, removed } = reconcileTabs([], [sess("x"), sess("y")]);
  assert.deepEqual(added.map((s) => s.handle), ["x", "y"]);
  assert.deepEqual(removed, []);
}

// empty sessions + two existing → both removed
{
  const { added, removed } = reconcileTabs(["x", "y"], []);
  assert.deepEqual(added, []);
  assert.deepEqual(removed, ["x", "y"]);
}

// cycleHandle — next/prev with wraparound
assert.equal(cycleHandle(["a", "b", "c"], "a", 1), "b", "next");
assert.equal(cycleHandle(["a", "b", "c"], "c", 1), "a", "next wraps");
assert.equal(cycleHandle(["a", "b", "c"], "a", -1), "c", "prev wraps");
assert.equal(cycleHandle([], "x", 1), null, "empty → null");
assert.equal(cycleHandle(["a", "b"], "missing", 1), "a", "unknown → first");

// gotoHandle — 1-based index
assert.equal(gotoHandle(["a", "b", "c"], 2), "b", "goto 2");
assert.equal(gotoHandle(["a", "b"], 5), null, "goto out of range");

// swipeDirection — horizontal-dominant gestures pick a direction
assert.equal(swipeDirection(-100, 5), 1, "swipe left → next");
assert.equal(swipeDirection(100, -5), -1, "swipe right → prev");
assert.equal(swipeDirection(-20, 0), 0, "too short → ignore");
assert.equal(swipeDirection(-100, -120), 0, "vertical-dominant → ignore");

console.log("tabs.test.mjs: all assertions passed");
