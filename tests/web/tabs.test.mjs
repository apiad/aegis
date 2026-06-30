// Dependency-free node unit test for tab reconciliation.
// Run: node tests/web/tabs.test.mjs
import assert from "node:assert";
import { reconcileTabs } from "../../src/aegis/web/static/js/tabs.js";

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

console.log("tabs.test.mjs: all assertions passed");
