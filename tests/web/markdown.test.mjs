// Dependency-free node unit test for the minimal markdown renderer.
// Run: node tests/web/markdown.test.mjs
import assert from "node:assert";
import { renderMarkdown } from "../../src/aegis/web/static/js/markdown.js";

const eq = (got, want, msg) => assert.equal(got, want, `${msg}\n  got:  ${got}\n  want: ${want}`);

// inline, wrapped in a paragraph block
eq(renderMarkdown("**bold**"), "<p><strong>bold</strong></p>", "bold");
eq(renderMarkdown("*it*"), "<p><em>it</em></p>", "italic *");
eq(renderMarkdown("_it_"), "<p><em>it</em></p>", "italic _");
eq(renderMarkdown("a `code` b"), "<p>a <code>code</code> b</p>", "inline code");

// HTML is escaped (no injection)
eq(renderMarkdown("<script>x</script>"),
   "<p>&lt;script&gt;x&lt;/script&gt;</p>", "escape html");
eq(renderMarkdown("**a<b**"), "<p><strong>a&lt;b</strong></p>", "escape in bold");

// safe link; javascript: scheme is dropped to plain text
eq(renderMarkdown("[t](https://e.com)"),
   '<p><a href="https://e.com" target="_blank" rel="noopener">t</a></p>', "link");
eq(renderMarkdown("[t](javascript:alert)"), "<p>t</p>", "js link blocked");

// headers (no paragraph wrap)
eq(renderMarkdown("# Hi"), "<h1>Hi</h1>", "h1");
eq(renderMarkdown("### Hey"), "<h3>Hey</h3>", "h3");

// list — tight, no stray newlines between items
eq(renderMarkdown("- a\n- b"), "<ul><li>a</li><li>b</li></ul>", "list");

// fenced code block — inner not markdown-processed, and escaped
eq(renderMarkdown("```\n**x** <y>\n```"),
   "<pre><code>**x** &lt;y&gt;</code></pre>", "fenced code");

// soft line break inside a paragraph collapses to a space
eq(renderMarkdown("line1\nline2"), "<p>line1 line2</p>", "soft break");

// blank line separates paragraphs
eq(renderMarkdown("a\n\nb"), "<p>a</p><p>b</p>", "paragraph break");

// list followed by prose
eq(renderMarkdown("- x\n\ntext"), "<ul><li>x</li></ul><p>text</p>", "list+para");

// empty
eq(renderMarkdown(""), "", "empty");

console.log("markdown.test.mjs: all assertions passed");
