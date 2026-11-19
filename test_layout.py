"""布局算法测试：合成树形图谱，验证辐射布局无交叉、无重叠。"""
import unittest

import graph_layout


def make_synthetic_tree():
    nodes = [{"id": "root", "label": "社区服务体验", "level": 1, "category": "主题"}]
    edges = []
    for i in range(4):
        cat_id = f"cat{i+1}"
        nodes.append({"id": cat_id, "label": f"类别{i+1}", "level": 2, "category": "分类"})
        edges.append({"source": "root", "target": cat_id, "type": "层级", "label": "次级"})
        for j in range(3):
            item_id = f"{cat_id}_item{j+1}"
            nodes.append({"id": item_id, "label": f"表现{i+1}-{j+1}", "level": 3, "category": "表现"})
            edges.append({"source": cat_id, "target": item_id, "type": "层级", "label": "次级"})
            for k in range(2):
                desc_id = f"{item_id}_desc{k+1}"
                nodes.append({"id": desc_id, "label": f"描述{i+1}-{j+1}-{k+1}", "level": 4, "category": "描述"})
                edges.append({"source": item_id, "target": desc_id, "type": "层级", "label": "次级"})
    return nodes, edges


class TestLayout(unittest.TestCase):
    def setUp(self):
        self.nodes, self.edges = make_synthetic_tree()

    def test_positions_cover_all_nodes(self):
        positions = graph_layout.calculate_positions_hierarchical(self.nodes, self.edges)
        self.assertEqual(set(positions.keys()), {n["id"] for n in self.nodes})
        for x, y in positions.values():
            self.assertTrue(x == x and y == y)  # 非 NaN

    def test_radial_layout_has_no_crossings(self):
        positions = graph_layout.calculate_positions_hierarchical(self.nodes, self.edges)
        positions = graph_layout.adjust_positions_to_avoid_overlap(positions, self.nodes, node_size=0.4)
        crossings = graph_layout.check_line_crossing(positions, self.edges)
        self.assertEqual(crossings, [])


if __name__ == "__main__":
    unittest.main()