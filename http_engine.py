"""HTTP engine for FCU-AutoClass.

After the Selenium + OCR login, the browser is left idle and the course page
is driven with raw ASP.NET postbacks through ``requests``. Every course gets
its own thread and its own ViewState chain, so N courses are polled
concurrently instead of one after another.

Field names come from the page itself: the add / seat-query buttons are read
from the ``gvToAdd`` row of each response, so only the textbox and the lookup
button names are hard-coded.
"""
import html
import json
import logging
import os
import queue
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter

log = logging.getLogger(__name__)

# ---- page constants --------------------------------------------------------
PREFIX = "ctl00$MainContent$TabContainer1$tabSelected$"
F_SUBID = PREFIX + "tbSubID"
F_GETSUB = PREFIX + "btnGetSub"
GRID_ID = "ctl00_MainContent_TabContainer1_tabSelected_gvToAdd"
MSG_ID = "ctl00_MainContent_TabContainer1_tabSelected_lblMsgBlock"
LOGOUT_ID = "ctl00_btnLogout"
# The AjaxControlToolkit TabContainer remembers the active tab client-side in
# this hidden field and posts it back. btnGetSub only renders the gvToAdd grid
# when the "已選課表" (add/drop) tab is active, which the Selenium version
# reached with driver_click(Label3). A fresh login lands on tab 0, so every
# postback must carry ActiveTabIndex = the add/drop tab index.
CLIENT_STATE_FIELD = "ctl00_MainContent_TabContainer1_ClientState"
TAB_SELECTED_INDEX = 1
LOGIN_USER_ID = "ctl00_Login1_UserName"
ADD_TD = 1      # 1-based <td> index of the add button in the course row
QUOTA_TD = 8    # 1-based <td> index of the seat-query button
REGISTRATION_MARK = "登記人數"
SUCCESS_MARK = "加選成功"
INVALID_POSTBACK_MARKS = ("Invalid postback or callback argument",
                          "Validation of viewstate MAC failed",
                          "無效的回傳", "檢視狀態 MAC")


class RegistrationPhase(Exception):
    """The system is in the registration (登記) phase; realtime add/drop is closed."""


class SeatQueryNoResponse(Exception):
    """Seat queries for a course repeatedly came back without a seat message."""


class SessionExpired(Exception):
    """The login session is gone (redirected to Login.aspx / no logout button)."""


class PageChanged(Exception):
    """The response did not contain the course row / buttons we expect."""


class InvalidPostback(Exception):
    """ASP.NET rejected the postback (HTTP 5xx or an event-validation error)."""


class NoAlert(Exception):
    """A seat query came back without a seat-count message."""


# ---- parsers (pure functions, unit-tested offline) -------------------------
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_HIDDEN_INPUT_RE = re.compile(r"<input\b[^>]*\btype\s*=\s*[\"']hidden[\"'][^>]*>", re.I)
_ALERT_RE = re.compile(r"""alert\(\s*(['"])((?:\\.|(?!\1).)*?)\1\s*\)""", re.S)
_QUOTA_RE = re.compile(r"剩餘名額\s*/\s*開放名額\s*[:：]\s*(\d+)\s*/\s*(\d+)")
_DOPOSTBACK_RE = re.compile(
    r"""__doPostBack\(\s*(['"])(.*?)\1\s*,\s*(['"])(.*?)\3\s*\)""")
_TR_RE = re.compile(r"<tr\b.*?</tr\s*>", re.S | re.I)
_TD_RE = re.compile(r"<t[dh]\b.*?</t[dh]\s*>", re.S | re.I)
_CONTROL_RE = re.compile(r"<(?:input|a|button)\b[^>]*>", re.I)


def _attr(tag: str, name: str) -> Optional[str]:
    """Return the (HTML-unescaped) value of attribute ``name`` in one tag."""
    m = re.search(r"(?<![\w-])" + re.escape(name)
                  + r"\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))", tag, re.I)
    if not m:
        return None
    return html.unescape(next(v for v in m.groups() if v is not None))


def _strip_tags(fragment: str) -> str:
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", fragment))).strip()


