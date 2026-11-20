"""命令行入口冒烟测试：确保 once/report 可独立运行。"""
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tests.collector_support import CollectorTest


class CliSmokeTest(CollectorTest):
    def test_once_and_report_commands(self):
        from collector import cli

        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "cli.db")
            args = cli.argparse.Namespace(
                base_url=self.base, db=db, input=None, credential=None,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli.cmd_once(args)
            self.assertIn("run_id", buf.getvalue())

            args_report = cli.argparse.Namespace(db=db)
            report_buf = io.StringIO()
            with redirect_stdout(report_buf):
                cli.cmd_report(args_report)
            self.assertIn("公告适用性值班报告", report_buf.getvalue())
            self.assertIn("node-a", report_buf.getvalue())


if __name__ == "__main__":
    unittest.main()
