# Mobile slice 4: the cookie lock and the manifest, implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** aegis installs from Chrome's menu on Android and opens signed in, with the token held only as an HttpOnly cookie and no proxy password in front.

**Architecture:** `GET /?token=` checks the token, sets an HttpOnly, `SameSite=Strict` cookie named after the state root, and redirects to `/`; `POST /login` does the same from a pasted token. The websocket accepts that cookie in place of `hello.token`. The client stops touching the token and shows a login field when the socket says 4401. `/manifest.webmanifest` names the server, and three PNG icons drawn by a script ship under `client/icons/`. No service worker.

**Tech Stack:** Starlette (`RedirectResponse`, `JSONResponse`, `Response.set_cookie`); plain ES modules; Pillow only in the icon script (`uv run --with pillow`), never a dependency.

**Spec:** `docs/superpowers/specs/2026-10-08-mobile-and-pwa-design.md`, section 3, "The installable app and the lock".

## Global Constraints

- The cookie is HttpOnly, `SameSite=Strict`, `Path=/`, `Max-Age` one year (31,536,000 s), and `Secure` when the request's `Host` is an `--origin` whose scheme is https.
- The cookie's name is `aegis_` plus the first 8 hex digits of the SHA-256 of the state root's path, so two servers on one host keep separate cookies.
- `hello.token` still works (tests, `window.serves`, the bench).
- No service worker in this slice.
- No new runtime dependency.
- The manifest: `display: standalone`, `start_url: /`, `scope: /`, name `aegis · <hostname>`, short name `aegis`, `background_color: #11100e`, `theme_color: #0b0a09`, icons 192 px, 512 px and a 512 px maskable one.
- Comparing a token always goes through `hmac.compare_digest`.

## Review Focus

- A page opened from a link in another app (Telegram, an email): the navigation is cross-site, so a `Strict` cookie does not ride on it, but the page's own socket is same-site and does carry it. The page must sign in without the login field. Pinned in Task 1's browser test, which navigates from a `data:` page. If Chrome withholds the cookie there, `samesite="lax"` is the fallback: it still refuses the cookie to cross-site subrequests, and the socket's origin check is the guard that matters.
- A token file rotated while a phone holds the old cookie: the socket gets 4401 and the login field shows, not a reconnect loop. Pinned in Task 1.
- Two servers on one host (zion runs 8742, tests run more): signing in to one must not sign out the other. Pinned in Task 1.
- A login posted with a wrong token: 401, no cookie, the field says so, and nothing is logged with the token in it. Pinned in Task 1.
- A proxy origin over plain http (a LAN `--origin http://box:8742`): the cookie must not be `Secure`, or the browser drops it. Pinned in Task 1.

---

### Task 1: The cookie, the login, and a client that never holds the token

