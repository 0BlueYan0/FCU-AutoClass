"""One-shot discovery for the HTTP engine.

Logs in with the normal Selenium + OCR flow, then:
  1. prints the form fields / textbox handlers / lookup button of the live page,
  2. types the first course id, prints the gvToAdd row and its two <input>s,
  3. hooks __doPostBack / XHR / form.submit, clicks the seat-query button,
     prints the alert text and what the browser actually posted,
  4. replays the same cycle over plain HTTP with http_engine (GET, lookup
     POST, seat-query POST) and prints what came back.
It NEVER sends the add-course postback. Everything is saved under
logs/discovery-<timestamp>/ (that folder contains personal data; review it
before sharing or copying into tests/fixtures/).

Usage (Windows):
    set PYTHONUTF8=1
    .venv\\Scripts\\python discover_form.py
"""
import logging
import os
import re
import sys
import time
from urllib.parse import parse_qsl

from selenium.common import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as ec
from selenium.webdriver.support.ui import WebDriverWait

import app
import http_engine as eng
import utilities as utils

OUT_DIR = time.strftime("./logs/discovery-%Y%m%d-%H%M%S")
TB_SUBID_ID = eng.F_SUBID.replace("$", "_")
CLIENT_STATE_ID = "ctl00_MainContent_TabContainer1_ClientState"
ROW_XPATH = f"//*[@id='{eng.GRID_ID}']/tbody/tr[2]"
ADD_XPATH = f"{ROW_XPATH}/td[{eng.ADD_TD}]/input"
QUOTA_XPATH = f"{ROW_XPATH}/td[{eng.QUOTA_TD}]/input"

HOOK_JS = r"""
sessionStorage.removeItem('cap');
var cap = function (k, v) {
  sessionStorage.setItem('cap', (sessionStorage.getItem('cap') || '') + k + '\t' + v + '\n');
};
if (window.__doPostBack && !window.__dpbHooked) {
  var dpb = window.__doPostBack;
  window.__doPostBack = function (t, a) { cap('doPostBack', t + '|' + a); return dpb(t, a); };
  window.__dpbHooked = true;
}
if (!HTMLFormElement.prototype.__submitHooked) {
  var sub = HTMLFormElement.prototype.submit;
  HTMLFormElement.prototype.submit = function () {
    cap('formsubmit', new URLSearchParams(new FormData(this)).toString());
    return sub.call(this);
  };
  HTMLFormElement.prototype.__submitHooked = true;
}
if (!XMLHttpRequest.prototype.__sendHooked) {
  var send = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function (b) { cap('xhr', String(b)); return send.call(this, b); };
  XMLHttpRequest.prototype.__sendHooked = true;
}
document.forms[0].addEventListener('submit', function () {
  cap('submit', new URLSearchParams(new FormData(document.forms[0])).toString());
});
"""


def section(title):
    print("\n" + "=" * 72 + "\n" + title + "\n" + "=" * 72)


def js(script, *args):
    return app.driver.execute_script(script, *args)


def save(name, text):
    path = os.path.join(OUT_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text or "")
    print(f"  已儲存 {path}")
    return path


def scrub(page, username):
    """Replace ViewState blobs and the student id so the page can be a test fixture."""
    for key, dummy in (("__VIEWSTATE", "VIEWSTATE"), ("__EVENTVALIDATION", "EVENTVALIDATION"),
                       ("__VIEWSTATEGENERATOR", "GEN")):
        page = re.sub(r'(id="%s" value=")[^"]*(")' % key, r"\g<1>%s\2" % dummy, page)
    if username:
        page = page.replace(username, "STUDENT")
    return page


def short(value, limit=60):
    value = value or ""
    return value if len(value) <= limit else f"{value[:40]}...(len={len(value)})"


def step_login():
    section("1. 登入 (Selenium + OCR)")
    app.login()
    print("current_url:", app.driver.current_url)
    print("userAgent  :", js("return navigator.userAgent"))
    for cookie in app.driver.get_cookies():
        print(f"  cookie {cookie['name']}  domain={cookie.get('domain')}  "
              f"path={cookie.get('path')}  value_len={len(cookie['value'])}")


