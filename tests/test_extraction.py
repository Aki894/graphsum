"""抽取路径测试：失败显式报错、图结构清洗、长文本分段。"""
import os
import unittest
from unittest import mock

from interview_graph_generator import InterviewGraphGenerator

ENV = {
    "PROVIDER_UNITTEST_API_KEY": "test-key",
    "PROVIDER_UNITTEST_BASE_URL": "http://127.0.0.1:9/v1",
    "PROVIDER_UNITTEST_MODEL": "unit-model",
    "DEFAULT_PROVIDER": "unittest",
    "NO_PROXY": "localhost,127.0.0.1",
}


class TestExtraction(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.gen = InterviewGraphGenerator()

    def test_short_text_failure_raises(self):
        with mock.patch.object(self.gen, "_call_api", side_effect=RuntimeError("api down")):
            with self.assertRaises(RuntimeError):
                self.gen.extract_entities("社区访谈短文本。" * 5, "sample.txt")

    def test_chunked_failure_raises(self):
        text = "这是一段用于测试的社区访谈内容，包含具体观点。" * 400
        with mock.patch.object(self.gen, "_call_api", side_effect=RuntimeError("api down")):
            with self.assertRaises(RuntimeError):
                self.gen.extract_entities(text, "sample.txt")

    def test_clean_filters_same_level_and_reverse_edges(self):
        graph = {
            "nodes": [
                {"id": "root", "label": "主题", "level": 1},
                {"id": "a", "label": "甲", "level": 2},
                {"id": "b", "label": "乙", "level": 2},
                {"id": "c", "label": "丙", "level": 3},
            ],
            "edges": [
                {"source": "root", "target": "a"},
                {"source": "a", "target": "b"},
                {"source": "b", "target": "a"},
                {"source": "a", "target": "c"},
            ],
        }
        cleaned = self.gen.clean_graph_structure(graph)
        pairs = {(e["source"], e["target"]) for e in cleaned["edges"]}
        self.assertEqual(pairs, {("root", "a"), ("a", "c")})

    def test_split_text_with_overlap(self):
        text = "字" * 9000
        chunks = self.gen._split_text_with_overlap(text, chunk_size=4000, overlap=500)
        self.assertGreaterEqual(len(chunks), 2)
        self.assertTrue(all(0 < len(c) <= 4000 for c in chunks))


if __name__ == "__main__":
    unittest.main()