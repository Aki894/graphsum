"""
Graph Layout Module - Hierarchical graph layout and visualization
Provides optimized radial layout algorithm with no crossing lines
"""
import numpy as np
import matplotlib
import os
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from collections import defaultdict
from typing import Dict, List, Tuple, Any, Optional

# Chinese font configuration
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei']
plt.rcParams['axes.unicode_minus'] = False

# Default level distances for radial layout (increased for larger nodes)
DEFAULT_LEVEL_DISTANCES: Dict[int, float] = {
    2: 3.0,
    3: 5.0,
    4: 7.0
}

# Butterfly layout parameters
BUTTERFLY_BASE_DISTANCE: Dict[int, float] = {
    2: 2.5,
    3: 4.5,
    4: 6.5
}
BUTTERFLY_DISTANCE_VARIATION = 0.8  # 距离变化范围

# 工程纸风格层级色（与前端 blueprint.js 保持一致）
DEFAULT_LEVEL_COLORS: Dict[int, str] = {
    1: '#0055FF',
    2: '#E11D48',
    3: '#059669',
    4: '#D97706',
}

LEVEL_CODES: Dict[int, str] = {1: 'ROOT', 2: 'CORE', 3: 'SUB', 4: 'DETAIL'}

# 图纸底色 / 墨色 / 灰线
SHEET_COLOR = '#F8FAFC'
SHEET_DOT = '#94A3B8'
SHEET_LINE = '#CBD5E1'
INK_COLOR = '#0F172A'
SLATE_COLOR = '#64748B'
ENG_BLUE = '#0055FF'


def calculate_positions_hierarchical(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    level_distances: Optional[Dict[int, float]] = None
) -> Dict[str, Tuple[float, float]]:
    """
    Calculate node positions using recursive sector allocation.
    Ensures no crossing lines and proper hierarchical layout.
    
    Args:
        nodes: List of node dicts with 'id', 'level', 'label' keys
        edges: List of edge dicts with 'source', 'target', 'type' keys
        level_distances: Optional custom distances for each level
    
    Returns:
        Dict mapping node_id to (x, y) position tuple
    """
    if level_distances is None:
        level_distances = DEFAULT_LEVEL_DISTANCES
    
    node_dict = {node['id']: node for node in nodes}
    
    # Build parent-child relationships
    parent_children: Dict[str, List[str]] = defaultdict(list)
    for edge in edges:
        # 支持有 type 字段和无 type 字段的边
        edge_type = edge.get('type', '')
        # 如果没有 type 或 type 为 '层级'，都视为层级关系
        if edge_type == '' or edge_type == '层级':
            source = edge['source']
            target = edge['target']
            if source in node_dict and target in node_dict:
                if node_dict[source]['level'] < node_dict[target]['level']:
                    parent_children[source].append(target)
    
    # Find root node
    root_node = None
    for node in nodes:
        if node['level'] == 1:
            root_node = node
            break
    
    if not root_node:
        return {}
    
    positions: Dict[str, Tuple[float, float]] = {}
    
    # Calculate leaf count for each node (subtree size)
    def count_leaves(node_id: str) -> int:
        """Recursively count leaf nodes in subtree"""
        children = parent_children.get(node_id, [])
        if not children:
            return 1  # Leaf node
        return sum(count_leaves(child) for child in children)
    
    leaf_counts: Dict[str, int] = {}
    for node in nodes:
        leaf_counts[node['id']] = count_leaves(node['id'])
    
    # Recursive layout function
    def layout_subtree(
        node_id: str,
        center_angle: float,
        angle_span: float,
        parent_pos: Tuple[float, float],
        level: int
    ) -> None:
        """
        Recursively layout subtree with sector allocation.
        
        Args:
            node_id: Current node ID
            center_angle: Center angle for this node's sector
            angle_span: Angle range allocated to this node
            parent_pos: Parent node position
            level: Current hierarchy level
        """
        if node_id not in node_dict:
            return
        
        distance = level_distances.get(level, 2.0)
        
        # Calculate current node position
        if level == 1:
            positions[node_id] = (0.0, 0.0)
        else:
            x = distance * np.cos(center_angle)
            y = distance * np.sin(center_angle)
            positions[node_id] = (x, y)
        
        # Recursively layout children
        children = parent_children.get(node_id, [])
        if not children:
            return
        
        # Allocate angles based on leaf counts
        total_leaves = sum(leaf_counts[child] for child in children)
        current_angle = center_angle - angle_span / 2  # Start from left boundary
        
        for child_id in children:
            child_leaves = leaf_counts[child_id]
            # Angle range = total_angle * (child_leaves / total_leaves)
            child_angle_span = angle_span * (child_leaves / total_leaves)
            child_center_angle = current_angle + child_angle_span / 2
            
            layout_subtree(
                child_id, child_center_angle, child_angle_span,
                positions[node_id], level + 1
            )
            
            current_angle += child_angle_span
    
    # Start recursive layout from root
    layout_subtree(root_node['id'], -np.pi / 2, 2 * np.pi, (0, 0), 1)
    
    return positions


