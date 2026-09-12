"""Offline tests for http_engine: parsers and worker cycles with a fake session."""
import tempfile
import unittest

import http_engine as eng

GRID = eng.GRID_ID
MSG = eng.MSG_ID
QUOTA_NAME = "ctl00$MainContent$TabContainer1$tabSelected$gvToAdd$ctl02$btnQuota"
ADD_TARGET = "ctl00$MainContent$TabContainer1$tabSelected$gvToAdd"


def page(alert=None, msg="", with_row=True, logged_in=True, viewstate="VS+1/2=="):
    """Build a minimal ASP.NET-looking course page."""
    row = f"""
<table id="{GRID}" cellspacing="0" rules="all" border="1">
  <tr><th>加選</th><th>課程代碼</th><th>科目名稱</th><th>學分</th><th>開課系所</th><th>時間</th><th>教室</th><th>名額</th></tr>
  <tr>
    <td><input type="button" value="加選" onclick="javascript:__doPostBack(&#39;{ADD_TARGET}&#39;,&#39;addCourse$0&#39;)" /></td>
    <td><font>0050</font></td><td>資料結構</td><td>3</td><td>資訊系</td><td>(一) 1-2</td><td>資電234</td>
    <td><input type="submit" name="{QUOTA_NAME}" value="查詢名額" id="x_btnQuota" /></td>
  </tr>
</table>""" if with_row else ""
    logout = (f'<input type="submit" name="ctl00$btnLogout" value="登出" id="{eng.LOGOUT_ID}" />'
              if logged_in else "")
    script = f"""<script type="text/javascript">
//<![CDATA[
alert('{alert}');//]]>
</script>""" if alert else ""
    return f"""<html><body>
<form method="post" action="./NetPreSelect.aspx?guid=abc&amp;lang=zh-tw" id="aspnetForm">
<input type="hidden" name="__EVENTTARGET" id="__EVENTTARGET" value="" />
<input type="hidden" name="__EVENTARGUMENT" id="__EVENTARGUMENT" value="" />
<input type="hidden" name="__VIEWSTATE" id="__VIEWSTATE" value="{viewstate}" />
<input type="hidden" name="__VIEWSTATEGENERATOR" id="__VIEWSTATEGENERATOR" value="CA0B0334" />
<input type="hidden" name="__EVENTVALIDATION" id="__EVENTVALIDATION" value="EV&amp;x" />
<input type="hidden" name="ctl00_MainContent_TabContainer1_ClientState" id="ctl00_MainContent_TabContainer1_ClientState" value="{{&quot;ActiveTabIndex&quot;:0}}" />
{logout}
<input name="{eng.F_SUBID}" type="text" id="ctl00_MainContent_TabContainer1_tabSelected_tbSubID" />
{row}
<span id="{MSG}"><span style="color:Red;">{msg}</span></span>
</form>
{script}
</body></html>"""


LOGIN_PAGE = (f'<html><body><form><input name="ctl00$Login1$UserName" '
              f'id="{eng.LOGIN_USER_ID}" /></form></body></html>')


