"""skills/xubao/scripts/remind.py 的测试（unittest，标准库零依赖）。

运行：python -m unittest discover -s tests/xubao -p "test_*.py"

案号由碎片拼出：本文件自己也要过隐私钩子。
"""
import datetime as dt
import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO / "skills" / "xubao" / "scripts" / "remind.py"


def load_module():
    spec = importlib.util.spec_from_file_location("remind", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def j(*parts):
    return "".join(parts)


def text_of(raw):
    """脚本输出解码；Windows 的 stdout 会把换行转成 CRLF，统一回 LF。"""
    return raw.decode("utf-8").replace("\r\n", "\n")


CASE = j("（2026）", "测01", "执保", "1号")
CASE2 = j("（2026）", "测01", "执保", "2号")
NOW = "2026-10-08 21:00"

DEPOSIT = {"案号": CASE, "法院": "测试市测试区人民法院", "申请人": "甲测试", "被申请人": "乙测试",
           "财产": "丙测试银行测试支行账号 6222 0000 0000 0000 的存款", "类型": "银行存款",
           "实施日": "2026-03-02", "届满日": "2027-03-01"}
HOUSE = {"案号": CASE, "财产": "测试市测试区测试路1号房产", "类型": "不动产",
         "实施日": "2026-03-03", "届满日": "2029-03-02"}
CAR = {"案号": CASE, "财产": "车牌号测A00000的小型普通客车", "类型": "机动车", "实施日": "2026-03-05"}


class Units(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = load_module()

    def test_infer_expiry_counts_start_day(self):
        d = dt.date
        self.assertEqual(self.m.infer_expiry(d(2026, 3, 5), 24), d(2028, 3, 4))
        self.assertEqual(self.m.infer_expiry(d(2026, 3, 2), 12), d(2027, 3, 1))
        self.assertEqual(self.m.infer_expiry(d(2024, 2, 29), 12), d(2025, 2, 27), "对应日不存在取月末再前一日")
        self.assertEqual(self.m.infer_expiry(d(2026, 8, 31), 6), d(2027, 2, 27))
        self.assertEqual(self.m.infer_expiry(d(2026, 1, 1), 36), d(2028, 12, 31))

    def test_deadline_is_seven_days_before_expiry(self):
        self.assertEqual(self.m.filing_deadline(dt.date(2027, 3, 1)), dt.date(2027, 2, 22))

    def now(self, text):
        return dt.datetime.strptime(text, "%Y-%m-%d %H:%M")

    def test_schedule_two_tiers_three_times(self):
        status, points, skipped = self.m.schedule(dt.date(2027, 3, 1), self.now(NOW))
        self.assertEqual(status, "正常")
        self.assertEqual(skipped, 0)
        self.assertEqual([p.strftime("%Y-%m-%d %H:%M") for p in points], [
            "2027-01-23 10:00", "2027-01-23 12:00", "2027-01-23 17:00",
            "2027-02-15 10:00", "2027-02-15 12:00", "2027-02-15 17:00"])

    def test_schedule_skips_past_points(self):
        # 截止日 2026-10-29：前 30 天那一档已过
        status, points, skipped = self.m.schedule(dt.date(2026, 11, 5), self.now(NOW))
        self.assertEqual((status, skipped, len(points)), ("正常", 3, 3))
        status, points, skipped = self.m.schedule(dt.date(2026, 11, 5), self.now("2026-10-22 11:00"))
        self.assertEqual((status, skipped), ("正常", 4))
        self.assertEqual(points[0], self.now("2026-10-22 12:00"))

    def test_schedule_urgent_takes_next_slot_today(self):
        # 截止日 2026-10-12，前 7 天那档在 10-05，已过
        status, points, _ = self.m.schedule(dt.date(2026, 10, 19), self.now("2026-10-08 11:00"))
        self.assertEqual(status, "紧急")
        self.assertEqual(points, [self.now("2026-10-08 12:00")])

    def test_schedule_urgent_after_last_slot(self):
        status, points, _ = self.m.schedule(dt.date(2026, 10, 19), self.now("2026-10-08 21:03"))
        self.assertEqual(status, "紧急")
        self.assertEqual(points, [self.now("2026-10-08 21:20")])

    def test_schedule_deadline_day_itself_is_not_expired(self):
        status, points, _ = self.m.schedule(dt.date(2026, 10, 15), self.now("2026-10-08 09:00"))
        self.assertEqual(status, "紧急")
        self.assertEqual(points, [self.now("2026-10-08 10:00")])

    def test_schedule_expired(self):
        status, points, _ = self.m.schedule(dt.date(2026, 10, 14), self.now(NOW))
        self.assertEqual((status, points), ("已过期限", []))


class CliCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self._tmp.name)
        self.ledger = self.dir / "保全期限.md"

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, *args):
        proc = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True)
        return proc.returncode, text_of(proc.stdout), text_of(proc.stderr)

    def merge(self, rows, now=NOW):
        path = self.dir / "rows.json"
        path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        return self.run_cli("merge", "--ledger", str(self.ledger), "--rows", str(path), "--now", now)

    def ok(self, result):
        code, out, err = result
        self.assertEqual(code, 0, err + out)
        return out

    @staticmethod
    def payload(out):
        if "```json" not in out:
            return None
        return json.loads(out.split("```json\n", 1)[1].split("\n```", 1)[0])

    def ledger_text(self):
        return self.ledger.read_text(encoding="utf-8")