def step_form(summary):
    section("2. 表單欄位 (只列 tabSelected / __* / ScriptManager 相關)")
    elements = js("""
        return Array.from(document.forms[0].elements).map(function (e) {
          return [e.name, e.type, e.id, (e.value || '').length,
                  e.getAttribute('onclick'), e.getAttribute('onchange'),
                  e.getAttribute('onkeyup'), e.getAttribute('onkeypress')];
        });""")
    print(f"  表單共 {len(elements)} 個欄位")
    for name, typ, eid, vlen, onclick, onchange, onkeyup, onkeypress in elements:
        label = name or eid or ""
        if not (label.startswith("__") or "tabSelected" in label or "ScriptManager" in label
                or "ClientState" in label):
            continue
        handlers = {k: v for k, v in (("onclick", onclick), ("onchange", onchange),
                                      ("onkeyup", onkeyup), ("onkeypress", onkeypress)) if v}
        print(f"  {typ or '?':8s} {label}  value_len={vlen}  {handlers if handlers else ''}")
    tb_html = js("var e = document.getElementById(arguments[0]); return e ? e.outerHTML : null",
                 TB_SUBID_ID)
    has_getsub = js("return document.getElementsByName(arguments[0]).length", eng.F_GETSUB)
    client_state = js("var e = document.getElementById(arguments[0]); return e ? e.value : null",
                      CLIENT_STATE_ID)
    print("\n  tbSubID  :", tb_html)
    print("  btnGetSub 存在:", bool(has_getsub))
    print("  TabContainer ClientState (瀏覽器端 JS 設定的值):", client_state)
    summary["tbSubID"] = tb_html
    summary["btnGetSub"] = bool(has_getsub)
    summary["client_state"] = client_state
    m = eng._DOPOSTBACK_RE.search(tb_html or "")  # pylint: disable=protected-access
    if not has_getsub and m:
        eng.LOOKUP = eng.ButtonSpec(m.group(2), m.group(4))
        print(f"  -> 沒有 btnGetSub 但 tbSubID 自帶 __doPostBack, 改用 LOOKUP = {eng.LOOKUP}")
    summary["lookup"] = eng.LOOKUP


def step_type_course(class_id, summary):
    section(f"3. 輸入課號 {class_id}, 取得 gvToAdd 列 (照 app.py 的流程)")
    app.driver_click((By.ID, "ctl00_MainContent_TabContainer1_tabSelected_Label3"))
    app.driver_send_keys((By.ID, TB_SUBID_ID), class_id)
    WebDriverWait(app.driver, 10).until(ec.presence_of_element_located((By.XPATH, QUOTA_XPATH)))
    grid = js("return document.getElementById(arguments[0]).outerHTML", eng.GRID_ID)
    save("grid.html", grid)
    for label, xpath in (("td[1] 加選按鈕", ADD_XPATH), ("td[8] 名額按鈕", QUOTA_XPATH)):
        try:
            print(f"  {label}:", app.driver.find_element(By.XPATH, xpath).get_attribute("outerHTML"))
        except Exception as error:  # pylint: disable=broad-except
            print(f"  {label}: 找不到 ({error.__class__.__name__})")
    row = eng.parse_course_row(grid)
    print("  parse_course_row(瀏覽器 DOM) ->")
    print("     cells:", row.cells if row else None)
    print("     add  :", row.add if row else None)
    print("     quota:", row.quota if row else None)
    summary["dom_row"] = row


def step_click_quota(summary):
    section("4. 掛上 __doPostBack / form.submit / XHR hook 後點名額按鈕")
    js(HOOK_JS)
    app.driver_click((By.XPATH, QUOTA_XPATH))
    text = None
    try:
        WebDriverWait(app.driver, 10).until(ec.alert_is_present())
        alert = app.driver.switch_to.alert
        text = alert.text
        print("  alert.text:", text)
        alert.accept()
    except TimeoutException:
        print("  !! 10 秒內沒有 alert")
    WebDriverWait(app.driver, 10).until(
        lambda d: d.execute_script("return document.readyState") == "complete")
    captured = js("return sessionStorage.getItem('cap')") or ""
    print("  瀏覽器實際送出的內容:")
    for line in captured.splitlines():
        kind, _, body = line.partition("\t")
        if kind in ("formsubmit", "submit", "xhr"):
            fields = parse_qsl(body, keep_blank_values=True)
            print(f"   [{kind}] {len(fields)} 個欄位:")
            for key, value in fields:
                print(f"      {key} = {short(value)}")
        else:
            print(f"   [{kind}] {body}")
    if not captured:
        print("   (沒攔到任何東西: 可能按鈕是原生 submit 且 hook 太晚)")
    summary["async_postback"] = "xhr\t" in captured
    if summary["async_postback"]:
        print("  -> 頁面用 UpdatePanel 非同步 postback (XHR); 若步驟 5 的整頁 POST 拿不到 alert, "
              "需要實作非同步變體")
    page = app.driver.page_source
    save("after_alert.html", page)
    print("  page_source 內的 alert(...):", eng.parse_alerts(page))
    after_row = eng.parse_course_row(page)
    print("  名額查詢後頁面仍有課程列:", after_row is not None and after_row.quota is not None)
    return text