**Files:**
- Modify: `src/aegis/web.py` (module docstring; new `cookie_name`; `build_web`: `index`, new `login`, `ws`, routes)
- Modify: `src/aegis/client/js/protocol.js` (`Connection` constructor and `hello`)
- Modify: `src/aegis/client/js/app.js` (lines 20-28 token block; `new Connection`; `onState` for `unauthorized`; the `if (!token) … else { … }` wrapper at lines 116-157; `render`'s guard at line 198; the login form)
- Modify: `src/aegis/client/index.html` (`v-boot`: the login form)
- Modify: `src/aegis/client/css/base.css` (`.login`)
- Modify: `src/aegis/files.py` (docstring), `DESIGN.md` ("Agent text is untrusted", "A sent file's URL is its key")
- Modify: `tests/test_browser.py` (`test_a_wrong_token_says_so`)
- Test: `tests/test_web.py`, `tests/test_browser.py` (append)

**Interfaces:**
- Produces: `cookie_name(state_root: Path) -> str` in `aegis.web`; `POST /login` taking JSON `{"token": str}` and answering 204 with the cookie or 401; `GET /?token=<t>` answering 303 to `/` with the cookie, or 303 to `/?refused=1` without one. `new Connection(url, { onState })` (no token argument).

- [ ] **Step 1: Write the failing server tests**

Add `cookie_name` to the `from aegis.web import ...` line at the top of `tests/test_web.py`, then append:

```python
def test_the_token_url_sets_a_cookie_and_moves_the_token_out(project, fake_claude):
    with client_for(project, fake_claude) as c:
        r = c.get(f"/?token={TOKEN}", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/"
        cookie = r.headers["set-cookie"]
        name = cookie_name(project / ".aegis" / "state")
        assert cookie.startswith(f"{name}={TOKEN};")
        for attr in ("HttpOnly", "SameSite=strict", "Path=/", "Max-Age=31536000"):
            assert attr.lower() in cookie.lower(), attr
        assert "secure" not in cookie.lower(), "plain http on loopback"
        bad = c.get("/?token=wrong", follow_redirects=False)
        assert bad.headers["location"] == "/?refused=1" and "set-cookie" not in bad.headers


def test_a_socket_signs_in_with_the_cookie_alone(project, fake_claude):
    name = cookie_name(project / ".aegis" / "state")
    with client_for(project, fake_claude) as c:
        with c.websocket_connect(
            "/ws", headers={**ORIGIN, "cookie": f"{name}={TOKEN}"}
        ) as ws:
            ws.send_json({"t": "hello", "proto": PROTO})
            assert ws.receive_json()["t"] == "welcome"
        with c.websocket_connect(
            "/ws", headers={**ORIGIN, "cookie": f"{name}=wrong"}
        ) as ws:
            ws.send_json({"t": "hello", "proto": PROTO})
            with pytest.raises(WebSocketDisconnect) as e:
                ws.receive_json()
            assert e.value.code == 4401
        with c.websocket_connect(
            "/ws", headers={**ORIGIN, "cookie": f"aegis_00000000={TOKEN}"}
        ) as ws:
            ws.send_json({"t": "hello", "proto": PROTO})
            with pytest.raises(WebSocketDisconnect) as e:
                ws.receive_json()
            assert e.value.code == 4401, "another server's cookie name"


def test_login_sets_the_cookie_for_a_pasted_token(project, fake_claude):
    with client_for(project, fake_claude) as c:
        ok = c.post("/login", json={"token": TOKEN})
        assert ok.status_code == 204 and "httponly" in ok.headers["set-cookie"].lower()
        for body in ({"token": "wrong"}, {"token": 3}, {}):
            r = c.post("/login", json=body)
            assert r.status_code == 401 and "set-cookie" not in r.headers


def test_two_state_roots_have_two_cookie_names(tmp_path):
    a, b = cookie_name(tmp_path / "a"), cookie_name(tmp_path / "b")
    assert a != b and a.startswith("aegis_") and len(a) == len("aegis_") + 8


def test_the_cookie_is_secure_only_behind_an_https_origin(project, fake_claude):
    app = App(make_roots(project, None), claude_bin=fake_claude)
    web = build_web(
        app, TOKEN, {"testserver"}, ["https://dev.example", "http://box.lan:8742"]
    )
    with TestClient(web, base_url="https://dev.example") as c:
        r = c.get(f"/?token={TOKEN}", follow_redirects=False)
        assert "secure" in r.headers["set-cookie"].lower()
    with TestClient(web, base_url="http://box.lan:8742") as c:
        r = c.get(f"/?token={TOKEN}", follow_redirects=False)
        assert "secure" not in r.headers["set-cookie"].lower()
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest -q tests/test_web.py -k "cookie or login or secure"`
Expected: FAIL with `ImportError: cannot import name 'cookie_name'`.

- [ ] **Step 3: Implement the server**

`src/aegis/web.py`. Imports: add `import hashlib`, and change the Starlette imports to

```python
from starlette.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
```

Constants and the name, after `LOOPBACK`:

```python
COOKIE_MAX_AGE_S = 365 * 24 * 3600


def cookie_name(state_root: Path) -> str:
    """The session cookie's name. Cookies ignore ports, so two servers on one
    host would overwrite each other's under one name; this one is per state
    root, as the token is."""
    return "aegis_" + hashlib.sha256(str(state_root).encode()).hexdigest()[:8]
```

Inside `build_web`, after `public = ...`:

```python
    cookie = cookie_name(app.roots.state_root)

    def valid(given: object) -> bool:
        return isinstance(given, str) and hmac.compare_digest(given, token)

    def signed_in(response: Response, request) -> Response:
        """``response`` carrying the cookie: HttpOnly, so no script on the page
        can read the token; Secure behind an https origin."""
        origin = public.get(request.headers.get("host", ""), "")
        response.set_cookie(
            cookie,
            token,
            max_age=COOKIE_MAX_AGE_S,
            path="/",
            httponly=True,
            samesite="strict",
            secure=origin.startswith("https://"),
        )
        return response
```

Replace `index`:

```python
    async def index(request):
        """The page. ``?token=`` from the URL `aegis serve` printed becomes the
        cookie, and the address bar loses it; a wrong one says so."""
        given = request.query_params.get("token")
        if given is not None:
            if valid(given):
                return signed_in(RedirectResponse("/", status_code=303), request)
            return RedirectResponse("/?refused=1", status_code=303)
        return FileResponse(
            CLIENT_DIR / "index.html", headers={"Cache-Control": "no-cache"}
        )

    async def login(request):
        """A pasted token, for a browser that never opened the printed URL."""
        try:
            body = await request.json()
        except ValueError:
            body = None
        given = body.get("token") if isinstance(body, dict) else None
        if not valid(given):
            return JSONResponse({"error": "bad_token"}, status_code=401)
        return signed_in(Response(status_code=204), request)
```

In `ws`, replace the token check:

```python
        given = hello.get("token") if isinstance(hello, dict) else None
        if not isinstance(given, str):
            given = websocket.cookies.get(cookie)
        if hello.get("t") != "hello" or not valid(given):
            await websocket.close(code=BAD_TOKEN)
            return
```

Routes: add `Route("/login", login, methods=["POST"]),` after `Route("/", index),`.

Module docstring, replace the first paragraph's last sentence with: `…and must prove it holds the server's token within 5 s: in its ``hello``, or as the HttpOnly cookie ``GET /?token=`` and ``POST /login`` set, so no script on the page ever holds the token.`

- [ ] **Step 4: Run the server tests**

Run: `uv run pytest -q tests/test_web.py tests/test_window.py tests/test_detach.py`
Expected: PASS.

- [ ] **Step 5: Write the failing browser tests**

In `tests/test_browser.py`, replace `test_a_wrong_token_says_so` with:

```python
def test_a_wrong_token_says_so_and_offers_the_login(server, page):
    page.goto(re.sub(r"token=[^&]+", "token=wrong", server.url))
    page.wait_for_selector("#login", state="visible")
    assert "refused" in page.inner_text("#login-error")
    assert "token=" not in page.url


def test_pasting_the_token_signs_this_browser_in_and_it_stays(server, browser):
    errors: list = []
    page = new_page(browser, errors)
    bare = server.url.split("?")[0]
    page.goto(bare)
    page.wait_for_selector("#login", state="visible")
    page.fill("#login-token", "wrong")
    page.press("#login-token", "Enter")
    page.wait_for_function("document.getElementById('login-error').textContent !== ''")
    token = server.url.split("token=")[1]
    page.fill("#login-token", token)
    page.press("#login-token", "Enter")
    page.wait_for_selector("#a2[data-view=fleet]")
    page.reload()
    page.wait_for_selector("#a2[data-view=fleet]")
    assert page.evaluate("document.cookie") == "", "HttpOnly: no script sees it"
    # Opened from another site, as a link in a chat app would.
    page.goto(f"data:text/html,<a id=go href='{bare}'>aegis</a>")
    page.click("#go")
    page.wait_for_selector("#a2[data-view=fleet]")
    assert errors == []


def test_two_servers_on_one_host_keep_their_own_sign_in(
    server, browser, tmp_path, fake_claude
):
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / ".aegis.yaml").write_text(CONFIG)
    other = Server(tmp_path / "other", fake_claude).start()
    try:
        errors: list = []
        page = new_page(browser, errors)
        page.goto(server.url)
        page.wait_for_selector("#a2[data-view=fleet]")
        page.goto(other.url)
        page.wait_for_selector("#a2[data-view=fleet]")
        page.goto(server.url.split("?")[0])
        page.wait_for_selector("#a2[data-view=fleet]")
        assert errors == []
    finally:
        other.stop()