class ParserTests(unittest.TestCase):
    def test_hidden_fields_are_unescaped(self):
        fields = eng.parse_hidden_fields(page())
        self.assertEqual(fields["__VIEWSTATE"], "VS+1/2==")
        self.assertEqual(fields["__VIEWSTATEGENERATOR"], "CA0B0334")
        self.assertEqual(fields["__EVENTVALIDATION"], "EV&x")
        self.assertEqual(fields["ctl00_MainContent_TabContainer1_ClientState"],
                         '{"ActiveTabIndex":0}')
        self.assertEqual(fields["__EVENTTARGET"], "")

    def test_alerts(self):
        self.assertEqual(eng.parse_alerts(page(alert="剩餘名額/開放名額：3 / 60")),
                         ["剩餘名額/開放名額：3 / 60"])
        self.assertEqual(eng.parse_alerts("""alert("a"); alert('it\\'s'); alert(msg);"""),
                         ["a", "it's"])
        self.assertEqual(eng.parse_alerts(page()), [])

    def test_quota(self):
        self.assertEqual(eng.parse_quota("剩餘名額/開放名額：3 / 60"), (3, 60))
        self.assertEqual(eng.parse_quota("剩餘名額/開放名額： 0 /60"), (0, 60))
        self.assertIsNone(eng.parse_quota("查無此課程"))
        self.assertIsNone(eng.parse_quota("登記人數/開放名額：5 / 60"))
        self.assertIn(eng.REGISTRATION_MARK, "登記人數/開放名額：5 / 60")

    def test_course_row_buttons(self):
        row = eng.parse_course_row(page())
        self.assertEqual(row.cells[1], "0050")
        self.assertTrue(row.mentions("0050"))
        self.assertFalse(row.mentions("0051"))
        self.assertEqual(row.add, eng.ButtonSpec(ADD_TARGET, "addCourse$0"))
        self.assertEqual(row.add.payload(),
                         {"__EVENTTARGET": ADD_TARGET, "__EVENTARGUMENT": "addCourse$0"})
        self.assertEqual(row.quota, eng.ButtonSpec(QUOTA_NAME, "查詢名額", by_name=True))
        self.assertEqual(row.quota.payload(),
                         {"__EVENTTARGET": "", "__EVENTARGUMENT": "", QUOTA_NAME: "查詢名額"})
        self.assertIsNone(eng.parse_course_row(page(with_row=False)))

    def test_image_button_payload(self):
        spec = eng.ButtonSpec.from_input('<input type="image" name="a$b" src="q.gif" />')
        self.assertEqual(spec.payload(),
                         {"__EVENTTARGET": "", "__EVENTARGUMENT": "", "a$b.x": "1", "a$b.y": "1"})

    def test_msg_block(self):
        self.assertEqual(eng.parse_msg_block(page(msg="加選成功")), "加選成功")
        self.assertEqual(eng.parse_msg_block(page()), "")

    def test_login_detection(self):
        self.assertTrue(eng.is_logged_in(page()))
        self.assertFalse(eng.is_logged_in(page(logged_in=False)))
        self.assertTrue(eng.is_login_page("https://course.fcu.edu.tw/Login.aspx", ""))
        self.assertTrue(eng.is_login_page("https://x/NetPreSelect.aspx", LOGIN_PAGE))
        self.assertFalse(eng.is_login_page("https://x/NetPreSelect.aspx?guid=1", page()))
        self.assertTrue(eng.is_invalid_postback(500, ""))
        self.assertTrue(eng.is_invalid_postback(200, "Invalid postback or callback argument"))
        self.assertFalse(eng.is_invalid_postback(200, page()))


class FakeLogin:
    """Stands in for LoginSession: records payloads, replays canned pages.

    When the canned responses run out it sets ``stop`` (if given) and keeps
    returning the last page, so a worker's run() loop ends deterministically.
    """

    def __init__(self, responses, stop=None):
        self.responses = list(responses)
        self.stop = stop
        self.posts = []

    def get_page(self):
        return page()

    def post(self, fields):
        self.posts.append(dict(fields))
        if len(self.responses) > 1:
            return self.responses.pop(0)
        if self.stop is not None:
            self.stop.set()
        return self.responses[0]