def step_http_replay(config, class_id, summary):
    section("5. 用 requests 重放同一循環 (GET -> lookup POST -> 名額 POST), 不送加選")
    login = eng.LoginSession.from_selenium(app.driver, pool_size=2)
    state = eng.SharedState(class_ids=[class_id], notify=lambda _m: None)
    worker = eng.CourseWorker(login, class_id, state, interval=1.0, dry_run=True, log_dir=OUT_DIR)
    username = config.get("username") or ""

    def report(name):
        response = login.last_response
        page = eng._decode(response) if response is not None else ""  # pylint: disable=protected-access
        if response is not None:
            print(f"  HTTP {response.status_code}  url={response.url}")
            print(f"  Content-Type={response.headers.get('Content-Type')}  encoding={response.encoding}")
        print("  alert(...) :", eng.parse_alerts(page))
        print("  marks      :", {m: (m in page) for m in ("剩餘名額", "登記人數", eng.LOGOUT_ID,
                                                            "Invalid postback")})
        save(name, page)
        save(os.path.join("fixtures", name), scrub(page, username))
        return page

    os.makedirs(os.path.join(OUT_DIR, "fixtures"), exist_ok=True)
    try:
        worker.reseed()
        print("  GET 成功, cookie 交接 OK. hidden fields:",
              {k: len(v) for k, v in worker.fields.items()})
        summary["handoff"] = True
    except eng.SessionExpired as error:
        print("  !! GET 後不是登入狀態 -> cookie 交接失敗:", error)
        summary["handoff"] = False
    report("get.html")
    if not summary["handoff"]:
        return

    print("\n  lookup 使用:", eng.LOOKUP)
    try:
        worker.lookup()
        print("  lookup 成功: cells =", worker.row.cells)
        print("     add   =", worker.row.add)
        print("     quota =", worker.row.quota)
        summary["http_row"] = worker.row
    except (eng.PageChanged, eng.InvalidPostback, eng.SessionExpired) as error:
        print("  !! lookup 失敗:", error)
        print("     若步驟 2 顯示 tbSubID 有 onchange=__doPostBack(...), 請把 http_engine.LOOKUP "
              "改成 ButtonSpec(F_SUBID, '')")
    report("lookup.html")
    if worker.row is None:
        return

    print()
    try:
        remain, total = worker.query_seats()
        print(f"  名額 POST 成功: 剩餘名額 {remain} / 開放名額 {total}")
        summary["http_quota"] = (remain, total)
    except eng.NoAlert as error:
        print("  !! 名額 POST 沒有名額訊息, alerts =", error.args)
    except eng.RegistrationPhase as error:
        print("  目前為登記階段:", error)
    except (eng.PageChanged, eng.InvalidPostback, eng.SessionExpired) as error:
        print("  !! 名額 POST 失敗:", error)
    page = report("quota.html")
    row = eng.parse_course_row(page)
    print("  名額 POST 回應仍含課程列 (穩態每輪只需一個 POST):",
          row is not None and row.quota is not None and row.add is not None)
    login.close()


def step_summary(summary):
    section("6. 總結")
    print("  lookup 變體        :", summary.get("lookup"))
    print("  btnGetSub 存在      :", summary.get("btnGetSub"))
    print("  非同步 postback (XHR):", summary.get("async_postback"))
    print("  瀏覽器 alert 文字   :", summary.get("alert"))
    print("  cookie 交接         :", summary.get("handoff"))
    row = summary.get("http_row") or summary.get("dom_row")
    print("  add ButtonSpec      :", row.add if row else None)
    print("  quota ButtonSpec    :", row.quota if row else None)
    print("  HTTP 名額結果        :", summary.get("http_quota"))
    print("  ClientState         :", short(summary.get("client_state") or "", 80))
    print(f"\n  輸出資料夾 {OUT_DIR} 含個資 (學號/姓名); fixtures/ 已去除 ViewState 與學號, "
          "但姓名需自行檢查後再複製到 tests/fixtures/")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    os.makedirs(OUT_DIR, exist_ok=True)
    config = utils.read_config()
    class_id = config["class_ids"][0]
    app.config = dict(config, headless=False)
    print(f"輸出資料夾: {OUT_DIR}")
    app.driver = app.make_driver()
    summary = {}
    try:
        step_login()
        step_form(summary)
        step_type_course(class_id, summary)
        summary["alert"] = step_click_quota(summary)
        step_http_replay(config, class_id, summary)
        step_summary(summary)
    finally:
        try:
            input("\n按 Enter 關閉瀏覽器並結束...")
        except EOFError:
            pass
        app.quit_driver()


if __name__ == "__main__":
    main()