def _extract_element(page: str, elem_id: str) -> Optional[str]:
    """Return the outer HTML of the element with ``id=elem_id`` (depth-aware)."""
    m = re.search(r"<([a-zA-Z][\w:-]*)\b[^>]*?(?<![\w-])id\s*=\s*[\"']"
                  + re.escape(elem_id) + r"[\"'][^>]*>", page, re.I)
    if not m:
        return None
    if m.group(0).endswith("/>"):
        return m.group(0)
    tag = re.escape(m.group(1))
    depth = 0
    for tok in re.finditer(r"<" + tag + r"\b[^>]*>|</" + tag + r"\s*>",
                           page[m.start():], re.I):
        text = tok.group(0)
        if text.startswith("</"):
            depth -= 1
        elif not text.endswith("/>"):
            depth += 1
        if depth == 0:
            return page[m.start():m.start() + tok.end()]
    return page[m.start():]


def parse_hidden_fields(page: str) -> Dict[str, str]:
    """All ``<input type="hidden">`` fields as name -> unescaped value."""
    fields = {}
    for tag in _HIDDEN_INPUT_RE.findall(page):
        name = _attr(tag, "name") or _attr(tag, "id")
        if name:
            fields[name] = _attr(tag, "value") or ""
    return fields


def parse_alerts(page: str) -> List[str]:
    """Texts of every ``alert('...')`` literal in the page, unescaped."""
    out = []
    for _quote, text in _ALERT_RE.findall(page):
        text = (text.replace("\\'", "'").replace('\\"', '"')
                .replace("\\n", "\n").replace("\\\\", "\\"))
        out.append(html.unescape(text).strip())
    return out


def parse_quota(text: str) -> Optional[Tuple[int, int]]:
    """``剩餘名額/開放名額：3 / 60`` -> (3, 60); None when not a quota message."""
    m = _QUOTA_RE.search(text)
    return (int(m.group(1)), int(m.group(2))) if m else None


def parse_msg_block(page: str) -> str:
    """Visible text of the add-result label (``lblMsgBlock``)."""
    return _strip_tags(_extract_element(page, MSG_ID) or "")


def is_logged_in(page: str) -> bool:
    return LOGOUT_ID in page


def is_login_page(url: str, page: str) -> bool:
    return "login.aspx" in (url or "").lower() or LOGIN_USER_ID in page


def is_invalid_postback(status: int, page: str) -> bool:
    return status >= 500 or any(mark in page for mark in INVALID_POSTBACK_MARKS)


