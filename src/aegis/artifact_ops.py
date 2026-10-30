"""The artifact operations: five for agents, six for the page's bridge.

An agent creates a draft, edits it with its own tools, and sends it; the send
runs the static checks, snapshots the draft like ``file.send`` does, asks the
browsers on the transcript to run it hidden (``Board.probe``), and records the
artifact only when the page started or nobody was there to try. The page's
side is six person operations the bridge (``client/js/artifacts.js``) calls
with the artifact id the host holds, never one the page sent.
"""

from __future__ import annotations

import asyncio
import shutil
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, field_validator

from . import artifacts, dictation, files
from . import attachments as att
from . import layout as canvas
from .artifacts import ArtifactError
from .ops import Caller, OpError

if TYPE_CHECKING:
    from .app import App


class _Strict(BaseModel):
    model_config = {"extra": "forbid"}


Label = Field(min_length=1, max_length=artifacts.LABEL_MAX, pattern=r"^[^\n]+$")
# Characters of a page's error message or stack kept in the wake.
ERROR_MESSAGE_MAX = 4000


class ArtifactCreate(_Strict):
    title: str = Field(
        min_length=1,
        max_length=artifacts.TITLE_MAX,
        pattern=r"^[^\n]+$",
        description="The page's heading and title.",
    )
    caption: str | None = Field(
        None, description="One line of Markdown shown above the card."
    )
    state: Any = Field(None, description="JSON the page receives on init; up to 64 KB.")


class ArtifactId(_Strict):
    id: str


class ArtifactSend(ArtifactId):
    dock: str | None = Field(None, description=canvas.DOCK_DOC)


class ArtifactUpdate(_Strict):
    id: str
    state: Any = Field(None, description="New state, pushed into the live page.")
    caption: str | None = None
    resend: bool = Field(
        False, description="Snapshot the edited draft again and swap the page."
    )


class ArtifactClose(_Strict):
    id: str
    label: str | None = Field(None, max_length=artifacts.LABEL_MAX, pattern=r"^[^\n]+$")


class PageRef(_Strict):
    log_id: str
    artifact_id: str


class PageState(PageRef):
    state: Any = None


class PageEmit(PageRef):
    name: str
    data: Any = None
    # The page got dictated text since its last answer: the agent reads the
    # answer with dictation.NOTE after it. The bridge sets it, not the page.
    dictated: bool = False


class PageSubmit(PageRef):
    data: Any = None
    label: str = Label
    dictated: bool = False


class PageUpload(PageRef):
    upload_id: str


class _CutError(BaseModel):
    """A page's error text is uncapped on its side; what reaches the agent, as
    a wake or as a failed send, carries at most ERROR_MESSAGE_MAX of each."""

    @field_validator("message", "stack", mode="before", check_fields=False)
    @classmethod
    def _cut(cls, v: object) -> object:
        return v[:ERROR_MESSAGE_MAX] if isinstance(v, str) else v


class PageError(PageRef, _CutError):
    message: str
    stack: str = ""


class Probed(_Strict, _CutError):
    log_id: str
    probe_id: str
    started: bool
    message: str = ""
    stack: str = ""


def _err(e: ArtifactError) -> OpError:
    return OpError(e.code, e.message)


def _sized(obj: Any, what: str) -> None:
    n = artifacts.json_size(obj)
    if n > artifacts.MAX_STATE_BYTES:
        raise OpError(
            "too_large",
            f"{what} is {n} bytes; the limit is {artifacts.MAX_STATE_BYTES}",
        )