def test_a_rotated_token_shows_the_login_instead_of_retrying(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    server.stop()
    (server.root / ".aegis" / "state" / "token").unlink()
    server.start()
    page.wait_for_selector("#login", state="visible", timeout=15000)
```

(The rotated-token test reloads nothing: the page reconnects on its own, gets 4401, and must stop.)

- [ ] **Step 6: Run them and watch them fail**

Run: `uv run pytest -q -m browser -k "wrong_token or pasting_the_token or two_servers or rotated_token"`
Expected: FAIL: `#login` does not exist.

- [ ] **Step 7: Implement the client**

`index.html`, the boot view:

```html
  <main class="view v-boot">
    <div class="boot">
      <div class="notice" id="boot-text">Connecting…</div>
      <form class="login" id="login" hidden>
        <label for="login-token">Paste the token <code>aegis serve</code> printed. This browser stays signed in.</label>
        <input id="login-token" type="password" autocomplete="current-password" spellcheck="false" required>
        <button class="btn primary" type="submit">Sign in</button>
        <p class="err-text" id="login-error"></p>
      </form>
    </div>
  </main>
```

`base.css`, after `#a2 .notice{...}`:

```css
#a2 .boot{display:grid;gap:14px;justify-items:center;padding:16px}
#a2 .login{display:grid;gap:10px;width:min(360px,100%)}
#a2 .login[hidden]{display:none}
#a2 .login label{color:var(--muted);font-size:13px;line-height:1.45}
#a2 .login input{background:var(--surface);border:1px solid var(--rule);border-radius:var(--r);padding:10px 12px;font-size:15px;color:var(--strong)}
```

`protocol.js`: the constructor becomes `constructor(url, { onState = () => {} } = {})` without `this.token`, and `onopen` sends `{ t: "hello", proto: PROTO }`; the browser's cookie rides on the socket's handshake. Change the header comment's token sentence accordingly: `The socket signs in with the HttpOnly cookie the server set; the page never holds the token.`

`app.js`: delete lines 20-28 (the token block and `const token`). Change the connection to

```js
const conn = new Connection(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`, {
```

and the `unauthorized` line in `onState` to `if (state === "unauthorized") showLogin();`. Remove the `if (!token) show(...); else {` line and its closing `}` after `conn.connect();`, keeping the body. In `render`, change `if (!token || !booted) return;` to `if (!booted) return;`. Add, after `show`:

```js
// -- signing in: the socket said 4401, so this browser has no valid cookie -----
function showLogin() {
  show("boot", "This browser is not signed in to this server.");
  $("login").hidden = false;
  if (new URLSearchParams(location.search).has("refused")) {
    $("login-error").textContent = "That token was refused. Paste the one `aegis serve` printed.";
    history.replaceState(null, "", location.pathname + location.hash);
  }
  $("login-token").focus();
}

$("login").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  $("login-error").textContent = "";
  const r = await fetch("/login", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ token: $("login-token").value.trim() }),
  });
  if (r.ok) location.reload();
  else $("login-error").textContent = "That token was refused.";
});
```

`files.py` docstring: replace `the token lives in the tab's ``sessionStorage`` and only the websocket carries it, so an ``<img>``, a new tab and a download could not.` with `the websocket needs the server's token, and an ``<img>``, a new tab and a download cannot prove it.`, and `on aegis's origin a script could read the token and drive every agent` with `on aegis's origin a script could open the websocket, which the browser would sign in with its cookie, and drive every agent`.

`DESIGN.md`, "A sent file's URL is its key": replace `on aegis's own origin their script could read the token from `sessionStorage` and drive every agent (`files.py`).` with `on aegis's own origin their script could open the websocket, which the browser signs in with its cookie, and drive every agent; the sandbox's opaque origin fails the socket's origin check (`files.py`).` In "Agent text is untrusted", append: `The browser holds the token only as an HttpOnly cookie, named per state root, that no script on the page can read.`

- [ ] **Step 8: Run the browser tests**

Run: `uv run pytest -q -m browser -k "wrong_token or pasting_the_token or two_servers or rotated_token or spawn_to_close or restart_brings or sent_file"`
Expected: PASS. `test_a_sent_file_previews…` still reads "storage refused" from the sandboxed report.

- [ ] **Step 9: Commit**

```bash
git add src/aegis/web.py src/aegis/client/js/protocol.js src/aegis/client/js/app.js src/aegis/client/index.html src/aegis/client/css/base.css src/aegis/files.py DESIGN.md tests/test_web.py tests/test_browser.py
git commit -m "feat(web): the token lives in an HttpOnly cookie; a browser signs in once"
```

### Task 2: The manifest and the icons

**Files:**
- Create: `scripts/make_icons.py`
- Create: `src/aegis/client/icons/aegis-192.png`, `aegis-512.png`, `aegis-maskable-512.png` (generated)
- Modify: `src/aegis/web.py` (`manifest` route)
- Modify: `src/aegis/client/index.html` (`<head>`)
- Test: `tests/test_web.py` (append)

**Interfaces:**
- Produces: `GET /manifest.webmanifest` (`application/manifest+json`), icons under `/static/icons/`.

- [ ] **Step 1: Write the failing test**

Add `import json` and `import struct` to the imports at the top of `tests/test_web.py`, then append:

```python
def png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def test_the_manifest_names_the_server_and_its_icons_load(project, fake_claude):
    with client_for(project, fake_claude) as c:
        r = c.get("/manifest.webmanifest")
        assert r.headers["content-type"].startswith("application/manifest+json")
        m = json.loads(r.text)
        assert m["display"] == "standalone" and m["start_url"] == "/"
        assert m["name"].startswith("aegis · ") and m["short_name"] == "aegis"
        got_icons = set()
        for icon in m["icons"]:
            got = c.get(icon["src"])
            assert got.status_code == 200 and got.headers["content-type"] == "image/png"
            w, h = png_size(got.content)
            assert f"{w}x{h}" == icon["sizes"]
            got_icons.add((icon["sizes"], icon.get("purpose", "any")))
        assert got_icons == {
            ("192x192", "any"),
            ("512x512", "any"),
            ("512x512", "maskable"),
        }
        page = c.get("/").text
        assert '<link rel="manifest" href="/manifest.webmanifest">' in page
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest -q tests/test_web.py -k manifest`
Expected: FAIL with a 404 page for `/manifest.webmanifest`.

- [ ] **Step 3: The icon script, and the icons**

Create `scripts/make_icons.py`:

```python
"""Draw the app icons: an amber hexagon outline on Ink's background.

    uv run --with pillow python scripts/make_icons.py

Pillow is not a dependency of aegis; the PNGs are committed. The maskable icon
keeps the hexagon inside the central 80%, the safe zone a launcher may crop to.
"""