def calculate_positions_butterfly(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    base_distances: Optional[Dict[int, float]] = None,
    distance_variation: float = BUTTERFLY_DISTANCE_VARIATION
) -> Dict[str, Tuple[float, float]]:
    """
    Calculate node positions using butterfly/fan layout.
    Children are distributed in a fan shape on one side of parent,
    with varying distances based on sibling count.
    
    Args:
        nodes: List of node dicts with 'id', 'level', 'label' keys
        edges: List of edge dicts with 'source', 'target', 'type' keys
        base_distances: Base distances for each level
        distance_variation: How much distance can vary (0-1)
    
    Returns:
        Dict mapping node_id to (x, y) position tuple
    """
    if base_distances is None:
        base_distances = BUTTERFLY_BASE_DISTANCE
    
    node_dict = {node['id']: node for node in nodes}
    
    # Build parent-child relationships
    parent_children: Dict[str, List[str]] = defaultdict(list)
    child_parent: Dict[str, str] = {}
    for edge in edges:
        edge_type = edge.get('type', '')
        if edge_type == '' or edge_type == '层级':
            source = edge['source']
            target = edge['target']
            if source in node_dict and target in node_dict:
                if node_dict[source]['level'] < node_dict[target]['level']:
                    parent_children[source].append(target)
                    child_parent[target] = source
    
    # Find root node
    root_node = None
    for node in nodes:
        if node['level'] == 1:
            root_node = node
            break
    
    if not root_node:
        return {}
    
    positions: Dict[str, Tuple[float, float]] = {}
    
    # Calculate leaf count for angle allocation
    def count_leaves(node_id: str) -> int:
        children = parent_children.get(node_id, [])
        if not children:
            return 1
        return sum(count_leaves(child) for child in children)
    
    leaf_counts: Dict[str, int] = {}
    for node in nodes:
        leaf_counts[node['id']] = count_leaves(node['id'])
    
    def layout_butterfly(
        node_id: str,
        center_angle: float,
        angle_span: float,
        level: int
    ) -> None:
        """
        Layout nodes in butterfly pattern.
        Children fan out from parent with varying distances.
        """
        if node_id not in node_dict:
            return
        
        # Position root at center
        if level == 1:
            positions[node_id] = (0.0, 0.0)
        
        children = parent_children.get(node_id, [])
        if not children:
            return
        
        n_children = len(children)
        base_dist = base_distances.get(level + 1, 5.0)
        
        # Calculate total leaves for proportional angle allocation
        total_leaves = sum(leaf_counts[child] for child in children)
        
        # Start angle for children (fan out from center_angle)
        current_angle = center_angle - angle_span / 2
        
        for idx, child_id in enumerate(children):
            child_leaves = leaf_counts[child_id]
            # Proportional angle based on subtree size
            child_angle_span = angle_span * (child_leaves / total_leaves)
            child_center_angle = current_angle + child_angle_span / 2
            
            # Vary distance based on position in sibling list
            # Middle children are closer, edge children are farther
            if n_children > 1:
                # Normalized position: 0 at edges, 1 at center
                center_factor = 1 - abs(2 * idx / (n_children - 1) - 1)
                distance = base_dist * (1 - distance_variation * 0.5 + distance_variation * center_factor * 0.5)
            else:
                distance = base_dist
            
            # Calculate position
            x = positions[node_id][0] + distance * np.cos(child_center_angle)
            y = positions[node_id][1] + distance * np.sin(child_center_angle)
            positions[child_id] = (x, y)
            
            # Recursively layout grandchildren
            layout_butterfly(child_id, child_center_angle, child_angle_span, level + 1)
            
            current_angle += child_angle_span
    
    # Start layout from root
    layout_butterfly(root_node['id'], -np.pi / 2, 2 * np.pi, 1)
    
    return positions


