// What a reply's references link to and how a URL is labelled (#393). Pure:
// strings in, strings out; entries.js does the DOM. The shapes match
// aegis/refs.py.

const OWNER = "[A-Za-z0-9-]{1,39}";
const NAME = "[A-Za-z0-9._-]{1,100}";
const REPO_N = new RegExp(`^(${OWNER})/(${NAME})#(\\d+)$`);
const GITHUB = new RegExp(`^github\\.com/${OWNER}/${NAME}(?:/\\S*)?$`);
const HOSTS = { "github.com": "github", "pypi.org": "pypi", "arxiv.org": "arxiv", "npmjs.com": "npm" };

// A repo whatever the disk holds: an issue ref or a github.com path.
export function sureRepo(text) {
  return REPO_N.test(text) || GITHUB.test(text);
}

// An issue number that is a pull request: GitHub redirects /issues/n to it.
export function repoUrl(text) {
  const m = REPO_N.exec(text);
  if (m) return `https://github.com/${m[1]}/${m[2]}/issues/${m[3]}`;
  return GITHUB.test(text) ? `https://${text}` : `https://github.com/${text}`;
}

const host = (u) => u.hostname.replace(/^www\./, "");

export function hostIcon(u) {
  return HOSTS[host(u)] || "globe";
}

// A bare URL's label: owner/repo#n for a GitHub issue or pull request, a
// package's name, an arXiv id, else the host and its first segment.
export function shortLabel(u) {
  const h = host(u);
  const seg = u.pathname.split("/").filter(Boolean);
  if (h === "github.com" && seg.length >= 2) {
    const r = `${seg[0]}/${seg[1]}`;
    if ((seg[2] === "pull" || seg[2] === "issues") && seg[3]) return `${r}#${seg[3]}`;
    if ((seg[2] === "blob" || seg[2] === "tree") && seg.length > 4) return `${r}: ${seg.slice(4).join("/")}`;
    if (seg[2] === "commit" && seg[3]) return `${r}@${seg[3].slice(0, 7)}`;
    return seg.length > 2 ? `${r}/${seg.slice(2).join("/")}` : r;
  }
  if (h === "pypi.org" && seg[0] === "project" && seg[1]) return seg[1];
  if (h === "arxiv.org" && seg[0] === "abs" && seg[1]) return `arXiv:${seg[1]}`;
  if (h === "npmjs.com" && seg[0] === "package" && seg[1]) return seg.slice(1).join("/");
  return h + (seg.length ? `/${seg[0]}${seg.length > 1 ? "/…" : ""}` : "");
}
