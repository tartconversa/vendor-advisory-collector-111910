import json,unittest
from pathlib import Path

class InputTest(unittest.TestCase):
    def test_request_is_readable_utf8_object(self):
        data=json.loads((Path(__file__).resolve().parents[1]/"inputs/request.json").read_text(encoding="utf-8"))
        self.assertIsInstance(data,dict)
        self.assertTrue(data)