class MergeTests(CliCase):
    def test_first_run_writes_ledger_and_three_batches(self):
        out = self.ok(self.merge([DEPOSIT, HOUSE, CAR]))
        data = self.payload(out)
        self.assertEqual((data["format"], data["list"], data["void"]), (1, "续保", []))
        ids = [b["id"] for b in data["batches"]]
        self.assertEqual(ids, ["续保识别码：%s@2027-03-01" % CASE, "续保识别码：%s@2029-03-02" % CASE,
                               "续保识别码：%s@2028-03-04" % CASE])
        dep, _, car = data["batches"]
        self.assertEqual(dep["title"], "续保｜甲测试诉乙测试｜截止 2027-02-22")
        self.assertEqual(len(dep["alerts"]), 6)
        self.assertTrue(dep["notes"].endswith(dep["id"]), "识别码在备注最后一行")
        self.assertIn("法院：测试市测试区人民法院", dep["notes"])
        self.assertTrue(car["title"].endswith("｜推算待核"))
        self.assertIn("推算", car["notes"])
        self.assertIn("机动车按动产推算是推断", out)
        text = self.ledger_text()
        self.assertIn("| 2028-03-04 | 推算 |", text)
        self.assertIn("| 动产 |", text, "别名落成规范类型")

    def test_same_property_in_one_batch(self):
        other = dict(DEPOSIT, 财产="丁测试银行账号尾号1111的存款")
        data = self.payload(self.ok(self.merge([DEPOSIT, other])))
        self.assertEqual(len(data["batches"]), 1)
        self.assertIn("2. 丁测试银行", data["batches"][0]["notes"])

    def test_rerun_same_rows_exports_nothing(self):
        self.ok(self.merge([DEPOSIT, HOUSE, CAR]))
        before = self.ledger_text()
        out = self.ok(self.merge([DEPOSIT, HOUSE, CAR]))
        self.assertIsNone(self.payload(out))
        self.assertIn("没有新增或变动的批", out)
        self.assertEqual(self.ledger_text(), before)

    def test_key_ignores_spacing_and_width(self):
        self.ok(self.merge([DEPOSIT]))
        squeezed = dict(DEPOSIT, 财产=DEPOSIT["财产"].replace(" ", "").replace("6", "６"))
        out = self.ok(self.merge([squeezed]))
        self.assertIsNone(self.payload(out))
        self.assertEqual(self.ledger_text().count("6222"), 1)

    def test_renewal_overrides_and_voids_old_batch(self):
        self.ok(self.merge([DEPOSIT, HOUSE]))
        renewed = dict(HOUSE, 实施日="2029-02-20", 届满日="2032-02-19")
        out = self.ok(self.merge([renewed]))
        data = self.payload(out)
        self.assertEqual(data["void"], ["续保识别码：%s@2029-03-02" % CASE])
        self.assertEqual([b["id"] for b in data["batches"]], ["续保识别码：%s@2032-02-19" % CASE])
        self.assertIn("原届满日 2029-03-02", self.ledger_text())

    def test_partial_renewal_rebuilds_remaining_batch(self):
        other = dict(DEPOSIT, 财产="丁测试银行账号尾号1111的存款")
        self.ok(self.merge([DEPOSIT, other]))
        out = self.ok(self.merge([dict(other, 届满日="2028-02-28")]))
        data = self.payload(out)
        old = "续保识别码：%s@2027-03-01" % CASE
        self.assertEqual(data["void"], [old], "留下的那批内容变了，先删再建")
        ids = [b["id"] for b in data["batches"]]
        self.assertIn(old, ids)
        self.assertIn("续保识别码：%s@2028-02-28" % CASE, ids)

    def test_older_document_does_not_roll_back(self):
        self.ok(self.merge([DEPOSIT]))
        before = self.ledger_text()
        out = self.ok(self.merge([dict(DEPOSIT, 届满日="2026-12-01")]))
        self.assertIsNone(self.payload(out))
        self.assertIn("看作旧文书", out)
        self.assertEqual(self.ledger_text(), before)

    def test_stated_date_confirms_inferred(self):
        self.ok(self.merge([CAR]))
        out = self.ok(self.merge([dict(CAR, 届满日="2028-03-04")]))
        data = self.payload(out)
        self.assertEqual(data["void"], ["续保识别码：%s@2028-03-04" % CASE])
        self.assertFalse(data["batches"][0]["title"].endswith("推算待核"))
        self.assertIn("| 2028-03-04 | 文书写明 |", self.ledger_text())

    def test_stated_earlier_date_beats_inferred(self):
        self.ok(self.merge([CAR]))
        out = self.ok(self.merge([dict(CAR, 届满日="2027-03-04")]))
        self.assertIn("以文书为准", out)
        self.assertIn("原推算届满日 2028-03-04", self.ledger_text())

    def test_lawyer_edit_survives_rerun(self):
        self.ok(self.merge([CAR]))
        self.ledger.write_text(self.ledger_text().replace("| 2028-03-04 | 推算 |", "| 2028-02-01 | 推算 |"),
                               encoding="utf-8")
        out = self.ok(self.merge([CAR]))
        self.assertIsNone(self.payload(out))
        self.assertIn("| 2028-02-01 | 推算 |", self.ledger_text())

    def test_new_start_date_alone_never_overrides(self):
        self.ok(self.merge([DEPOSIT]))
        out = self.ok(self.merge([{"案号": CASE, "财产": DEPOSIT["财产"], "类型": "银行存款", "实施日": "2027-02-20"}]))
        self.assertIn("底账没改", out)
        self.assertIn("| 2027-03-01 | 文书写明 |", self.ledger_text())

    def test_cleared_cells_are_inferred_again(self):
        self.ok(self.merge([CAR]))
        self.ledger.write_text(self.ledger_text().replace("| 2028-03-04 | 推算 |", "|  |  |"), encoding="utf-8")
        out = self.ok(self.run_cli("export", "--ledger", str(self.ledger), "--now", NOW))
        self.assertIn("| 2028-03-04 | 推算 |", self.ledger_text())
        self.assertIn("保全底账已写", out)

    def test_stated_period_beats_type_table(self):
        row = {"案号": CASE, "财产": "某证券账户资金", "类型": "证券交易结算资金", "实施日": "2026-03-02", "期限": "1年"}
        out = self.ok(self.merge([row]))
        self.assertIn("2027-03-01", self.ledger_text())
        self.assertNotIn("实务有争议", out, "按文书期限推算的不提类型争议")

    def test_securities_inference_mentions_controversy(self):
        row = {"案号": CASE, "财产": "某证券账户资金", "类型": "证券交易结算资金", "实施日": "2026-06-02"}
        out = self.ok(self.merge([row]))
        self.assertIn("2026-12-01", self.ledger_text())
        self.assertIn("实务有争议", out)

    def test_missing_dates_ask_lawyer(self):
        out = self.ok(self.merge([{"案号": CASE, "财产": "某物", "类型": "动产"}]))
        self.assertIn("缺日期", out)
        self.assertIsNone(self.payload(out))

    def test_expired_deadline_warns_and_skips(self):
        out = self.ok(self.merge([dict(DEPOSIT, 届满日="2026-10-14")]))
        self.assertIn("已过法定申请期限", out)
        self.assertIsNone(self.payload(out))

    def test_urgent_batch_warns(self):
        out = self.ok(self.merge([dict(DEPOSIT, 届满日="2026-10-19")]))
        self.assertIn("**紧急", out)
        self.assertEqual(len(self.payload(out)["batches"][0]["alerts"]), 1)

    def test_text_outside_table_is_kept(self):
        self.ok(self.merge([DEPOSIT]))
        self.ledger.write_text(self.ledger_text() + "\n律师自己的笔记\n", encoding="utf-8")
        self.ok(self.merge([HOUSE]))
        self.assertTrue(self.ledger_text().rstrip().endswith("律师自己的笔记"))

    def test_pipe_in_cell_is_made_fullwidth(self):
        self.ok(self.merge([dict(DEPOSIT, 财产="账号 A|B 的存款")]))
        self.assertIn("账号 A｜B 的存款", self.ledger_text())
        self.ok(self.merge([HOUSE]))


