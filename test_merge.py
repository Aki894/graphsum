"""合并模块测试：子图加载、层级节点提取、去重合并与 root 映射。"""
import json
import os
import tempfile
import unittest
from unittest import mock

from merge_graphs import GraphMerger

ENV = {
    "PROVIDER_UNITTEST_API_KEY": "test-key",
    "PROVIDER_UNITTEST_BASE_URL": "http://127.0.0.1:9/v1",
    "PROVIDER_UNITTEST_MODEL": "unit-model",
    "DEFAULT_PROVIDER": "unittest",
    "NO_PROXY": "localhost,127.0.0.1",
}


def graph(root_label, cat_labels):
    nodes = [{"id": "root", "label": root_label, "level": 1, "category": "主题"}]
    edges = []
    for i, label in enumerate(cat_labels):
        nodes.append({"id": f"c{i+1}", "label": label, "level": 2, "category": "分类"})
        edges.append({"source": "root", "target": f"c{i+1}", "type": "层级", "label": "次级"})
    return {"nodes": nodes, "edges": edges}


class TestMerge(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for name, root_label, cats in [
            ("a_graph.json", "社区服务体验", ["服务态度", "餐饮质量"]),
            ("b_graph.json", "养老站使用感受", ["服务态度", "活动设施"]),
        ]:
            with open(os.path.join(self.tmp.name, name), "w", encoding="utf-8") as f:
                json.dump(graph(root_label, cats), f, ensure_ascii=False)
        self.merger = GraphMerger(data_folder=self.tmp.name)

    def test_load_and_extract_by_level(self):
        self.assertEqual(self.merger.load_all_graphs(), 2)
        level2 = self.merger.extract_nodes_by_level(2)
        self.assertEqual(len(level2), 4)
        self.assertEqual(len(self.merger.extract_nodes_by_level(1)), 2)

    def test_simple_merge_dedup_and_mapping(self):
        self.merger.load_all_graphs()
        nodes = self.merger.extract_nodes_by_level(2)
        merged = self.merger._simple_merge_nodes(2, nodes)
        self.assertEqual([n["label"] for n in merged], ["服务态度", "餐饮质量", "活动设施"])
        for node in nodes:
            self.assertIn(node["id"], self.merger.node_mapping)

    def test_root_mapping(self):
        self.merger._build_node_mapping(1, [{"id": "r1"}, {"id": "r2"}], [])
        self.assertEqual(self.merger.node_mapping["r1"], "root")
        self.assertEqual(self.merger.node_mapping["r2"], "root")


if __name__ == "__main__":
    unittest.main()