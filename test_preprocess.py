"""预处理模块测试：规则过滤、分句、未启用时透传。"""
import os
import unittest
from unittest import mock

from text_preprocessor import TextPreprocessor


class TestPreprocess(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"ENABLE_RERANKER_PREPROCESS": "false"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.pre = TextPreprocessor()

    def test_disabled_passthrough_for_subtitles(self):
        text = "哈哈哈哈\n今天讨论社区养老服务的改进建议"
        self.assertEqual(self.pre.preprocess(text, ".ass"), text)

    def test_non_subtitle_passthrough(self):
        text = "哈哈哈哈\n今天讨论社区养老服务的改进建议"
        self.assertEqual(self.pre.preprocess(text, ".txt"), text)

    def test_rule_filter_removes_noise(self):
        sentences = ["哈哈哈哈", "666", "大家好", "谢谢送的礼物", "社区食堂本周减盐了"]
        self.assertEqual(self.pre._rule_filter(sentences), ["社区食堂本周减盐了"])

    def test_split_sentences_keeps_long_lines(self):
        sentences = self.pre._split_sentences("好\n今天天气不错适合出去散步\n")
        self.assertIn("今天天气不错适合出去散步", sentences)


if __name__ == "__main__":
    unittest.main()