def register_artifact_ops(app: App) -> None:
    r, reg = app.registry, app.sessions

    def own(caller: Caller):
        if not caller.is_agent or caller.log_id not in reg.sessions:
            raise OpError("agents_only", "only an agent's session can do this")
        return reg.sessions[caller.log_id]

    async def land(s, a, caption: str | None):
        """Checks, snapshot, probe, record; the snapshot goes when the probe fails."""
        run = app.place_of_session(s.log_id).run
        path = artifacts.draft_path(s.drafts, a.id)
        try:
            artifacts.check(await run(path.read_text, encoding="utf-8"))
        except OSError as e:
            raise OpError("unreadable", f"cannot read the draft {path}: {e}") from e
        except ArtifactError as e:
            raise _err(e) from e
        try:
            (rec,) = await files.store_as(app.roots.state_root, [path], run)
        except files.FileError as e:
            raise OpError(e.code, e.message) from e
        url = files.url(rec["file_id"], rec["name"])
        landed = False
        watching = app.channels.subscribers(s.channel)
        try:
            started = await s.artifacts.probe(a, url, watching)
            if a.status not in ("draft", "live"):
                # The person answered the old page while the probe ran.
                raise ArtifactError(
                    "not_live", f"{a.id} was {a.status} while the new page was probed"
                )
            s.artifacts.land(a, rec["file_id"], rec["name"], caption, started)
            landed = True
        except ArtifactError as e:
            raise _err(e) from e
        finally:
            if (
                not landed
            ):  # a failed probe, a late submit, an interrupt: nothing is served
                shutil.rmtree(
                    app.roots.state_root / "files" / rec["file_id"], ignore_errors=True
                )
        return {"id": a.id, "url": url, "started": started, "watching": watching > 0}

    # -- agents ------------------------------------------------------------------
    @r.op("artifact.create", ArtifactCreate, agent=True)
    async def create(p: ArtifactCreate, caller):
        """Start an interactive page for the person: a decision to make, values
        to tune, a form, a visualization to play with. Writes a working
        skeleton and returns its path and text; edit that file with your Edit
        tool, then artifact_send. The page talks to aegis through
        `window.aegis`: `aegis.ready((state, theme) => ...)` runs once the host
        has handed over the state and the theme's CSS variables;
        `aegis.state(obj)` keeps state silently for you to artifact_read;
        `aegis.emit(name, obj)` wakes you and leaves the page live;
        `aegis.submit(obj, label)` wakes you with the answer and closes the
        page, the card collapsing to `label`; `aegis.onState(fn)` hears your
        artifact_update; `aegis.onStatus(fn)` hears `{status, refused}`, where
        `refused` is `{code, message}` when the server refused the last submit
        (the card shows the person the message), also on
        `<html data-refused>`; `aegis.dictate(field, fn)` inside a click
        dictates into a textarea, `fn` hearing loading, listening, finishing
        and idle; `aegis.upload(file, onProgress)` puts a file of up to 100 MB
        in your inbox, wakes you with its path and resolves to `{name, path,
        mime, size}` (help_read artifacts, "Take dictation or a file").
        Answers reach you as a user turn headed
        `> from artifact:<id> · submit|<event>|upload|error · …` with the JSON in a
        code block. Rules the checks cannot catch: an event name is one word,
        `[a-z][a-z0-9_-]{0,31}`, never `submit`, `error`, `close` or `upload`; state,
        event data and the submit's data are at most 64 KB each; at most 20
        emits a minute; a submit's label is one line of at most 140
        characters. Keep the skeleton's script tag and stylesheet link, or
        drop the stylesheet for your own look."""
        s = own(caller)
        if p.state is not None:
            _sized(p.state, "state")
        a = s.artifacts.create(p.title, p.caption, p.state, write=False)
        run = app.place_of_session(s.log_id).run
        # Written as the session's user, in its home in multi-user mode.
        path = await run(artifacts.write_draft, s.drafts, a.id, p.title)
        return {"id": a.id, "path": str(path), "html": artifacts.skeleton(p.title)}

    @r.op("artifact.send", ArtifactSend, agent=True)
    async def send(p: ArtifactSend, caller):
        """Land the draft in your transcript. The page is checked and, when
        the person's browser has this transcript open, run hidden there first:
        a page that throws is refused with `page_error` and the message, and
        nothing is shown, so edit the draft and send again. `started` is true
        when the page ran, null when it landed untried (a script error then
        reaches your inbox when someone opens it). `watching` says why: false
        when no browser had your transcript on screen (the person will see
        the card when they open your tab), true when one had it but did not
        answer within 3 s. Only then write your message, which may refer to the card above
        it, and end the turn with turn_end(needs_you) when the page asks
        something. With dock, the page also opens beside the transcript."""
        s = own(caller)
        try:
            a = s.artifacts.get(p.id)
        except ArtifactError as e:
            raise _err(e) from e
        if a.status != "draft":
            raise OpError(
                "not_draft",
                f"{p.id} is {a.status}; artifact_update with resend to swap a live page",
            )
        # place() lets the page through before it has landed; a bad dock is
        # refused before anything is stored or probed.
        if p.dock is not None:
            try:
                canvas.place(s.layout, a.id, p.dock, s.dockable)
            except canvas.LayoutError as e:
                raise OpError("bad_params", str(e)) from e
        out = await land(s, a, a.caption)
        if p.dock is not None:
            # Against the layout as it is now: the probe can take 3 s, and the
            # person may have moved a pane meanwhile. The page has landed, so a
            # placement that no longer fits is reported, not raised.
            try:
                s.set_layout(canvas.place(s.layout, a.id, p.dock, s.dockable), "agent")
            except canvas.LayoutError as e:
                out |= {"docked": False, "reason": str(e)}
        return out

    @r.op("artifact.read", ArtifactId, agent=True)
    async def read(p: ArtifactId, caller):
        """The page's status (draft, live, submitted, closed), its latest state,
        its last 20 events, the submit's data and label, and `errors`: how
        many script errors aegis heard after the first, which woke you and
        was the only one to (a browser forwards a frame's first error only,
        so a loop in one frame counts once at most). Wakes nobody."""
        s = own(caller)
        try:
            return s.artifacts.read(p.id)
        except ArtifactError as e:
            raise _err(e) from e

    @r.op("artifact.update", ArtifactUpdate, agent=True)
    async def update(p: ArtifactUpdate, caller):
        """Change a live page: push `state` into it, change its caption, or
        with `resend` snapshot the edited draft again and swap the page (the
        state carries over unless `state` is also given)."""
        s = own(caller)
        if p.state is None and p.caption is None and not p.resend:
            raise OpError("bad_params", "nothing to change")
        if p.state is not None:
            _sized(p.state, "state")  # before a resend swaps the page
        try:
            a = s.artifacts.live(p.id)
            if p.resend:
                out = await land(
                    s, a, p.caption if p.caption is not None else a.caption
                )
            elif p.caption is not None:
                s.artifacts.land(a, a.file_id, a.name, p.caption, a.started)
                out = {
                    "id": a.id,
                    "url": files.url(a.file_id, a.name),
                    "started": a.started,
                }
            else:
                out = {
                    "id": a.id,
                    "url": files.url(a.file_id, a.name),
                    "started": a.started,
                }
            if p.state is not None:
                s.artifacts.state(a.id, p.state, "agent")
        except ArtifactError as e:
            raise _err(e) from e
        return out

    @r.op("artifact.close", ArtifactClose, agent=True)
    async def close(p: ArtifactClose, caller):
        """Withdraw a page; the card collapses to `label`. A draft never sent
        is simply discarded."""
        s = own(caller)
        try:
            draft = s.artifacts.get(p.id).status == "draft"
            s.artifacts.close(p.id, p.label, drop=False)
        except ArtifactError as e:
            raise _err(e) from e
        if draft:
            run = app.place_of_session(s.log_id).run
            await run(artifacts.drop_draft, s.drafts, p.id)
        return "closed"

    # -- the page, through the bridge ----------------------------------------------
    def page(p: PageRef):
        s = reg.open(p.log_id)
        try:
            s.artifacts.get(p.artifact_id)
        except ArtifactError as e:
            raise _err(e) from e
        return s

    @r.op("artifact.state", PageState, owned="log_id")
    async def state(p: PageState, caller):
        s = page(p)
        _sized(p.state, "state")
        try:
            s.artifacts.state(p.artifact_id, p.state, "page")
        except ArtifactError as e:
            raise _err(e) from e
        return "ok"

    @r.op("artifact.emit", PageEmit, owned="log_id")
    async def emit(p: PageEmit, caller):
        s = page(p)
        s.check_lock()  # a person does not steer a workflow's node
        _sized(p.data, "data")
        try:
            s.artifacts.event(p.artifact_id, p.name, p.data)
        except ArtifactError as e:
            raise _err(e) from e
        text = artifacts.body(p.data)
        await s.deliver(
            artifacts.header(p.artifact_id, p.name),
            dictation.with_note(text) if p.dictated else text,
        )
        return "ok"

    @r.op("artifact.submit", PageSubmit, owned="log_id")
    async def submit(p: PageSubmit, caller):
        s = page(p)
        s.check_lock()  # a person does not steer a workflow's node
        _sized(p.data, "data")
        try:
            s.artifacts.submit(p.artifact_id, p.data, p.label)
        except ArtifactError as e:
            raise _err(e) from e
        # The person's own answer: the turn it begins is theirs, as /setup's
        # apply is (Session.person_turn).
        text = artifacts.body(p.data, p.label)
        await s.deliver(
            artifacts.header(p.artifact_id, "submit"),
            dictation.with_note(text) if p.dictated else text,
            by_person=not caller.is_agent,
        )
        return "ok"

    @r.op("artifact.upload", PageUpload, owned="log_id")
    async def upload(p: PageUpload, caller):
        """Move a file the page uploaded (attachment.begin and .put) into the
        session's inbox and wake the agent with its path. It counts as an
        event, and the count comes first, so a refused file stays staged and
        reaches nobody. The page gets the record, for the person to see."""
        s = page(p)
        s.check_lock()  # a person does not steer a workflow's node
        try:
            s.artifacts.rate(p.artifact_id)
        except ArtifactError as e:
            raise _err(e) from e
        try:
            (rec,) = await asyncio.to_thread(
                att.commit,
                app.roots.state_root,
                p.log_id,
                [p.upload_id],
                deliver=app._deliver(s),
            )
        except files.FileError as e:
            raise OpError(e.code, e.message) from e
        sent = {k: rec[k] for k in ("name", "path", "mime", "size")}
        try:
            s.artifacts.record_event(p.artifact_id, "upload", sent)
        except ArtifactError:
            pass  # closed while the file moved: the agent is still told where it is
        await s.deliver(
            artifacts.header(p.artifact_id, "upload"),
            att.message("", [rec]),
            by_person=not caller.is_agent,
        )
        return sent

    @r.op("artifact.error", PageError, owned="log_id")
    async def error(p: PageError, caller):
        s = page(p)
        s.check_lock()  # a person does not steer a workflow's node
        if not s.artifacts.error_wakes(p.artifact_id):
            return "dropped"
        await s.deliver(
            artifacts.header(p.artifact_id, "error"),
            artifacts.body(None, error=(p.message, p.stack)),
        )
        return "ok"

    @r.op("artifact.probed", Probed, owned="log_id")
    async def probed(p: Probed, caller):
        s = reg.open(p.log_id)
        return (
            "ok"
            if s.artifacts.probed(p.probe_id, p.started, p.message, p.stack)
            else "dropped"
        )