import math
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).parents[1] / "src" / "aegis" / "client" / "icons"
BG, FG = "#11100e", "#e0a872"


def draw(size: int, scale: float) -> Image.Image:
    im = Image.new("RGB", (size, size), BG)
    r = size * scale / 2
    c = size / 2
    pts = [
        (c + r * math.cos(math.radians(60 * k - 90)), c + r * math.sin(math.radians(60 * k - 90)))
        for k in range(6)
    ]
    ImageDraw.Draw(im).polygon(pts, outline=FG, width=max(2, size // 22))
    return im


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    draw(192, 0.72).save(OUT / "aegis-192.png")
    draw(512, 0.72).save(OUT / "aegis-512.png")
    draw(512, 0.56).save(OUT / "aegis-maskable-512.png")


if __name__ == "__main__":
    main()
```

Run: `uv run --with pillow python scripts/make_icons.py && ls -l src/aegis/client/icons/`
Expected: three PNGs.

- [ ] **Step 4: The route and the head**

In `build_web`, add:

```python
    manifest = {
        "name": f"aegis · {socket.gethostname()}",
        "short_name": "aegis",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#11100e",
        "theme_color": "#0b0a09",
        "icons": [
            {"src": "/static/icons/aegis-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icons/aegis-512.png", "sizes": "512x512", "type": "image/png"},
            {
                "src": "/static/icons/aegis-maskable-512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "maskable",
            },
        ],
    }

    async def webmanifest(request):
        """What Chrome installs: the server's name, so zion and the VPS differ
        on a home screen. No service worker: Chrome installs from its menu
        without one since 108 on Android, and a caching one could serve a stale
        client after an upgrade."""
        return JSONResponse(manifest, media_type="application/manifest+json")
```

and the route `Route("/manifest.webmanifest", webmanifest),` after `Route("/login", ...)`.

`index.html`, in `<head>` after the viewport tag:

```html
<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="#0b0a09">
<link rel="icon" href="/static/icons/aegis-192.png">
```

- [ ] **Step 5: Run the test**

Run: `uv run pytest -q tests/test_web.py -k manifest`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/make_icons.py src/aegis/client/icons/ src/aegis/web.py src/aegis/client/index.html tests/test_web.py
git commit -m "feat(web): a manifest and icons, so Chrome installs aegis"
```

### Task 3: Release note, gates, and a real browser

**Files:**
- Create: `changelog.d/mobile-install-and-cookie.added.md`

- [ ] **Step 1: Release note**

```markdown
- **aegis installs as an app, and a browser signs in once.** Chrome's menu offers "Add to home screen" (Android) or "Install" (desktop), named after the server. Opening the URL `aegis serve` prints, or pasting its token into the new sign-in field, sets an HttpOnly cookie that lasts a year, so an installed app opens signed in. No script on the page can read the token any more.
```

Run: `make changelog-check`
Expected: exit 0.

- [ ] **Step 2: The gates**

Run: `make check`, then `uv run pytest -q -m browser`.
Expected: all pass.

- [ ] **Step 3: A real browser, then the phone**

Start `uv run aegis serve --root <a temp dir> --port 8796 -d` from this worktree. In desktop Chrome: open the printed URL, check the address bar has no token, open devtools > Application > Manifest (no errors, three icons) and > Cookies (one `aegis_…` cookie, HttpOnly). Open `http://127.0.0.1:8796/` in a fresh profile: the sign-in field shows; paste the token; the Fleet shows. `kill` the server. Installing on the phone over dev.apiad.net is slice 5 of the spec, checked by hand and reported as such.

- [ ] **Step 4: Commit**

```bash
git add changelog.d/mobile-install-and-cookie.added.md
git commit -m "docs: release note for installing aegis and the cookie lock"
```
