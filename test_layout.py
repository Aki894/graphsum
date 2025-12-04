"""
Test script for graph layout algorithm
Reads existing graph JSON and validates layout for line crossings
"""
import os
import json
from collections import defaultdict
import graph_layout

def main():
    """主测试函数"""
    # 读取merged_graph.json
    json_path1 = "data/sample_interview_graph.json"
    json_path2 = "data/邓纯-2021年11月12日13日-示例机构_graph.json"
    json_path3="data/merged_graph.json"
    
    for json_path in [json_path1, json_path2, json_path3]:
        print(f"读取文件: {json_path}")
        with open(json_path, 'r', encoding='utf-8') as f:
            graph_data = json.load(f)
        
        nodes = graph_data['nodes']
        edges = graph_data['edges']
        
        print(f"节点数: {len(nodes)}")
        print(f"边数: {len(edges)}")
        
        # 按层级统计
        level_counts = defaultdict(int)
        for node in nodes:
            level_counts[node['level']] += 1
        
        print("\n各层节点数:")
        for level in sorted(level_counts.keys()):
            print(f"  第{level}层: {level_counts[level]} 个节点")
        
        # Calculate layout using graph_layout module
        print("\n计算布局...")
        positions = graph_layout.calculate_positions_hierarchical(nodes, edges)
        # Determine node size based on node count
        n_nodes = len(nodes)
        node_size = 0.5 if n_nodes <= 30 else (0.4 if n_nodes <= 50 else 0.3)
        positions = graph_layout.adjust_positions_to_avoid_overlap(positions, nodes, node_size=node_size)
        
        # Check for crossings
        crossings = graph_layout.check_line_crossing(positions, edges)
        print(f"\n发现 {len(crossings)} 处线条交叉")
        
        # Draw graph with crossing highlights
        output_path = f"{json_path}_test.png"
        print(f"生成测试图片: {output_path}")
        graph_layout.draw_graph(
            nodes, edges, output_path,
            positions=positions,
            show_crossing_highlight=True
        )
        
        print("\n" + "=" * 60)
        if len(crossings) == 0:
            print("✓ 测试通过！没有发现线条交叉")
        else:
            print(f"✗ 测试失败！发现 {len(crossings)} 处线条交叉")
            for (s1, t1), (s2, t2) in crossings:
                print(f"  边 ({s1} → {t1}) 与 边 ({s2} → {t2}) 交叉")
        print("=" * 60)

if __name__ == "__main__":
    main()