def adjust_positions_to_avoid_overlap(
    positions: Dict[str, Tuple[float, float]],
    nodes: List[Dict[str, Any]],
    min_distance: float = 0.8,
    max_iterations: int = 50,
    node_size: float = 0.35
) -> Dict[str, Tuple[float, float]]:
    """
    Adjust node positions to avoid overlap while maintaining hierarchy.
    
    Args:
        positions: Dict of node_id to (x, y) positions
        nodes: List of node dicts
        min_distance: Minimum distance between nodes (will be overridden by node_size)
        max_iterations: Maximum adjustment iterations
        node_size: Radius of nodes (used to calculate actual min_distance)
    
    Returns:
        Adjusted positions dict
    """
    # Calculate actual minimum distance based on node size
    # Use 2.2x node diameter to ensure no overlap with safety margin
    actual_min_distance = node_size * 2.2
    
    # Group nodes by level
    level_nodes: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for node in nodes:
        level_nodes[node['level']].append(node)
    
    # Only adjust overlap within same level
    for level, level_node_list in level_nodes.items():
        if level == 1:
            continue  # Level 1 has only one node
        
        node_ids = [node['id'] for node in level_node_list]
        
        for iteration in range(max_iterations):
            adjusted = False
            
            for i, node_id1 in enumerate(node_ids):
                for node_id2 in node_ids[i + 1:]:
                    if node_id1 not in positions or node_id2 not in positions:
                        continue
                    
                    pos1 = np.array(positions[node_id1])
                    pos2 = np.array(positions[node_id2])
                    
                    distance = np.linalg.norm(pos1 - pos2)
                    
                    if distance < actual_min_distance and distance > 0:
                        # Calculate push direction
                        direction = pos2 - pos1
                        direction = direction / distance
                        
                        push_distance = (actual_min_distance - distance) / 2
                        
                        # Push along radial direction to maintain hierarchy
                        angle1 = np.arctan2(pos1[1], pos1[0])
                        angle2 = np.arctan2(pos2[1], pos2[0])
                        
                        radius1 = np.linalg.norm(pos1)
                        radius2 = np.linalg.norm(pos2)
                        
                        # Calculate angle adjustment
                        angle_adjustment = push_distance / ((radius1 + radius2) / 2)
                        
                        new_angle1 = angle1 - angle_adjustment
                        new_angle2 = angle2 + angle_adjustment
                        
                        positions[node_id1] = (
                            radius1 * np.cos(new_angle1),
                            radius1 * np.sin(new_angle1)
                        )
                        positions[node_id2] = (
                            radius2 * np.cos(new_angle2),
                            radius2 * np.sin(new_angle2)
                        )
                        adjusted = True
            
            if not adjusted:
                break
    
    return positions


