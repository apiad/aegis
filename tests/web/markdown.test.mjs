// Dependency-free node unit test for the minimal markdown renderer.
// Run: node tests/web/markdown.test.mjs
import assert from "node:assert";
import { renderMarkdown } from "../../src/aegis/web/static/js/markdown.js";

const eq = (got, want, msg) => assert.equal(got, want, `${msg}\n  got:  ${got}\n  want: ${want}`);

eq(renderMarkdown("**bold**"), "<strong>bold</strong>", "bold");
eq(renderMarkdown("*it*"), "<em>it</em>", "italic *");
eq(renderMarkdown("_it_"), "<em>it</em>", "italic _");
eq(renderMarkdown("a `code` b"), "a <code>code</code> b", "inline code");

// HTML is escaped (no injection)
eq(renderMarkdown("<script>x</script>"), "&lt;script&gt;x&lt;/script&gt;", "escape html");
eq(renderMarkdown("**a<b**"), "<strong>a&lt;b</strong>", "escape inside bold");

// safe link; javascript: scheme is dropped to plain text
eq(renderMarkdown("[t](https://e.com)"),
   '<a href="https://e.com" target="_blank" rel="noopener">t</a>', "link");
eq(renderMarkdown("[t](javascript:alert)"), "t", "javascript link blocked");

// headers + lists
eq(renderMarkdown("# Hi"), "<h1>Hi</h1>", "h1");
eq(renderMarkdown("### Hey"), "<h3>Hey</h3>", "h3");
eq(renderMarkdown("- a\n- b"), "<ul>\n<li>a</li>\n<li>b</li>\n</ul>", "list");

// fenced code block — inner is NOT markdown-processed, and is escaped
eq(renderMarkdown("```\n**x** <y>\n```"),
   "<pre><code>**x** &lt;y&gt;</code></pre>", "fenced code");

// plain newlines are preserved verbatim (transcript CSS is pre-wrap)
eq(renderMarkdown("line1\nline2"), "line1\nline2", "newline preserved");

// empty
eq(renderMarkdown(""), "", "empty");

console.log("markdown.test.mjs: all assertions passed");