class WorkerTests(unittest.TestCase):
    def make(self, responses, class_ids=("0050", "0051"), dry_run=False):
        messages = []
        state = eng.SharedState(class_ids=list(class_ids), notify=messages.append)
        login = FakeLogin(responses, stop=state.stop)
        worker = eng.CourseWorker(login, "0050", state, interval=0, dry_run=dry_run,
                                  log_dir=tempfile.mkdtemp())
        return worker, login, state, messages

    def test_lookup_then_query_uses_own_viewstate_chain(self):
        worker, login, _, _ = self.make([page(viewstate="VS-after-lookup"),
                                         page(alert="剩餘名額/開放名額：0 / 60")])
        worker.reseed()
        worker.lookup()
        self.assertEqual(worker.query_seats(), (0, 60))
        lookup, query = login.posts
        self.assertEqual(lookup[eng.F_SUBID], "0050")
        self.assertEqual(lookup[eng.F_GETSUB], "查詢")
        self.assertEqual(lookup["__EVENTTARGET"], "")
        self.assertEqual(lookup["__VIEWSTATE"], "VS+1/2==")
        self.assertEqual(query["__VIEWSTATE"], "VS-after-lookup")
        self.assertEqual(query[QUOTA_NAME], "查詢名額")
        self.assertNotIn(eng.F_GETSUB, query)
        self.assertEqual(query[eng.F_SUBID], "0050")

    def test_activate_tab_forces_add_drop_tab(self):
        self.assertEqual(eng.activate_tab({})[eng.CLIENT_STATE_FIELD],
                         '{"ActiveTabIndex":1,"TabState":[true,true,true]}')
        self.assertEqual(
            eng.activate_tab({eng.CLIENT_STATE_FIELD: '{"ActiveTabIndex":0,"TabState":[true,false]}'})
            [eng.CLIENT_STATE_FIELD],
            '{"ActiveTabIndex":1,"TabState":[true,false]}')
        # a corrupt value is replaced, not crashed on
        self.assertEqual(eng.activate_tab({eng.CLIENT_STATE_FIELD: "junk"})[eng.CLIENT_STATE_FIELD],
                         '{"ActiveTabIndex":1,"TabState":[true,true,true]}')

    def test_posts_activate_the_tab(self):
        worker, login, _, _ = self.make([page(viewstate="v2"),
                                         page(alert="剩餘名額/開放名額：0 / 60")])
        worker.reseed()
        worker.lookup()
        worker.query_seats()
        for sent in login.posts:
            self.assertEqual(sent[eng.CLIENT_STATE_FIELD],
                             '{"ActiveTabIndex":1,"TabState":[true,true,true]}')

    def test_run_adds_when_seats_open(self):
        worker, login, state, messages = self.make([
            page(), page(alert="剩餘名額/開放名額：3 / 60"), page(msg="加選成功")])
        worker.run()
        self.assertEqual(state.class_ids, ["0051"])
        self.assertEqual(messages, ["✅ 成功加選課程：0050\n尚待加選：0051"])
        self.assertEqual(state.events.get_nowait(), ("done", "0050"))
        self.assertEqual(len(login.posts), 3)
        add = login.posts[2]
        self.assertEqual(add["__EVENTTARGET"], ADD_TARGET)
        self.assertEqual(add["__EVENTARGUMENT"], "addCourse$0")
        self.assertEqual(add[eng.F_SUBID], "0050")
        self.assertNotIn(QUOTA_NAME, add)

    def test_run_dry_run_never_adds(self):
        worker, login, state, messages = self.make(
            [page(), page(alert="剩餘名額/開放名額：3 / 60")], dry_run=True)
        worker.run()  # FakeLogin sets stop after the seat query
        self.assertEqual(len(login.posts), 2)
        self.assertEqual(state.class_ids, ["0050", "0051"])
        self.assertEqual(messages, [])
        self.assertTrue(state.events.empty())

    def test_registration_phase_stops_everything(self):
        worker, _, state, _ = self.make([page(), page(alert="登記人數/開放名額：5 / 60")])
        worker.run()
        kind, error = state.events.get_nowait()
        self.assertEqual(kind, "error")
        self.assertIsInstance(error, eng.RegistrationPhase)
        self.assertTrue(state.stop.is_set())

    def test_three_no_alerts_raise_seat_query_no_response(self):
        worker, login, state, _ = self.make([page(), page(alert="查無此課程")] * 3
                                            + [page(alert="查無此課程")],
                                            class_ids=("0050",))
        worker.run()
        kind, error = state.events.get_nowait()
        self.assertEqual(kind, "error")
        self.assertIsInstance(error, eng.SeatQueryNoResponse)
        self.assertEqual(len(login.posts), 6)  # 3 x (lookup + query)


if __name__ == "__main__":
    unittest.main()