def check_line_crossing(
    positions: Dict[str, Tuple[float, float]],
    edges: List[Dict[str, Any]]
) -> List[Tuple[Tuple[str, str], Tuple[str, str]]]:
    """
    Check for crossing lines in the graph.
    
    Args:
        positions: Dict of node_id to (x, y) positions
        edges: List of edge dicts
    
    Returns:
        List of crossing edge pairs
    """
    edge_list = []
    for edge in edges:
        source = edge['source']
        target = edge['target']
        if source in positions and target in positions:
            edge_list.append((source, target, positions[source], positions[target]))
    
    crossings = []
    for i, (s1, t1, p1_start, p1_end) in enumerate(edge_list):
        for j, (s2, t2, p2_start, p2_end) in enumerate(edge_list[i + 1:], i + 1):
            if _lines_intersect(p1_start, p1_end, p2_start, p2_end):
                crossings.append(((s1, t1), (s2, t2)))
    
    return crossings


def _lines_intersect(
    p1: Tuple[float, float],
    p2: Tuple[float, float],
    p3: Tuple[float, float],
    p4: Tuple[float, float]
) -> bool:
    """Check if two line segments intersect (excluding shared endpoints)"""
    def ccw(a: Tuple[float, float], b: Tuple[float, float], c: Tuple[float, float]) -> bool:
        return (c[1] - a[1]) * (b[0] - a[0]) > (b[1] - a[1]) * (c[0] - a[0])
    
    # Skip if shared endpoints
    if p1 == p3 or p1 == p4 or p2 == p3 or p2 == p4:
        return False
    
    return ccw(p1, p3, p4) != ccw(p2, p3, p4) and ccw(p1, p2, p3) != ccw(p1, p2, p4)


def wrap_text(text: str, max_chars_per_line: int = 4) -> str:
    """Wrap text into multiple lines"""
    if len(text) <= max_chars_per_line:
        return text
    
    lines = []
    for i in range(0, len(text), max_chars_per_line):
        lines.append(text[i:i + max_chars_per_line])
    
    return '\n'.join(lines)


