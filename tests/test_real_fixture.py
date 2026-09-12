"""Parsers and one worker cycle against markup trimmed from a real seat-query response."""
import os
import tempfile
import unittest

import http_engine as eng

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "quota_response.html")
GRID = "ctl00$MainContent$TabContainer1$tabSelected$gvToAdd"


def load():
    with open(FIXTURE, encoding="utf-8") as f:
        return f.read()


class RealMarkupTests(unittest.TestCase):
    def setUp(self):
        self.page = load()

    def test_hidden_fields_present(self):
        fields = eng.parse_hidden_fields(self.page)
        for name in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION",
                     "__EVENTTARGET", "__EVENTARGUMENT", "__LASTFOCUS", "__VIEWSTATEENCRYPTED",
                     "ctl00_MainContent_TabContainer1_ClientState"):
            self.assertIn(name, fields)
        self.assertEqual(fields["__VIEWSTATE"], "VIEWSTATE-DUMMY")

    def test_alert_inside_settimeout_and_double_space(self):
        alerts = eng.parse_alerts(self.page)
        self.assertEqual(alerts, ["剩餘名額/開放名額：0  /40"])
        self.assertEqual(eng.parse_quota(alerts[0]), (0, 40))

    def test_row_buttons_are_dopostback_with_prefix_js(self):
        row = eng.parse_course_row(self.page)
        self.assertTrue(row.mentions("3157"))
        self.assertEqual(row.add, eng.ButtonSpec(GRID, "addCourse$0"))
        self.assertEqual(row.quota, eng.ButtonSpec(GRID, "selquota$0"))

    def test_lookup_controls_and_session_markers(self):
        self.assertTrue(eng.is_logged_in(self.page))
        self.assertFalse(eng.is_login_page("https://x/NetPreSelect.aspx?guid=1", self.page))
        self.assertFalse(eng.is_invalid_postback(200, self.page))
        self.assertIn('name="%s"' % eng.F_SUBID, self.page)
        self.assertIn('name="%s"' % eng.F_GETSUB, self.page)
        self.assertEqual(eng.parse_msg_block(self.page), "")

    def test_worker_cycle_on_real_markup(self):
        class FakeLogin:
            def __init__(self, page):
                self.page = page
                self.posts = []

            def get_page(self):
                return self.page

            def post(self, fields):
                self.posts.append(dict(fields))
                return self.page

        login = FakeLogin(self.page)
        state = eng.SharedState(class_ids=["3157"], notify=lambda _m: None)
        worker = eng.CourseWorker(login, "3157", state, interval=0, dry_run=True,
                                  log_dir=tempfile.mkdtemp())
        worker.reseed()
        worker.lookup()
        self.assertEqual(worker.query_seats(), (0, 40))
        lookup, query = login.posts
        self.assertEqual(lookup[eng.F_GETSUB], "查詢")
        self.assertEqual(lookup[eng.F_SUBID], "3157")
        self.assertEqual(query["__EVENTTARGET"], GRID)
        self.assertEqual(query["__EVENTARGUMENT"], "selquota$0")
        self.assertEqual(query["ctl00_MainContent_TabContainer1_ClientState"],
                         eng.parse_hidden_fields(self.page)["ctl00_MainContent_TabContainer1_ClientState"])
        self.assertNotIn(eng.F_GETSUB, query)


if __name__ == "__main__":
    unittest.main()