class RefuseTests(CliCase):
    def assertRefused(self, result, words):
        code, out, err = result
        self.assertEqual(code, 1, out + err)
        self.assertIn(words, err)
        self.assertEqual(out, "")

    def test_unknown_key(self):
        self.assertRefused(self.merge([dict(DEPOSIT, 来源="文书写明")]), "不认得的字段")

    def test_unknown_type(self):
        self.assertRefused(self.merge([dict(DEPOSIT, 类型="飞机")]), "类型「飞机」不认得")

    def test_bad_date(self):
        self.assertRefused(self.merge([dict(DEPOSIT, 届满日="2027-02-30")]), "不存在")

    def test_missing_required(self):
        self.assertRefused(self.merge([{"案号": CASE, "财产": "某物"}]), "缺 类型")

    def test_refusal_leaves_ledger_untouched(self):
        self.ok(self.merge([DEPOSIT]))
        before = self.ledger_text()
        self.assertRefused(self.merge([HOUSE, dict(CAR, 类型="飞机")]), "不认得")
        self.assertEqual(self.ledger_text(), before)

    def test_changed_header(self):
        self.ok(self.merge([DEPOSIT]))
        self.ledger.write_text(self.ledger_text().replace("| 备注 |", "| 说明 |"), encoding="utf-8")
        self.assertRefused(self.merge([HOUSE]), "表头被改过")

    def test_bad_cell_in_ledger_names_line(self):
        self.ok(self.merge([DEPOSIT]))
        self.ledger.write_text(self.ledger_text().replace("2027-03-01", "明年三月"), encoding="utf-8")
        self.assertRefused(self.merge([HOUSE]), "底账第")

    def test_export_unknown_case(self):
        self.ok(self.merge([DEPOSIT]))
        self.assertRefused(self.run_cli("export", "--ledger", str(self.ledger), "--case", CASE2), "没有这些案号")