def draw_graph(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    output_path: str,
    positions: Optional[Dict[str, Tuple[float, float]]] = None,
    title: Optional[str] = None,
    level_colors: Optional[Dict[int, str]] = None,
    show_edge_labels: bool = True,
    show_crossing_highlight: bool = False,
    layout_mode: str = "butterfly"
) -> int:
    """
    Draw graph with hierarchical layout and save to file.
    
    Args:
        nodes: List of node dicts with 'id', 'level', 'label' keys
        edges: List of edge dicts with 'source', 'target', 'type' keys
        output_path: Path to save the PNG image
        positions: Optional pre-calculated positions (will calculate if None)
        title: Optional custom title (uses root label if None)
        level_colors: Optional custom colors for each level
        show_edge_labels: Whether to show '次级' labels on edges
        show_crossing_highlight: Whether to highlight crossing lines in red
        layout_mode: Layout algorithm - "butterfly" (default) or "radial"
    
    Returns:
        Number of line crossings found
    """
    if level_colors is None:
        level_colors = DEFAULT_LEVEL_COLORS
    
    # Calculate positions if not provided
    if positions is None:
        if layout_mode == "butterfly":
            positions = calculate_positions_butterfly(nodes, edges)
        else:
            positions = calculate_positions_hierarchical(nodes, edges)
        # Note: node_size will be calculated below, so we use a default here
        temp_node_size = 0.5 if len(nodes) <= 30 else (0.4 if len(nodes) <= 50 else 0.3)
        positions = adjust_positions_to_avoid_overlap(positions, nodes, node_size=temp_node_size)
    
    # Adjust figure size based on node count
    n_nodes = len(nodes)
    if n_nodes <= 20:
        figsize = (16, 12)
    elif n_nodes <= 40:
        figsize = (20, 16)
    else:
        figsize = (24, 18)

    fig, ax = plt.subplots(figsize=figsize)
    fig.patch.set_facecolor(SHEET_COLOR)
    ax.set_facecolor(SHEET_COLOR)

    # 标题字号随节点数缩放
    if n_nodes <= 30:
        font_size = 11
    elif n_nodes <= 50:
        font_size = 10
    else:
        font_size = 9

    # 卡片尺寸（数据单位）：编号行 + 标题行，与前端卡片比例一致
    max_coord = max([max(abs(p[0]), abs(p[1])) for p in positions.values()]) if positions else 7.0
    span = 2 * max_coord + 2
    inch_per_unit = (figsize[0] - 2.0) / span
    char_w = font_size / 72.0 / inch_per_unit          # 一个中文字符宽
    code_char_w = char_w * 0.52                        # 等宽小编号字符宽
    line_h = font_size / 72.0 / inch_per_unit * 1.55   # 标题行高
    code_h = line_h * 0.8
    pad = line_h * 0.55

    def card_dims(node):
        label = str(node.get('label', node['id']))
        lines = []
        for i in range(0, len(label), 8):
            lines.append(label[i:i + 8])
        if len(lines) > 2:
            lines = lines[:2]
            lines[1] = lines[1][:7] + '…'
        if not lines:
            lines = [str(node['id'])]
        code = 'L{} // {}'.format(node.get('level', '?'), LEVEL_CODES.get(node.get('level'), 'NODE'))
        title_w = max(len(l) for l in lines) * char_w
        w = max(char_w * 7, pad * 2 + max(title_w, len(code) * code_char_w) + char_w * 0.6)
        h = pad * 2 + code_h + line_h * len(lines)
        return w, h, lines, code

    dims = {n['id']: card_dims(n) for n in nodes if n['id'] in positions}

    # 卡片式节点需要更大的同层间距
    positions = adjust_positions_to_avoid_overlap(positions, nodes, node_size=0.62)

    # 点阵工程纸底纹
    step = 0.5
    gx = np.arange(-max_coord - 1, max_coord + 1 + step, step)
    gy = np.arange(-max_coord - 1, max_coord + 1 + step, step)
    mx, my = np.meshgrid(gx, gy)
    ax.scatter(mx.ravel(), my.ravel(), s=1.6, color=SHEET_DOT, alpha=0.4, marker='.', linewidths=0)

    # Check crossings if highlighting enabled
    crossings = []
    if show_crossing_highlight:
        crossings = check_line_crossing(positions, edges)

    def rect_trim(pos, direction, w, h):
        """沿方向到卡片矩形边界的距离"""
        dx, dy = abs(direction[0]), abs(direction[1])
        tx = (w / 2) / dx if dx > 1e-9 else np.inf
        ty = (h / 2) / dy if dy > 1e-9 else np.inf
        return min(tx, ty)

    # Draw edges（灰线 + 小箭头，交叉高亮保留红色）
    for edge in edges:
        if edge['source'] not in positions or edge['target'] not in positions:
            continue

        source_pos = np.array(positions[edge['source']])
        target_pos = np.array(positions[edge['target']])

        direction = target_pos - source_pos
        distance = np.linalg.norm(direction)
        if distance > 0:
            direction = direction / distance
            sw, sh = dims.get(edge['source'], (1.0, 0.7))[:2]
            tw, th = dims.get(edge['target'], (1.0, 0.7))[:2]
            arrow_start = source_pos + direction * (rect_trim(source_pos, direction, sw, sh) + 0.04)
            arrow_end = target_pos - direction * (rect_trim(target_pos, direction, tw, th) + 0.06)

            is_crossing = False
            if show_crossing_highlight:
                for (s1, t1), (s2, t2) in crossings:
                    if (edge['source'] == s1 and edge['target'] == t1) or \
                       (edge['source'] == s2 and edge['target'] == t2):
                        is_crossing = True
                        break

            color = '#FF0000' if is_crossing else SHEET_DOT
            lw = 2.2 if is_crossing else 1.1
            alpha = 0.95 if is_crossing else 0.85

            ax.annotate(
                '', xy=arrow_end, xytext=arrow_start,
                arrowprops=dict(arrowstyle='-|>', lw=lw, color=color, alpha=alpha,
                                mutation_scale=9, shrinkA=0, shrinkB=0)
            )

            if show_edge_labels:
                edge_label = edge.get('label', '')
                if edge_label and edge_label not in ('次级', '层级', '包含', '属于'):
                    mid_x = (source_pos[0] + target_pos[0]) / 2
                    mid_y = (source_pos[1] + target_pos[1]) / 2
                    ax.text(
                        mid_x, mid_y, edge_label, fontsize=6, color=SLATE_COLOR,
                        ha='center', va='center',
                        bbox=dict(boxstyle='square,pad=0.15', facecolor=SHEET_COLOR,
                                  alpha=0.9, edgecolor='none')
                    )

    # Draw nodes：白底技术卡片（左侧类别色条 + 等宽编号 + 深墨标题）
    for node in nodes:
        if node['id'] not in positions:
            continue

        pos = positions[node['id']]
        level = node.get('level', 2)
        color = level_colors.get(level, '#7C3AED')
        w, h, lines, code = dims[node['id']]
        left, top = pos[0] - w / 2, pos[1] + h / 2
        is_root = level == 1

        card = patches.Rectangle(
            (left, top - h), w, h,
            facecolor='#EFF6FF' if is_root else '#FFFFFF',
            edgecolor=ENG_BLUE if is_root else SHEET_DOT,
            linewidth=1.4 if is_root else 1.0, zorder=3
        )
        ax.add_patch(card)
        stripe = patches.Rectangle(
            (left, top - h), w * 0.045, h,
            facecolor=color, edgecolor='none', zorder=4
        )
        ax.add_patch(stripe)

        tx = left + pad + w * 0.045
        ax.text(tx, top - pad - code_h * 0.5, code,
                fontsize=max(6, font_size - 4), color=SLATE_COLOR,
                family='monospace', ha='left', va='center', zorder=5)
        for i, ln in enumerate(lines):
            ax.text(tx, top - pad - code_h - line_h * (i + 0.5), ln,
                    fontsize=font_size, color=INK_COLOR, weight='bold',
                    ha='left', va='center', zorder=5)

    # Set figure properties
    ax.set_xlim(-max_coord - 1, max_coord + 1)
    ax.set_ylim(-max_coord - 1, max_coord + 1)
    ax.set_aspect('equal')
    ax.axis('off')

    # 四角套准标记
    for cx, cy in ((-max_coord - 0.7, -max_coord - 0.7), (max_coord + 0.7, -max_coord - 0.7),
                   (-max_coord - 0.7, max_coord + 0.7), (max_coord + 0.7, max_coord + 0.7)):
        ax.plot([cx - 0.18, cx + 0.18], [cy, cy], color=ENG_BLUE, lw=0.8, alpha=0.7)
        ax.plot([cx, cx], [cy - 0.18, cy + 0.18], color=ENG_BLUE, lw=0.8, alpha=0.7)

    # Set title
    if title is None:
        base_name = os.path.basename(output_path) if output_path else ""
        if base_name:
            name, _ = os.path.splitext(base_name)
            if name.endswith("_graph"):
                name = name[:-6]
            title = name
        elif nodes:
            title = nodes[0]['label']
        else:
            title = "图结构"
    ax.set_title(title, fontsize=16, weight='bold', pad=24, color=INK_COLOR)
    fig.text(0.02, 0.985, 'GRAPHSUM · KNOWLEDGE GRAPH DRAFTING', fontsize=8,
             color=SLATE_COLOR, family='monospace', va='top')
    fig.text(0.98, 0.985, 'SCALE 1:1 · LOCAL-FIRST', fontsize=8,
             color=SLATE_COLOR, family='monospace', va='top', ha='right')

    # 层级图例
    legend_handles = [
        patches.Patch(facecolor=level_colors.get(lv, '#7C3AED'),
                      label='L{} {}'.format(lv, name))
        for lv, name in ((1, '根节点'), (2, '核心话题'), (3, '子话题'), (4, '细节'))
    ]
    leg = fig.legend(handles=legend_handles, loc='lower center', ncol=4,
                     frameon=False, fontsize=9, bbox_to_anchor=(0.5, 0.005))
    for t in leg.get_texts():
        t.set_color(SLATE_COLOR)

    # Save image
    plt.tight_layout()
    plt.savefig(output_path, dpi=220, bbox_inches='tight', facecolor=SHEET_COLOR)
    plt.close()

    return len(crossings)