def activate_tab(fields: Dict[str, str], index: int = TAB_SELECTED_INDEX) -> Dict[str, str]:
    """Force the add/drop tab active in the TabContainer ClientState field.

    Mutates and returns ``fields``. Preserves the existing ``TabState`` array
    (all tabs enabled by default) and only overrides ``ActiveTabIndex``.
    """
    raw = fields.get(CLIENT_STATE_FIELD) or ""
    try:
        state = json.loads(raw) if raw else {}
    except (ValueError, TypeError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    state["ActiveTabIndex"] = index
    state.setdefault("TabState", [True, True, True])
    fields[CLIENT_STATE_FIELD] = json.dumps(state, separators=(",", ":"))
    return fields


@dataclass(frozen=True)
class ButtonSpec:
    """How to trigger one server-side button in a postback.

    ``by_name=False``: ``__doPostBack(target, argument)`` style.
    ``by_name=True``: a submit button posted as ``target=argument``
    (``image=True`` posts ``target.x``/``target.y`` instead).
    """
    target: str
    argument: str = ""
    by_name: bool = False
    image: bool = False

    def payload(self) -> Dict[str, str]:
        if not self.by_name:
            return {"__EVENTTARGET": self.target, "__EVENTARGUMENT": self.argument}
        fields = {"__EVENTTARGET": "", "__EVENTARGUMENT": ""}
        if self.image:
            fields[self.target + ".x"] = "1"
            fields[self.target + ".y"] = "1"
        else:
            fields[self.target] = self.argument
        return fields

    @classmethod
    def from_input(cls, tag: str) -> Optional["ButtonSpec"]:
        for attr in ("onclick", "href"):
            m = _DOPOSTBACK_RE.search(_attr(tag, attr) or "")
            if m:
                return cls(m.group(2), m.group(4))
        name = _attr(tag, "name")
        if name:
            return cls(name, _attr(tag, "value") or "", by_name=True,
                       image=(_attr(tag, "type") or "").lower() == "image")
        return None


@dataclass
class CourseRow:
    """The first data row of ``gvToAdd``: cell texts plus its two buttons."""
    cells: List[str]
    add: Optional[ButtonSpec]
    quota: Optional[ButtonSpec]
    raw: str

    def mentions(self, class_id: str) -> bool:
        return any(class_id == cell or class_id in cell.split() for cell in self.cells)


def _button_in(cells: List[str], index: int) -> Optional[ButtonSpec]:
    if index > len(cells):
        return None
    for tag in _CONTROL_RE.findall(cells[index - 1]):
        spec = ButtonSpec.from_input(tag)
        if spec:
            return spec
    return None


def parse_course_row(page: str) -> Optional[CourseRow]:
    table = _extract_element(page, GRID_ID)
    if table is None:
        return None
    rows = _TR_RE.findall(table)
    if len(rows) < 2:
        return None
    row = rows[1]
    cells = _TD_RE.findall(row)
    return CourseRow(cells=[_strip_tags(c) for c in cells],
                     add=_button_in(cells, ADD_TD),
                     quota=_button_in(cells, QUOTA_TD),
                     raw=row)


# Postback that makes the server render the gvToAdd row for the id typed into
# tbSubID. discover_form.py verifies this against the live page; if the
# textbox turns out to be AutoPostBack, replace with
# ButtonSpec(F_SUBID, "") (i.e. __EVENTTARGET = tbSubID).
LOOKUP = ButtonSpec(F_GETSUB, "查詢", by_name=True)


def _decode(response) -> str:
    encoding = response.encoding or "utf-8"
    try:
        return response.content.decode(encoding, "replace")
    except LookupError:
        return response.content.decode("utf-8", "replace")


class LoginSession:
    """One logged-in ``requests.Session`` shared by every CourseWorker."""

    def __init__(self, session: requests.Session, postback_url: str,
                 timeout: Tuple[float, float] = (5, 20)):
        self.session = session
        self.postback_url = postback_url
        self.timeout = timeout
        self.last_response = None  # kept for diagnostics (discover_form.py)

    @classmethod
    def from_selenium(cls, driver, pool_size: int = 4) -> "LoginSession":
        """Copy the browser's cookies / UA / current URL into a requests session."""
        url = driver.current_url
        parts = urlsplit(url)
        session = requests.Session()
        session.trust_env = False
        session.headers.update({
            "User-Agent": driver.execute_script("return navigator.userAgent"),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
            "Referer": url,
            "Origin": f"{parts.scheme}://{parts.netloc}",
        })
        for cookie in driver.get_cookies():
            session.cookies.set(cookie["name"], cookie["value"],
                                domain=cookie.get("domain"), path=cookie.get("path", "/"))
        adapter = HTTPAdapter(pool_connections=4, pool_maxsize=max(10, pool_size))
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return cls(session, url)

    def get_page(self) -> str:
        return self._check(self.session.get(self.postback_url, timeout=self.timeout))

    def post(self, fields: Dict[str, str]) -> str:
        return self._check(self.session.post(self.postback_url, data=fields,
                                             timeout=self.timeout))

    def _check(self, response) -> str:
        self.last_response = response
        page = _decode(response)
        if is_invalid_postback(response.status_code, page):
            raise InvalidPostback(f"HTTP {response.status_code}: {_strip_tags(page)[:200]}")
        if is_login_page(response.url, page) or not is_logged_in(page):
            raise SessionExpired(response.url)
        return page

    def close(self) -> None:
        self.session.close()


@dataclass
class SharedState:
    """State shared by all workers: the pending list, locks, stop flag, events."""
    class_ids: List[str]
    notify: Callable[[str], None]
    lock: threading.Lock = field(default_factory=threading.Lock)
    add_lock: threading.Lock = field(default_factory=threading.Lock)
    stop: threading.Event = field(default_factory=threading.Event)
    events: "queue.Queue" = field(default_factory=queue.Queue)

    def on_success(self, class_id: str) -> None:
        with self.lock:
            if class_id in self.class_ids:
                self.class_ids.remove(class_id)
            pending = list(self.class_ids)
        message = "✅ 成功加選課程：" + class_id
        if pending:
            message += "\n尚待加選：" + " ".join(pending)
        self.notify(message)
        self.events.put(("done", class_id))

    def fail(self, exc: BaseException) -> None:
        self.events.put(("error", exc))
        self.stop.set()


class CourseWorker(threading.Thread):
    """Polls one course; owns its own ViewState chain (``self.fields``)."""

    MAX_NO_ALERT = 3
    MAX_ERRORS = 8

    def __init__(self, login: LoginSession, class_id: str, state: SharedState,
                 interval: float, dry_run: bool = False, log_dir: str = "./logs"):
        super().__init__(name=f"course-{class_id}", daemon=True)
        self.login = login
        self.class_id = class_id
        self.state = state
        self.interval = interval
        self.dry_run = dry_run
        self.log_dir = log_dir
        self.fields: Dict[str, str] = {}
        self.row: Optional[CourseRow] = None
        self.last_page: str = ""

    # -- single postbacks ----------------------------------------------------
    def _post(self, overlay: Dict[str, str]) -> str:
        payload = dict(self.fields)
        payload["__EVENTTARGET"] = ""
        payload["__EVENTARGUMENT"] = ""
        payload[F_SUBID] = self.class_id
        payload.update(overlay)
        activate_tab(payload)
        page = self.login.post(payload)
        self._absorb(page)
        return page

    def _absorb(self, page: str) -> None:
        self.last_page = page
        self.fields = parse_hidden_fields(page)
        if "__VIEWSTATE" not in self.fields:
            raise PageChanged("回應中沒有 __VIEWSTATE")

    def reseed(self) -> None:
        """Start a fresh ViewState chain from a GET of the course page."""
        self._absorb(self.login.get_page())
        self.row = None

    def lookup(self) -> None:
        """Make the server render the gvToAdd row for this course."""
        page = self._post(LOOKUP.payload())
        row = parse_course_row(page)
        if row is None or row.quota is None or row.add is None:
            raise PageChanged(f"課程{self.class_id}: 回應中找不到課程列/按鈕 "
                              f"(alerts={parse_alerts(page)})")
        if not row.mentions(self.class_id):
            raise PageChanged(f"課程{self.class_id}: 課程列不含此課號 {row.cells}")
        self.row = row

    def query_seats(self) -> Tuple[int, int]:
        """One seat-query postback -> (remaining, total)."""
        if self.row is None:
            self.lookup()
        page = self._post(self.row.quota.payload())
        row = parse_course_row(page)
        if row is not None and row.quota is not None and row.add is not None:
            self.row = row
        else:
            self.row = None
        alerts = parse_alerts(page)
        for text in alerts:
            if REGISTRATION_MARK in text:
                raise RegistrationPhase(text)
            quota = parse_quota(text)
            if quota:
                return quota
        self._dump(page, "no-alert")
        raise NoAlert(alerts)

    def add(self) -> Tuple[bool, str]:
        """Send the add postback -> (success, message text)."""
        with self.state.add_lock:
            page = self._post(self.row.add.payload())
        message = parse_msg_block(page)
        alerts = parse_alerts(page)
        text = message or " | ".join(alerts)
        success = SUCCESS_MARK in message or any(SUCCESS_MARK in a for a in alerts)
        row = parse_course_row(page)
        self.row = row if (row and row.quota and row.add) else None
        return success, text

    def _dump(self, page: str, tag: str) -> Optional[str]:
        try:
            os.makedirs(self.log_dir, exist_ok=True)
            path = os.path.join(self.log_dir, time.strftime(
                f"course-{self.class_id}-{tag}-%Y%m%d-%H%M%S.html"))
            with open(path, "w", encoding="utf-8") as f:
                f.write(page)
            return path
        except OSError as error:
            log.warning("無法儲存頁面到 %s: %s", self.log_dir, error)
            return None

    # -- the loop -----------------------------------------------------------
    def run(self) -> None:
        errors = 0
        no_alert = 0
        need_seed = True
        stop = self.state.stop
        while not stop.is_set():
            started = time.monotonic()
            try:
                if need_seed:
                    self.reseed()
                    self.lookup()
                    need_seed = False
                remain, total = self.query_seats()
                log.info("課程%s: 剩餘名額/開放名額：%s / %s", self.class_id, remain, total)
                if remain > 0:
                    if self.dry_run:
                        log.info("[DRY-RUN] 課程%s 有名額 (%s), 略過加選", self.class_id, remain)
                    else:
                        success, text = self.add()
                        if success:
                            log.info("成功加選課程：%s (%s)", self.class_id, text)
                            self.state.on_success(self.class_id)
                            return
                        log.warning("課程%s: 加選失敗 (%s), 請確認是否已加選或衝堂/超修, "
                                    "也可能被其他機器人搶走了..", self.class_id, text)
                errors = 0
                no_alert = 0
            except (RegistrationPhase, SessionExpired) as error:
                self.state.fail(error)
                return
            except NoAlert as error:
                no_alert += 1
                log.warning("查詢課程 %s 的名額時沒有取得名額訊息 (第%s次), 回應訊息=%s",
                            self.class_id, no_alert, error.args[0] if error.args else "")
                if no_alert >= self.MAX_NO_ALERT:
                    self.state.fail(SeatQueryNoResponse(self.class_id))
                    return
                need_seed = True
            except (requests.RequestException, PageChanged, InvalidPostback) as error:
                errors += 1
                log.warning("課程%s: 請求失敗 (第%s次): %s", self.class_id, errors, error)
                if errors >= self.MAX_ERRORS:
                    self.state.fail(error)
                    return
                need_seed = True
                stop.wait(min(errors, 5))
            except Exception as error:  # pylint: disable=broad-except
                log.exception("課程%s: 未預期的錯誤", self.class_id)
                self.state.fail(error)
                return
            stop.wait(max(0.0, self.interval - (time.monotonic() - started)))


LABEL3_ID = "ctl00_MainContent_TabContainer1_tabSelected_Label3"
TBSUBID_ID = F_SUBID.replace("$", "_")
GETSUB_ID = F_GETSUB.replace("$", "_")
ROW_XPATH = f"//*[@id='{GRID_ID}']/tbody/tr[2]"


def prime_session(driver, first_id: str) -> None:
    """Render the add/drop grid once in the browser before the HTTP handoff.

    The AjaxControlToolkit TabContainer renders the tabSelected controls but
    leaves them server-side inert until the tab is activated by a real postback
    in the session; only then do HTTP btnGetSub posts return the course grid.
    This mirrors the first iteration of the proven Selenium auto_class flow.
    """
    from selenium.common import TimeoutException
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as ec
    from selenium.webdriver.support.ui import WebDriverWait

    row = (By.XPATH, ROW_XPATH)
    WebDriverWait(driver, 10).until(
        ec.element_to_be_clickable((By.ID, LABEL3_ID))).click()
    box = WebDriverWait(driver, 10).until(
        ec.presence_of_element_located((By.ID, TBSUBID_ID)))
    box.clear()
    box.send_keys(first_id)
    try:
        WebDriverWait(driver, 5).until(ec.presence_of_element_located(row))
    except TimeoutException:
        try:  # checkLength did not auto-submit; click 查詢 ourselves
            driver.find_element(By.ID, GETSUB_ID).click()
            WebDriverWait(driver, 10).until(ec.presence_of_element_located(row))
        except TimeoutException as error:
            raise PageChanged("瀏覽器暖身查詢未渲染課程列, 選課系統頁面可能已改版") from error
    try:  # a seat-count alert may pop once the grid renders; dismiss it
        WebDriverWait(driver, 3).until(ec.alert_is_present())
        driver.switch_to.alert.accept()
    except TimeoutException:
        pass
    log.info("已透過瀏覽器暖身選課分頁 (課號 %s), HTTP 引擎接手", first_id)


def run(driver, config: dict, notify: Callable[[str], None], dry_run: bool = False) -> None:
    """Hand the Selenium login over to requests and poll every course in parallel.

    Returns normally when every course has been added. Raises the failure of
    the first worker that gave up (RegistrationPhase, SessionExpired,
    SeatQueryNoResponse, network error...) so app.main() can restart.
    """
    class_ids = config["class_ids"]
    ids = [c for c in dict.fromkeys(class_ids) if c]
    interval = float(config.get("query_interval", 0.5))
    state = SharedState(class_ids=class_ids, notify=notify)
    prime_session(driver, ids[0])  # activate the tabSelected tab so HTTP lookups work
    login = LoginSession.from_selenium(driver, pool_size=len(ids))
    try:
        login.get_page()  # proves the cookie handoff worked (else SessionExpired)
        log.info("HTTP 引擎啟動: %s 門課程各一執行緒, 每門課查詢間隔 %.2f 秒%s",
                 len(ids), interval, " [DRY-RUN 只查名額不加選]" if dry_run else "")
        workers = [CourseWorker(login, cid, state, interval, dry_run) for cid in ids]
        for worker in workers:
            worker.start()
        while any(w.is_alive() for w in workers):
            try:
                kind, payload = state.events.get(timeout=0.5)  # short timeout keeps Ctrl+C alive
            except queue.Empty:
                continue
            if kind == "error":
                state.stop.set()
                for worker in workers:
                    worker.join(timeout=5)
                raise payload
        while True:  # an error queued by the very last worker
            try:
                kind, payload = state.events.get_nowait()
            except queue.Empty:
                break
            if kind == "error":
                raise payload
    finally:
        state.stop.set()
        login.close()