class CourtLeadTests(CliCase):
    """法院要求比法定七日更早提交（「到期前两周」）：截止日取更早的那天，识别码不变。"""

    def test_lead_moves_deadline_title_notes_and_alerts(self):
        out = self.ok(self.merge([dict(DEPOSIT, 提前天数="14")]))
        b = self.payload(out)["batches"][0]
        self.assertEqual(b["id"], "续保识别码：%s@2027-03-01" % CASE, "识别码仍是案号加届满日")
        self.assertEqual(b["title"], "续保｜甲测试诉乙测试｜截止 2027-02-15")
        self.assertIn("申请截止日：2027-02-15（法院要求届满前 14 日提交；法定为届满七日前，即 2027-02-22）", b["notes"])
        self.assertEqual(b["alerts"][0], "2027-01-16 10:00")
        self.assertEqual(b["alerts"][-1], "2027-02-08 17:00")
        self.assertIn("2027-02-15（法院要求提前 14 日）", out)
        self.assertIn("| 文书写明 | 14 |", self.ledger_text())

    def test_lead_accepts_day_suffix_and_never_goes_below_seven(self):
        self.ok(self.merge([dict(DEPOSIT, 提前天数="14天")]))
        self.assertIn("| 14 |", self.ledger_text())
        b = self.payload(self.ok(self.merge([dict(HOUSE, 提前天数="3")])))["batches"][0]
        self.assertIn("截止 2029-02-23", b["title"], "比法定七日还晚的要求不算数")

    def test_bad_lead_is_refused(self):
        code, out, err = self.merge([dict(DEPOSIT, 提前天数="两周")])
        self.assertEqual(code, 1)
        self.assertIn("提前天数「两周」认不出", err)

    def test_later_document_with_lead_rebuilds_same_batch(self):
        self.ok(self.merge([DEPOSIT]))
        data = self.payload(self.ok(self.merge([dict(DEPOSIT, 提前天数="14")])))
        ident = "续保识别码：%s@2027-03-01" % CASE
        self.assertEqual(data["void"], [ident], "标题与时点变了：手机上删旧重建")
        self.assertEqual([b["id"] for b in data["batches"]], [ident])
        out = self.ok(self.merge([dict(DEPOSIT, 提前天数="10")]))
        self.assertIsNone(self.payload(out), "更晚的要求不压过更早的")

    def test_past_court_deadline_but_not_legal_is_urgent(self):
        # 届满 2026-10-20：法院要求截止 10-06 已过，法定截止 10-13 未到
        out = self.ok(self.merge([dict(DEPOSIT, 届满日="2026-10-20", 提前天数="14")]))
        self.assertIn("已过法院要求的申请截止日 2026-10-06，法定截止日 2026-10-13 还没到", out)
        self.assertEqual(len(self.payload(out)["batches"][0]["alerts"]), 1)

    def test_legacy_ledger_without_lead_column_is_upgraded(self):
        self.ok(self.merge([DEPOSIT]))
        text = self.ledger_text()
        legacy = (text.replace(" 到期日来源 | 提前天数 |", " 到期日来源 |")
                  .replace("|---|---|---|---|---|---|---|---|---|---|---|---|", "|---|---|---|---|---|---|---|---|---|---|---|")
                  .replace("| 文书写明 |  |", "| 文书写明 |"))
        self.assertNotIn("提前天数", legacy.split("| 案号")[1])
        self.ledger.write_text(legacy, encoding="utf-8")
        out = self.ok(self.merge([DEPOSIT]))
        self.assertIsNone(self.payload(out), "补列不算这批变动")
        self.assertIn("保全底账已写", out)
        self.assertIn("| 到期日来源 | 提前天数 | 备注 |", self.ledger_text())


class ExportTests(CliCase):
    def test_export_filters_by_case(self):
        self.ok(self.merge([DEPOSIT, dict(HOUSE, 案号=CASE2)]))
        out = self.ok(self.run_cli("export", "--ledger", str(self.ledger), "--case", CASE2, "--now", NOW))
        data = self.payload(out)
        self.assertEqual([b["id"] for b in data["batches"]], ["续保识别码：%s@2029-03-02" % CASE2])
        self.assertEqual(data["void"], [])
        self.assertIn("未改动", out)

    def test_export_replace_voids_exported(self):
        self.ok(self.merge([DEPOSIT]))
        out = self.ok(self.run_cli("export", "--ledger", str(self.ledger), "--replace", "--now", NOW))
        data = self.payload(out)
        self.assertEqual(data["void"], [b["id"] for b in data["batches"]])

    def test_export_missing_ledger(self):
        code, _, err = self.run_cli("export", "--ledger", str(self.ledger))
        self.assertEqual(code, 1)
        self.assertIn("不在", err)


if __name__ == "__main__":
    unittest.main()
