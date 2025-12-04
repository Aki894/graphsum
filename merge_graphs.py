import os
import json
import re
import time
from collections import defaultdict
from interview_graph_generator import InterviewGraphGenerator
import graph_layout
from logger_config import log_info, log_debug, log_warning, log_error, log_stream, log_stream_line


class GraphMerger:
    def __init__(self, data_folder="data", max_nodes_config=None):
        """初始化图合并器"""
        self.data_folder = data_folder
        self.generator = InterviewGraphGenerator()
        self.all_graphs = []
        self.node_mapping = {}  # 原节点ID -> 合并后节点ID的映射
        
        # 性能优化：缓存数据结构
        self.merged_node_dict = {}  # 合并后节点ID -> 节点对象
        self.merged_nodes_by_level = defaultdict(list)  # 层级 -> 合并后节点列表
        
        # 节点数量限制配置
        self.max_nodes_config = max_nodes_config or {
            2: 8,
            3: 20,
            4: 30
        }
    
    def set_task_context(self, task_id, cancel_callback, sse_callback=None):
        """设置任务上下文，传递给generator"""
        self.generator.set_task_context(task_id, cancel_callback, sse_callback)
        
    def load_all_graphs(self):
        """加载所有子图的JSON文件"""
        if not os.path.exists(self.data_folder):
            raise FileNotFoundError(f"数据文件夹不存在: {self.data_folder}")
        
        json_files = []
        for file in os.listdir(self.data_folder):
            if file.endswith('_graph.json'):
                json_files.append(os.path.join(self.data_folder, file))
        
        log_info(f"找到 {len(json_files)} 个子图JSON文件")
        
        for json_file in json_files:
            with open(json_file, 'r', encoding='utf-8') as f:
                graph_data = json.load(f)
                self.all_graphs.append(graph_data)
                log_info(f"  加载: {os.path.basename(json_file)} - {len(graph_data['nodes'])} 个节点")
        
        return len(self.all_graphs)
    
    def extract_nodes_by_level(self, level):
        """提取所有子图中指定层级的节点"""
        nodes_by_level = []
        for graph in self.all_graphs:
            for node in graph['nodes']:
                if node.get('level') == level:
                    nodes_by_level.append(node)
        return nodes_by_level
    
    def merge_level_with_ai(self, level, nodes):
        """使用AI合并指定层级的节点"""
        if not nodes:
            return []
        
        log_info(f"\n正在合并第{level}层节点，共{len(nodes)}个节点...")
        
        # 收集所有节点的label
        node_labels = [node['label'] for node in nodes]
        unique_labels = list(set(node_labels))
        
        if level == 1:
            # 第1层：合并所有root节点为一个统一主题
            prompt = f"""
快速分析以下主题，合并为统一主题词（4-6字）。

所有主题：
{chr(10).join([f"- {label}" for label in unique_labels])}

【硬性结构约束】
1. root 节点是唯一允许没有入边的节点
2. 第2层及以下节点必须严格保持“单一父节点”结构，绝不允许出现一个节点拥有多个父节点
3. 如果合并导致任何节点可能拥有两个父节点，必须立即重新规划输出

请输出一个统一的主题词，格式为JSON：
{{"topic":"XXXX"}}

示例：{{"topic":"医院服务体验访谈"}}
"""
        else:
            # 其他层：识别语义相似的节点并合并
            category = nodes[0].get('category', '')
            # 为每个节点创建唯一标识
            node_list_with_id = []
            for i, label in enumerate(unique_labels):
                node_list_with_id.append(f"节点{i+1}: {label}")
            
            # 使用配置的节点数量上限
            max_nodes = self.max_nodes_config.get(level, 30)
            
            prompt = f"""
快速识别第{level}层节点中语义相似的并合并（类别：{category}）。

节点列表：
{chr(10).join(node_list_with_id)}

【硬性结构约束】
1. root 始终是唯一不需要入边的节点
2. 第{level}层的每个输出节点都必须能够只连接到一个父节点，任何可能导致“多父节点”的组合必须在本步骤拆分或重新定义
3. 如果一个节点看起来需要接两个父节点，请不要合并到一起，保持独立节点以满足单入边原则

要求（思考过程要简洁）：
1. 激进合并语义相似节点
2. 合并后≤{max_nodes}个节点（当前{len(unique_labels)}个→{max_nodes}个）
3. 选择代表性标签（2-4字）
4. 输出JSON格式

输出格式示例：
{{
  "merged_nodes": [
    {{"label": "合并后的标签1", "original_labels": ["原始标签1", "原始标签2", "原始标签3"]}},
    {{"label": "合并后的标签2", "original_labels": ["原始标签4", "原始标签5"]}},
    ...
  ]
}}

注意：合并后的节点数量必须≤{max_nodes}个！

只输出JSON，不要其他解释：
"""
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                result = self.generator._call_api(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.2,
                    max_tokens=1000 if level == 1 else 1500
                )
                
                # 提取JSON
                json_str = None
                if result.startswith('{'):
                    json_str = result
                else:
                    json_match = re.search(r'\{[\s\S]*\}', result)
                    if json_match:
                        json_str = json_match.group()
                
                if json_str:
                    json_str = json_str.replace('，', ',').replace('：', ':')
                    data = json.loads(json_str)
                    
                    if level == 1:
                        # 第1层返回单个主题
                        merged_topic = data.get("topic", "医院服务体验访谈")
                        merged_nodes = [{
                            "id": "root",
                            "label": merged_topic,
                            "level": 1,
                            "category": "主题"
                        }]
                        print(f"  合并后的主题: {merged_topic}")
                        # 建立第1层的映射
                        self._build_node_mapping(level, nodes, merged_nodes)
                        return merged_nodes
                    else:
                        # 其他层返回合并后的节点列表和映射关系
                        merged_groups = data.get("merged_nodes", [])
                        if not merged_groups:
                            # 如果AI没有返回合并结果，使用简单去重
                            return self._simple_merge_nodes(level, nodes)
                        
                        merged_nodes = []
                        label_to_merged_id = {}  # 标签 -> 合并节点ID的映射
                        
                        for i, group in enumerate(merged_groups):
                            merged_label = group.get("label", "")
                            original_labels = group.get("original_labels", [])
                            
                            if not merged_label:
                                continue
                            
                            node_id = f"l{level}_n{i+1}"
                            merged_nodes.append({
                                "id": node_id,
                                "label": merged_label,
                                "level": level,
                                "category": category
                            })
                            
                            # 建立标签到合并节点ID的映射
                            for orig_label in original_labels:
                                label_to_merged_id[orig_label] = node_id
                        
                        log_info(f"  合并后剩余 {len(merged_nodes)} 个节点")
                        
                        # 检查节点数量是否超过上限，如果超过则进行二次合并
                        max_nodes = self.max_nodes_config.get(level, 30)
                        
                        if len(merged_nodes) > max_nodes:
                            log_info(f"  节点数量({len(merged_nodes)})超过上限({max_nodes})，进行二次合并...")
                            # 将合并后的节点作为输入，再次合并
                            merged_nodes = self._secondary_merge(level, merged_nodes, max_nodes)
                            log_info(f"  二次合并后剩余 {len(merged_nodes)} 个节点")
                        
                        # 建立原节点到合并节点的映射
                        self._build_node_mapping_from_groups(level, nodes, label_to_merged_id, merged_nodes)
                        
                        return merged_nodes
                    
            except json.JSONDecodeError as e:
                log_error(f"  JSON解析错误: {e}，尝试 {attempt + 1}/{max_retries}")
            except Exception as e:
                log_error(f"  AI合并失败: {e}，尝试 {attempt + 1}/{max_retries}")
        
        # 如果AI合并失败，使用简单的去重策略
        log_warning(f"  AI合并失败，使用简单去重策略")
        return self._simple_merge_nodes(level, nodes)
    
    def _build_node_mapping(self, level, original_nodes, merged_nodes):
        """建立原节点到合并节点的映射关系（第1层专用）"""
        if level == 1:
            # 第1层：所有root节点都映射到新的root
            for node in original_nodes:
                self.node_mapping[node['id']] = "root"
    
    def _build_node_mapping_from_groups(self, level, original_nodes, label_to_merged_id, merged_nodes):
        """根据AI返回的合并组建立原节点到合并节点的映射关系"""
        for orig_node in original_nodes:
            orig_label = orig_node['label']
            merged_id = label_to_merged_id.get(orig_label)
            
            if merged_id:
                self.node_mapping[orig_node['id']] = merged_id
            else:
                # 如果标签不在映射中，尝试精确匹配合并后的节点
                for merged_node in merged_nodes:
                    if merged_node['label'] == orig_label:
                        self.node_mapping[orig_node['id']] = merged_node['id']
                        break
                else:
                    # 如果都没有匹配，映射到第一个合并节点（表示被合并了）
                    if merged_nodes:
                        self.node_mapping[orig_node['id']] = merged_nodes[0]['id']
    
    def _secondary_merge(self, level, nodes, max_nodes):
        """二次合并：如果节点数量仍然太多，进一步合并"""
        if len(nodes) <= max_nodes:
            return nodes
        
        node_labels = [node['label'] for node in nodes]
        category = nodes[0].get('category', '')
        
        prompt = f"""
以下第{level}层的节点数量({len(nodes)}个)仍然太多，需要进一步合并到{max_nodes}个或更少。

当前节点列表：
{chr(10).join([f"{i+1}. {label}" for i, label in enumerate(node_labels)])}

【硬性结构约束】
1. root 是唯一允许没有入边的节点
2. 合并产出的第{level}层节点在后续连线中只能拥有一个父节点，任何潜在的多父节点情况都必须在此步拆分
3. 若某个组合会导致单个节点需要同时指向两个父节点，请在本轮合并中保持多个节点，不要强行合并

要求：
1. **必须合并到{max_nodes}个或更少的节点**
2. 将语义相关、可以归类的节点进一步合并
3. 为每个合并组选择最具代表性的标签（2-4个字）
4. 输出JSON格式

输出格式：
{{
  "merged_nodes": [
    {{"label": "合并后的标签1", "original_labels": ["原始标签1", "原始标签2"]}},
    ...
  ]
}}

只输出JSON，不要其他解释：
"""
        
        max_retries = 2
        for attempt in range(max_retries):
            try:
                result = self.generator._call_api(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                    max_tokens=2000
                )
                
                json_str = None
                if result.startswith('{'):
                    json_str = result
                else:
                    json_match = re.search(r'\{[\s\S]*\}', result)
                    if json_match:
                        json_str = json_match.group()
                
                if json_str:
                    json_str = json_str.replace('，', ',').replace('：', ':')
                    data = json.loads(json_str)
                    
                    merged_groups = data.get("merged_nodes", [])
                    if merged_groups:
                        merged_nodes = []
                        for i, group in enumerate(merged_groups):
                            merged_label = group.get("label", "")
                            if merged_label:
                                node_id = f"l{level}_n{i+1}"
                                merged_nodes.append({
                                    "id": node_id,
                                    "label": merged_label,
                                    "level": level,
                                    "category": category
                                })
                        
                        if len(merged_nodes) <= max_nodes:
                            return merged_nodes
                            
            except Exception as e:
                log_error(f"    二次合并失败: {e}，尝试 {attempt + 1}/{max_retries}")
        
        # 如果二次合并失败，强制限制节点数量
        log_warning(f"    二次合并失败，强制限制为前{max_nodes}个节点")
        return nodes[:max_nodes]
    
    def _simple_merge_nodes(self, level, nodes):
        """简单的节点合并策略（去重）"""
        seen_labels = {}
        merged_nodes = []
        
        for node in nodes:
            label = node['label']
            if label not in seen_labels:
                seen_labels[label] = True
                node_id = f"l{level}_n{len(merged_nodes)+1}"
                merged_nodes.append({
                    "id": node_id,
                    "label": label,
                    "level": level,
                    "category": node.get('category', '')
                })
                # 建立映射
                self.node_mapping[node['id']] = node_id
            else:
                # 找到已存在的节点ID
                for merged_node in merged_nodes:
                    if merged_node['label'] == label:
                        self.node_mapping[node['id']] = merged_node['id']
                        break
        
        return merged_nodes
    
    def _determine_parents_with_ai(self, child_nodes, parent_nodes, level):
        """使用AI确定每个子节点应该连接到哪个父节点"""
        if not child_nodes or not parent_nodes:
            return {}
        
        log_info(f"    正在调用AI API确定 {len(child_nodes)} 个子节点的父节点...")
        
        # 构建提示词
        child_labels = [f"{i+1}. {node['label']}" for i, node in enumerate(child_nodes)]
        parent_labels = [f"{i+1}. {node['label']}" for i, node in enumerate(parent_nodes)]
        
        prompt = f"""
快速为第{level}层子节点分配父节点（第{level-1}层）。

父节点列表（第{level-1}层）：
{chr(10).join(parent_labels)}

子节点列表（第{level}层）：
{chr(10).join(child_labels)}

【严格的树形结构要求】
- 每个子节点必须且只能有1条入边（只能连接到一个父节点）
- 严禁出现以下情况：
  * 一个节点有多条入边（一个节点被多个父节点连接）
  * 同层节点之间的连接
  * 跨层连接
  * 反向连接（子节点连接到父节点）
- 确保生成的图是严格的树形结构，每个节点只有一个父节点

【硬性约束】
1. root 仍是唯一允许没有入边的节点
2. 第{level}层任意子节点如果没有明确父节点，必须在本次结果中分配；绝对禁止出现需要两个父节点的情况
3. 如果某个子节点语义上兼顾多个父节点，请强制选择“最符合语义的唯一父节点”，并在输出中保证只有这一条映射

要求（简洁思考）：
1. 根据语义分配最合适父节点
2. 每个子节点只连1个父节点
3. 输出JSON格式

输出格式：
{{
  "mappings": [
    {{"child": "子节点1的标签", "parent": "父节点1的标签"}},
    {{"child": "子节点2的标签", "parent": "父节点2的标签"}},
    ...
  ]
}}

只输出JSON，不要其他解释：
"""
        
        max_retries = 2
        for attempt in range(max_retries):
            try:
                result = self.generator._call_api(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                    max_tokens=1000
                )
                
                # 提取JSON
                json_str = None
                if result.startswith('{'):
                    json_str = result
                else:
                    json_match = re.search(r'\{[\s\S]*\}', result)
                    if json_match:
                        json_str = json_match.group()
                
                if json_str:
                    json_str = json_str.replace('，', ',').replace('：', ':')
                    data = json.loads(json_str)
                    
                    mappings = data.get("mappings", [])
                    if mappings:
                        # 构建标签到节点ID的映射
                        child_label_to_id = {node['label']: node['id'] for node in child_nodes}
                        parent_label_to_id = {node['label']: node['id'] for node in parent_nodes}
                        
                        # 构建子节点ID到父节点ID的映射
                        child_parent_mapping = {}
                        for mapping in mappings:
                            child_label = mapping.get("child", "")
                            parent_label = mapping.get("parent", "")
                            
                            child_id = child_label_to_id.get(child_label)
                            parent_id = parent_label_to_id.get(parent_label)
                            
                            if child_id and parent_id:
                                child_parent_mapping[child_id] = parent_id
                        
                        # 确保所有子节点都有父节点
                        missing_count = 0
                        for child_node in child_nodes:
                            if child_node['id'] not in child_parent_mapping:
                                # 如果没有映射，连接到第一个父节点
                                child_parent_mapping[child_node['id']] = parent_nodes[0]['id']
                                missing_count += 1
                        
                        if missing_count > 0:
                            log_warning(f"    警告：{missing_count} 个子节点没有找到映射，已连接到默认父节点")
                        
                        log_info(f"    ✓ AI成功确定了 {len(child_parent_mapping)} 个节点的父节点")
                        return child_parent_mapping
                        
            except json.JSONDecodeError as e:
                log_error(f"    JSON解析错误: {e}，尝试 {attempt + 1}/{max_retries}")
                if attempt < max_retries - 1:
                    time.sleep(0.5)  # 重试前等待
            except Exception as e:
                log_error(f"    AI确定父节点失败: {e}，尝试 {attempt + 1}/{max_retries}")
                if attempt < max_retries - 1:
                    time.sleep(0.5)  # 重试前等待
        
        # 如果AI失败，使用简单策略：每个子节点连接到第一个父节点
        log_warning(f"    ⚠ AI确定父节点失败，使用默认策略（所有子节点连接到第一个父节点）")
        child_parent_mapping = {}
        default_parent_id = parent_nodes[0]['id']
        for child_node in child_nodes:
            child_parent_mapping[child_node['id']] = default_parent_id
        
        return child_parent_mapping
    
    def merge_all_graphs(self):
        """合并所有子图 - 逐层处理，合并后立即找父节点连线"""
        log_info("=" * 60)
        log_info("开始合并多个子图")
        log_info("=" * 60)
        
        # 加载所有子图
        num_graphs = self.load_all_graphs()
        if num_graphs == 0:
            log_warning("没有找到子图文件")
            return None
        
        # 初始化数据结构
        all_merged_nodes = []
        all_edges = set()
        max_level = 4
        
        # 逐层处理：合并一层后立即找父节点连线
        for level in range(1, max_level + 1):
            log_info(f"\n{'='*60}")
            log_info(f"【处理第{level}层】")
            log_info(f"{'='*60}")
            
            # 提取当前层的所有节点
            nodes = self.extract_nodes_by_level(level)
            if not nodes:
                log_info(f"第{level}层没有节点，跳过")
                continue
            
            log_info(f"提取到 {len(nodes)} 个原始节点")
            
            # 合并当前层节点
            log_info(f"\n[步骤1/2] 合并第{level}层节点...")
            merged_nodes = self.merge_level_with_ai(level, nodes)
            all_merged_nodes.extend(merged_nodes)
            
            # 更新缓存
            for node in merged_nodes:
                self.merged_node_dict[node['id']] = node
                self.merged_nodes_by_level[node['level']].append(node)
            
            log_info(f"✓ 合并完成，第{level}层剩余 {len(merged_nodes)} 个节点")
            log_info(f"  当前总节点数: {len(all_merged_nodes)}")
            
            # 如果不是第1层，立即找父节点并连线
            if level > 1:
                log_info(f"\n[步骤2/2] 为第{level}层节点找父节点（第{level-1}层）...")
                parent_nodes = self.merged_nodes_by_level.get(level - 1, [])
                
                if not parent_nodes:
                    log_warning(f"  警告：第{level-1}层没有父节点，跳过连线")
                else:
                    # 第2层节点直接连接到root，不需要AI
                    if level == 2:
                        root_node = self.merged_nodes_by_level.get(1, [])
                        if root_node:
                            root_id = root_node[0]['id']
                            edges_added = 0
                            for child_node in merged_nodes:
                                all_edges.add((root_id, child_node['id']))
                                edges_added += 1
                            log_info(f"✓ 连线完成，第2层所有节点连接到root，添加了 {edges_added} 条边")
                        else:
                            log_warning(f"  警告：找不到root节点")
                    else:
                        # 第3层及以下：使用AI确定每个子节点的父节点
                        child_parent_mapping = self._determine_parents_with_ai(merged_nodes, parent_nodes, level)
                        
                        # 添加边
                        edges_added = 0
                        for child_id, parent_id in child_parent_mapping.items():
                            if parent_id in self.merged_node_dict and child_id in self.merged_node_dict:
                                all_edges.add((parent_id, child_id))
                                edges_added += 1
                        
                        log_info(f"✓ 连线完成，添加了 {edges_added} 条边")
                    log_info(f"  当前总边数: {len(all_edges)}")
            
            # API调用频率控制：每层之间延迟
            if level < max_level:
                log_info(f"\n等待1秒以避免API调用过于频繁...")
                time.sleep(1)
        
        log_info(f"\n{'='*60}")
        log_info(f"【合并完成】")
        log_info(f"{'='*60}")
        log_info(f"总节点数: {len(all_merged_nodes)}")
        log_info(f"总边数: {len(all_edges)}")
        
        # 转换为边列表格式
        merged_edges = []
        for source_id, target_id in all_edges:
            merged_edges.append({
                "source": source_id,
                "target": target_id,
                "type": "层级",
                "label": "次级"
            })
        
        # 清洗图结构：过滤同层边，确保严格的树形层级结构
        cleaned_graph = self.clean_graph_structure({
            "nodes": all_merged_nodes,
            "edges": merged_edges
        })
        
        # 构建合并后的图数据
        merged_graph = {
            "nodes": cleaned_graph['nodes'],
            "edges": cleaned_graph['edges'],
            "metadata": {
                "total_nodes": len(cleaned_graph['nodes']),
                "total_edges": len(cleaned_graph['edges']),
                "levels": list(set(node['level'] for node in cleaned_graph['nodes'])),
                "categories": list(set(node.get('category', '') for node in cleaned_graph['nodes'])),
                "source_graphs": num_graphs
            }
        }
        
        return merged_graph
    
    def clean_graph_structure(self, graph_data):
        """清洗图结构：过滤同层边，确保严格的树形层级结构"""
        nodes = graph_data.get('nodes', [])
        edges = graph_data.get('edges', [])
        
        # 构建 id → level 映射
        id_to_level = {}
        for node in nodes:
            node_id = node.get('id')
            level = node.get('level')
            if node_id and level:
                id_to_level[node_id] = level
        
        # 过滤边：只保留 source.level < target.level 的边（严格向下的层级边）
        cleaned_edges = []
        removed_count = 0
        
        for edge in edges:
            source_id = edge.get('source')
            target_id = edge.get('target')
            
            source_level = id_to_level.get(source_id)
            target_level = id_to_level.get(target_id)
            
            # 只保留父层指向子层的边
            if source_level is not None and target_level is not None:
                if source_level < target_level:
                    cleaned_edges.append(edge)
                else:
                    removed_count += 1
            else:
                # 如果节点 ID 不在映射中，保留边（避免误删）
                cleaned_edges.append(edge)
        
        if removed_count > 0:
            log_info(f"  过滤了 {removed_count} 条非树形边")
        
        return {
            'nodes': nodes,
            'edges': cleaned_edges
        }
    
    def draw_merged_graph(self, nodes, edges, output_path):
        """Draw merged graph using graph_layout module"""
        # Calculate positions and draw
        positions = graph_layout.calculate_positions_hierarchical(nodes, edges)
        # Determine node size based on node count
        n_nodes = len(nodes)
        node_size = 0.5 if n_nodes <= 30 else (0.4 if n_nodes <= 50 else 0.3)
        positions = graph_layout.adjust_positions_to_avoid_overlap(positions, nodes, node_size=node_size)
        graph_layout.draw_graph(nodes, edges, output_path, positions=positions)
    
    def save_merged_graph(self, merged_graph, output_name="merged_graph"):
        """保存合并后的图"""
        output_path = os.path.join(self.data_folder, f"{output_name}.png")
        json_path = os.path.join(self.data_folder, f"{output_name}.json")
        
        # 使用自定义的绘图方法
        self.draw_merged_graph(
            merged_graph['nodes'],
            merged_graph['edges'],
            output_path
        )
        
        # 保存JSON
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(merged_graph, f, ensure_ascii=False, indent=2)
        
        log_info(f"\n合并图已保存:")
        log_info(f"  PNG: {output_path}")
        log_info(f"  JSON: {json_path}")
        
        return output_path, json_path

def main():
    """主函数"""
    merger = GraphMerger(data_folder="data")
    
    # 合并所有子图
    merged_graph = merger.merge_all_graphs()
    
    if merged_graph:
        # 保存合并后的图
        merger.save_merged_graph(merged_graph)
        log_info("\n合并完成！")
    else:
        log_error("\n合并失败！")

if __name__ == "__main__":
    main()

