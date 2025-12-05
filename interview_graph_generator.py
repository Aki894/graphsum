import os
import json
import re
from docx import Document
from collections import defaultdict
from dotenv import load_dotenv
import graph_layout
from logger_config import init_logger, log_info, log_debug, log_warning, log_error, log_stream, log_stream_line
from text_preprocessor import preprocess_text, get_preprocessor

# 加载环境变量
load_dotenv()

class InterviewGraphGenerator:
    # 从环境变量读取默认节点数量上限
    DEFAULT_MAX_LEVEL2 = int(os.getenv("DEFAULT_MAX_LEVEL2_NODES", "8"))
    DEFAULT_MAX_LEVEL3 = int(os.getenv("DEFAULT_MAX_LEVEL3_NODES", "20"))
    DEFAULT_MAX_LEVEL4 = int(os.getenv("DEFAULT_MAX_LEVEL4_NODES", "30"))
    
    def __init__(self):
        # 动态加载所有配置的服务商
        self.providers = self._load_providers()
        
        # 读取流式输出终端显示配置
        self.stream_to_terminal = os.getenv("STREAM_TO_TERMINAL", "true").lower() == "true"
        
        # 初始化日志记录器
        init_logger(stream_to_terminal=self.stream_to_terminal)
        
        # 任务中断控制
        self.current_task_id = None
        self.cancel_callback = None
        self.sse_callback = None  # SSE消息发送回调函数
        
        # 提示词配置
        self.prompts_file = os.path.join(os.path.dirname(__file__), 'prompts.json')
        self.prompts_config = self._load_prompts()
        self.current_prompt_template = self.prompts_config.get('current', 'default')
        
        # 根据默认provider初始化
        self.current_provider = os.getenv("DEFAULT_PROVIDER", "deepseek")
        if self.current_provider not in self.providers:
            available = list(self.providers.keys())
            if available:
                self.current_provider = available[0]
                log_warning(f"默认provider未配置，自动切换到: {self.current_provider}")
            else:
                raise ValueError("未配置任何API provider，请在.env中配置")
        
        self._init_api_client()
    
    def _load_providers(self):
        """动态加载所有配置的服务商"""
        providers = {}
        # 扫描环境变量，找出所有 PROVIDER_XXX_API_KEY
        for key, value in os.environ.items():
            if key.startswith("PROVIDER_") and key.endswith("_API_KEY") and value:
                # 提取服务商名称，如 PROVIDER_SILICONFLOW_API_KEY -> siliconflow
                name = key.replace("PROVIDER_", "").replace("_API_KEY", "").lower()
                providers[name] = {
                    "api_key": value,
                    "base_url": os.getenv(f"PROVIDER_{name.upper()}_BASE_URL", ""),
                    "model": os.getenv(f"PROVIDER_{name.upper()}_MODEL", "")
                }
        return providers
    
    def _load_prompts(self):
        """加载提示词配置"""
        try:
            if os.path.exists(self.prompts_file):
                with open(self.prompts_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            log_warning(f"加载提示词配置失败: {e}")
        # 返回默认配置
        return {
            "current": "default",
            "templates": {
                "default": {
                    "name": "通用图谱提取",
                    "description": "适用于各类文本的通用提示词模板",
                    "extract_entities": "请简洁分析以下文本，快速提取关键实体和关系，构建4层层级图谱。\n\n文本内容：\n{text}\n\n直接输出 JSON：",
                    "extract_topic": "请阅读下面的内容，提取一个 4-6 个字的主题词。\n\n只输出一行 JSON，格式严格为：\n{{\"topic\":\"XXXX\"}}\n\n内容开头：\n{text}"
                }
            }
        }
    
    def _save_prompts(self):
        """保存提示词配置"""
        try:
            with open(self.prompts_file, 'w', encoding='utf-8') as f:
                json.dump(self.prompts_config, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            log_error(f"保存提示词配置失败: {e}")
            return False
    
    def get_prompt_templates(self):
        """获取所有提示词模板"""
        templates = self.prompts_config.get('templates', {})
        result = {}
        for key, value in templates.items():
            result[key] = {
                'name': value.get('name', key),
                'description': value.get('description', ''),
                'extract_entities': value.get('extract_entities', ''),
                'extract_topic': value.get('extract_topic', '')
            }
        return result
    
    def get_current_prompt_template(self):
        """获取当前使用的提示词模板名称"""
        return self.current_prompt_template
    
    def switch_prompt_template(self, template_name):
        """切换提示词模板"""
        if template_name not in self.prompts_config.get('templates', {}):
            available = ", ".join(self.prompts_config.get('templates', {}).keys())
            raise ValueError(f"不存在的提示词模板: {template_name}，可用: {available}")
        self.current_prompt_template = template_name
        self.prompts_config['current'] = template_name
        self._save_prompts()
        log_info(f"已切换到提示词模板: {template_name}")
        return True
    
    def update_prompt_template(self, template_name, template_data):
        """更新或创建提示词模板"""
        if 'templates' not in self.prompts_config:
            self.prompts_config['templates'] = {}
        self.prompts_config['templates'][template_name] = template_data
        self._save_prompts()
        log_info(f"已更新提示词模板: {template_name}")
        return True
    
    def delete_prompt_template(self, template_name):
        """删除提示词模板"""
        if template_name == 'default':
            raise ValueError("不能删除默认模板")
        if template_name in self.prompts_config.get('templates', {}):
            del self.prompts_config['templates'][template_name]
            if self.current_prompt_template == template_name:
                self.current_prompt_template = 'default'
                self.prompts_config['current'] = 'default'
            self._save_prompts()
            log_info(f"已删除提示词模板: {template_name}")
            return True
        return False
    
    def _get_prompt(self, prompt_type, **kwargs):
        """获取格式化后的提示词
        
        使用简单的字符串替换而非 .format()，避免 JSON 示例中的花括号被误解析
        """
        template = self.prompts_config.get('templates', {}).get(self.current_prompt_template, {})
        prompt_template = template.get(prompt_type, '')
        if not prompt_template:
            # 回退到默认模板
            template = self.prompts_config.get('templates', {}).get('default', {})
            prompt_template = template.get(prompt_type, '')
        
        # 使用简单的字符串替换，避免 .format() 误解析 JSON 中的花括号
        result = prompt_template
        for key, value in kwargs.items():
            placeholder = '{' + key + '}'
            result = result.replace(placeholder, str(value))
        
        return result
    
    def get_available_providers(self):
        """获取所有可用的服务商列表"""
        return list(self.providers.keys())
    
    def _init_api_client(self):
        """根据当前provider初始化API客户端（统一使用OpenAI SDK）"""
        if self.current_provider not in self.providers:
            raise ValueError(f"未配置的API provider: {self.current_provider}")
        
        config = self.providers[self.current_provider]
        
        from openai import OpenAI
        import httpx
        
        # 创建OpenAI客户端，兼容所有支持OpenAI格式的API
        http_client = httpx.Client(timeout=120.0)
        self.client = OpenAI(
            api_key=config["api_key"],
            base_url=config["base_url"],
            http_client=http_client
        )
        self.model_name = config["model"]
        log_info(f"[API] 已初始化 {self.current_provider} (model: {self.model_name})")
    
    def switch_api_provider(self, provider_name):
        """切换API provider"""
        if provider_name not in self.providers:
            available = ", ".join(self.providers.keys())
            raise ValueError(f"不支持的API provider: {provider_name}，可用: {available}")
        self.current_provider = provider_name
        self._init_api_client()
        log_info(f"已切换到API provider: {provider_name}")
    
    def set_task_context(self, task_id, cancel_callback, sse_callback=None):
        """设置任务上下文，用于中断控制和SSE消息发送"""
        self.current_task_id = task_id
        self.cancel_callback = cancel_callback
        self.sse_callback = sse_callback
    
    def _check_cancelled(self):
        """检查任务是否被取消"""
        if self.cancel_callback and self.current_task_id:
            return self.cancel_callback(self.current_task_id)
        return False
    
    def _call_api(self, messages, temperature=0.5, max_tokens=4000, stream=True):
        """统一的API调用接口，支持流式输出（使用OpenAI SDK）"""
        import sys
        
        log_info(f"    [API调用] {self.current_provider} (model: {self.model_name}, stream={stream})...")
        
        if stream:
            # 流式输出
            full_content = ""
            log_stream("    [流式输出] ", end="", flush=True)
            
            try:
                stream_response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=True
                )
                
                for chunk in stream_response:
                    # 检查任务是否被取消
                    if self._check_cancelled():
                        log_stream_line("\n    [任务中断] 流式输出已停止")
                        raise InterruptedError("任务已被用户取消")
                    
                    if chunk.choices and chunk.choices[0].delta.content:
                        content = chunk.choices[0].delta.content
                        full_content += content
                        log_stream(content, end="", flush=True)
                        # 通过SSE发送流式内容
                        if self.sse_callback and self.current_task_id:
                            self.sse_callback(self.current_task_id, content, 'stream')
                
                log_stream_line("")  # 换行
                log_info(f"    [API完成] 总长度: {len(full_content)} 字符")
                return full_content.strip()
                
            except InterruptedError:
                # 用户取消，直接抛出
                raise
            except Exception as e:
                # 网络错误等，记录已接收的内容长度，然后抛出异常让上层重试
                log_stream_line("")  # 换行
                error_msg = str(e)
                if "incomplete" in error_msg.lower() or "closed" in error_msg.lower() or "timeout" in error_msg.lower():
                    log_warning(f"    [API中断] 网络连接中断，已接收 {len(full_content)} 字符")
                raise  # 重新抛出异常，让上层的重试逻辑处理
        else:
            # 非流式输出
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=False
            )
            result = response.choices[0].message.content.strip()
            log_info(f"    [API完成] 总长度: {len(result)} 字符")
            return result
    
    def extract_text(self, file_path, enable_preprocess=True):
        """根据文件扩展名自动选择提取方法，并可选进行预处理
        
        Args:
            file_path: 文件路径
            enable_preprocess: 是否启用预处理（Reranker筛选），默认True
        """
        ext = os.path.splitext(file_path)[1].lower()
        
        extractors = {
            '.docx': self._extract_from_docx,
            '.doc': self._extract_from_doc,
            '.txt': self._extract_from_txt,
            '.md': self._extract_from_txt,  # Markdown 当作纯文本处理
            '.json': self._extract_from_json,
            '.ass': self._extract_from_ass,
            '.srt': self._extract_from_srt,
        }
        
        extractor = extractors.get(ext)
        if extractor:
            text = extractor(file_path)
            
            # 对字幕文件进行预处理（如果启用）
            if enable_preprocess and ext in ['.ass', '.srt']:
                preprocessor = get_preprocessor()
                if preprocessor.is_enabled():
                    log_info(f"[预处理] 检测到字幕文件，启用 Reranker 筛选...")
                    text = preprocess_text(text, ext)
            
            return text
        else:
            supported = ', '.join(extractors.keys())
            raise ValueError(f"不支持的文件格式: {ext}，支持的格式: {supported}")
    
    def _extract_from_docx(self, file_path):
        """从 .docx 文档中提取文本，包括表格"""
        doc = Document(file_path)
        text_content = []
        
        # 提取段落文本
        for paragraph in doc.paragraphs:
            if paragraph.text.strip():
                text_content.append(paragraph.text.strip())
        
        # 提取表格文本
        for table in doc.tables:
            for row in table.rows:
                row_text = []
                for cell in row.cells:
                    if cell.text.strip():
                        row_text.append(cell.text.strip())
                if row_text:
                    text_content.append(' '.join(row_text))
        
        return '\n'.join(text_content)
    
    def _extract_from_doc(self, file_path):
        """从 .doc 文档中提取文本（需要 antiword 或转换）"""
        import subprocess
        try:
            # 尝试使用 antiword（macOS: brew install antiword）
            result = subprocess.run(['antiword', file_path], capture_output=True, text=True)
            if result.returncode == 0:
                return result.stdout
        except FileNotFoundError:
            pass
        
        # 备选：尝试使用 textract
        try:
            import textract
            return textract.process(file_path).decode('utf-8')
        except ImportError:
            pass
        
        raise ValueError(".doc 格式需要安装 antiword (brew install antiword) 或 textract")
    
    def _extract_from_txt(self, file_path):
        """从纯文本文件中提取（.txt, .md）"""
        encodings = ['utf-8', 'gbk', 'gb2312', 'utf-16', 'latin-1']
        for encoding in encodings:
            try:
                with open(file_path, 'r', encoding=encoding) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue
        raise ValueError(f"无法解码文件: {file_path}")
    
    def _extract_from_json(self, file_path):
        """从 JSON 文件中提取文本（假设有 text/content 字段或直接序列化）"""
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # 尝试常见的文本字段
        if isinstance(data, str):
            return data
        if isinstance(data, dict):
            for key in ['text', 'content', 'transcript', 'body', 'message']:
                if key in data:
                    return data[key] if isinstance(data[key], str) else json.dumps(data[key], ensure_ascii=False)
        if isinstance(data, list):
            # 假设是对话列表
            texts = []
            for item in data:
                if isinstance(item, dict):
                    for key in ['text', 'content', 'message']:
                        if key in item:
                            texts.append(str(item[key]))
                            break
                elif isinstance(item, str):
                    texts.append(item)
            if texts:
                return '\n'.join(texts)
        
        # 兜底：序列化整个 JSON
        return json.dumps(data, ensure_ascii=False, indent=2)
    
    def _extract_from_ass(self, file_path):
        """从 ASS 字幕文件中提取文本"""
        text_content = []
        with open(file_path, 'r', encoding='utf-8-sig') as f:
            content = f.read()
        
        # 检查是否为标准 ASS 格式
        if '[Events]' in content and 'Dialogue:' in content:
            # 标准 ASS 格式处理
            in_events = False
            for line in content.split('\n'):
                line = line.strip()
                if line == '[Events]':
                    in_events = True
                    continue
                if in_events and line.startswith('Dialogue:'):
                    # ASS 格式: Dialogue: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
                    parts = line.split(',', 9)
                    if len(parts) >= 10:
                        text = parts[9]
                        # 移除 ASS 特效标签 {\xxx}
                        text = re.sub(r'\{[^}]*\}', '', text)
                        # 移除换行符 \N
                        text = text.replace('\\N', ' ').replace('\\n', ' ')
                        if text.strip():
                            text_content.append(text.strip())
        else:
            # 非标准 ASS 格式，作为纯文本处理
            log_warning(f"文件不是标准ASS格式，将作为纯文本处理: {file_path}")
            for line in content.split('\n'):
                line = line.strip()
                if line and not line.startswith('[') and not line.startswith('Format:'):
                    text_content.append(line)
        
        return '\n'.join(text_content)
    
    def _extract_from_srt(self, file_path):
        """从 SRT 字幕文件中提取文本"""
        text_content = []
        with open(file_path, 'r', encoding='utf-8-sig') as f:
            lines = f.readlines()
        
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            # 跳过序号行和时间轴行
            if line.isdigit():
                i += 1
                continue
            if '-->' in line:
                i += 1
                continue
            if line:
                text_content.append(line)
            i += 1
        
        return '\n'.join(text_content)
    
    # 保留旧方法名以兼容
    def extract_text_from_docx(self, file_path):
        """兼容旧代码，自动识别文件格式"""
        return self.extract_text(file_path)
    
    def _split_text_with_overlap(self, text, chunk_size=4000, overlap=500):
        """
        将长文本分成多个重叠的段落
        
        Args:
            text: 原始文本
            chunk_size: 每段的字符数
            overlap: 段落之间的重叠字符数
        
        Returns:
            List of text chunks
        """
        if len(text) <= chunk_size:
            return [text]
        
        chunks = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            chunk = text[start:end]
            chunks.append(chunk)
            
            # 下一段的起始位置 = 当前结束位置 - 重叠部分
            start = end - overlap
            
            # 如果剩余文本不足 overlap，就结束
            if start >= len(text):
                break
        
        return chunks
    
    def _extract_from_single_chunk(self, text_chunk, chunk_index, total_chunks, max_nodes_config, max_retries=3):
        """
        从单个文本段落提取话题（内部方法），带重试机制
        
        Args:
            text_chunk: 文本段落
            chunk_index: 段落索引
            total_chunks: 总段落数
            max_nodes_config: 节点数量上限配置
            max_retries: 最大重试次数
        
        Returns:
            graph_data dict or None
        """
        max_level2 = max_nodes_config.get('level2', self.DEFAULT_MAX_LEVEL2) if max_nodes_config else self.DEFAULT_MAX_LEVEL2
        max_level3 = max_nodes_config.get('level3', self.DEFAULT_MAX_LEVEL3) if max_nodes_config else self.DEFAULT_MAX_LEVEL3
        max_level4 = max_nodes_config.get('level4', self.DEFAULT_MAX_LEVEL4) if max_nodes_config else self.DEFAULT_MAX_LEVEL4
        
        # 分段时每段的节点上限适当减少，避免单段过多
        per_chunk_level2 = max(3, max_level2 // 2)
        per_chunk_level3 = max(5, max_level3 // 2)
        per_chunk_level4 = max(8, max_level4 // 2)
        
        prompt = self._get_prompt(
            'extract_entities',
            text=text_chunk,
            max_level2=per_chunk_level2,
            max_level3=per_chunk_level3,
            max_level4=per_chunk_level4
        )
        
        for attempt in range(max_retries):
            try:
                # 检查是否被取消
                if self._check_cancelled():
                    log_warning(f"    [段落{chunk_index+1}/{total_chunks}] 任务已取消")
                    return None
                
                result = self._call_api(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.5,
                    max_tokens=4000
                )
                
                # 检查返回结果是否为空或过短（可能是网络中断）
                if not result or len(result) < 50:
                    raise Exception("API 返回内容过短，可能是网络中断")
                
                json_str = None
                if result.startswith('{'):
                    json_str = result
                else:
                    json_match = re.search(r'\{[\s\S]*\}', result)
                    if json_match:
                        json_str = json_match.group()
                
                if json_str:
                    json_str = json_str.replace('，', ',').replace('：', ':')
                    graph_data = json.loads(json_str)
                    
                    if 'nodes' in graph_data and 'edges' in graph_data:
                        # 分段提取时放宽节点数量要求
                        if len(graph_data['nodes']) >= 5:
                            log_info(f"    [段落{chunk_index+1}/{total_chunks}] 提取到 {len(graph_data['nodes'])} 个节点")
                            return graph_data
                        else:
                            log_warning(f"    [段落{chunk_index+1}/{total_chunks}] 节点数量不足({len(graph_data['nodes'])}个)")
                            # 节点不足不重试，直接返回 None
                            return None
                else:
                    raise Exception("无法从返回内容中提取 JSON")
                    
            except json.JSONDecodeError as e:
                log_warning(f"    [段落{chunk_index+1}/{total_chunks}] JSON解析失败 (尝试 {attempt+1}/{max_retries}): {e}")
            except Exception as e:
                log_warning(f"    [段落{chunk_index+1}/{total_chunks}] 提取失败 (尝试 {attempt+1}/{max_retries}): {e}")
            
            # 重试前等待一小段时间
            if attempt < max_retries - 1:
                import time
                wait_time = 2 * (attempt + 1)  # 递增等待：2秒、4秒
                log_info(f"    [段落{chunk_index+1}/{total_chunks}] {wait_time}秒后重试...")
                time.sleep(wait_time)
        
        log_error(f"    [段落{chunk_index+1}/{total_chunks}] 所有重试均失败，跳过此段落")
        return None
    
    def _merge_chunk_results(self, chunk_results, max_nodes_config):
        """
        合并多个段落的提取结果，去重并重建树形结构
        
        Args:
            chunk_results: List of graph_data dicts from each chunk
            max_nodes_config: 节点数量上限配置
        
        Returns:
            Merged graph_data dict
        """
        max_level2 = max_nodes_config.get('level2', self.DEFAULT_MAX_LEVEL2) if max_nodes_config else self.DEFAULT_MAX_LEVEL2
        max_level3 = max_nodes_config.get('level3', self.DEFAULT_MAX_LEVEL3) if max_nodes_config else self.DEFAULT_MAX_LEVEL3
        max_level4 = max_nodes_config.get('level4', self.DEFAULT_MAX_LEVEL4) if max_nodes_config else self.DEFAULT_MAX_LEVEL4
        
        # 收集所有非 root 节点，按 level 分组
        level2_nodes = {}  # label -> node
        level3_nodes = {}  # label -> (node, parent_label)
        level4_nodes = {}  # label -> (node, parent_label)
        
        # 用于追踪父子关系
        for graph_data in chunk_results:
            if not graph_data:
                continue
            
            nodes = graph_data.get('nodes', [])
            edges = graph_data.get('edges', [])
            
            # 构建 id -> node 和 id -> label 映射
            id_to_node = {n['id']: n for n in nodes}
            id_to_label = {n['id']: n.get('label', '') for n in nodes}
            
            # 构建 child_id -> parent_id 映射
            child_to_parent = {}
            for edge in edges:
                child_to_parent[edge['target']] = edge['source']
            
            # 收集各层节点
            for node in nodes:
                level = node.get('level', 0)
                label = node.get('label', '').strip()
                
                if level == 1 or not label:
                    continue
                
                if level == 2:
                    if label not in level2_nodes:
                        level2_nodes[label] = node.copy()
                
                elif level == 3:
                    parent_id = child_to_parent.get(node['id'])
                    parent_label = id_to_label.get(parent_id, '') if parent_id else ''
                    key = f"{parent_label}::{label}"
                    if key not in level3_nodes:
                        level3_nodes[key] = (node.copy(), parent_label)
                
                elif level == 4:
                    parent_id = child_to_parent.get(node['id'])
                    parent_label = id_to_label.get(parent_id, '') if parent_id else ''
                    key = f"{parent_label}::{label}"
                    if key not in level4_nodes:
                        level4_nodes[key] = (node.copy(), parent_label)
        
        # 限制每层节点数量
        level2_list = list(level2_nodes.values())[:max_level2]
        level2_labels = {n['label'] for n in level2_list}
        
        # 过滤 level3：只保留父节点在 level2_labels 中的
        level3_filtered = [(n, p) for key, (n, p) in level3_nodes.items() if p in level2_labels]
        level3_list = [n for n, p in level3_filtered[:max_level3]]
        level3_labels = {n['label'] for n in level3_list}
        level3_parent_map = {n['label']: p for n, p in level3_filtered[:max_level3]}
        
        # 过滤 level4：只保留父节点在 level3_labels 中的
        level4_filtered = [(n, p) for key, (n, p) in level4_nodes.items() if p in level3_labels]
        level4_list = [n for n, p in level4_filtered[:max_level4]]
        level4_parent_map = {n['label']: p for n, p in level4_filtered[:max_level4]}
        
        # 重建节点列表，分配新 ID
        merged_nodes = []
        merged_edges = []
        label_to_id = {}
        
        # Root 节点
        # 对于直播话题提取场景，统一使用「本场直播话题」作为中心节点
        if getattr(self, "current_prompt_template", "") == "livestream":
            root_label = "本场直播话题"
        else:
            root_label = "话题总览"
        root_node = {"id": "root", "label": root_label, "level": 1, "category": "主题"}
        merged_nodes.append(root_node)
        label_to_id["__root__"] = "root"
        
        # Level 2 节点
        for i, node in enumerate(level2_list):
            new_id = f"l2_{i+1}"
            node['id'] = new_id
            node['level'] = 2
            node['category'] = "核心话题"
            merged_nodes.append(node)
            label_to_id[node['label']] = new_id
            merged_edges.append({"source": "root", "target": new_id, "type": "层级", "label": "次级"})
        
        # Level 3 节点
        for i, node in enumerate(level3_list):
            new_id = f"l3_{i+1}"
            node['id'] = new_id
            node['level'] = 3
            node['category'] = "子话题"
            merged_nodes.append(node)
            
            parent_label = level3_parent_map.get(node['label'], '')
            parent_id = label_to_id.get(parent_label, 'root')
            label_to_id[node['label']] = new_id
            merged_edges.append({"source": parent_id, "target": new_id, "type": "层级", "label": "次级"})
        
        # Level 4 节点
        for i, node in enumerate(level4_list):
            new_id = f"l4_{i+1}"
            node['id'] = new_id
            node['level'] = 4
            node['category'] = "表达观点"
            merged_nodes.append(node)
            
            parent_label = level4_parent_map.get(node['label'], '')
            parent_id = label_to_id.get(parent_label)
            if not parent_id:
                # 如果找不到父节点，尝试挂到任意 level3 节点
                if level3_list:
                    parent_id = label_to_id.get(level3_list[0]['label'], 'root')
                else:
                    continue  # 跳过这个节点
            merged_edges.append({"source": parent_id, "target": new_id, "type": "层级", "label": "次级"})
        
        log_info(f"    [合并结果] 共 {len(merged_nodes)} 个节点, {len(merged_edges)} 条边")
        log_info(f"    [合并结果] L2={len(level2_list)}, L3={len(level3_list)}, L4={len(level4_list)}")
        
        return {"nodes": merged_nodes, "edges": merged_edges}
    
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
                    print(f"  过滤同层/逆向边: {source_id}(L{source_level}) → {target_id}(L{target_level})")
            else:
                # 如果节点 ID 不在映射中，保留边（避免误删）
                cleaned_edges.append(edge)
        
        if removed_count > 0:
            log_info(f"  共过滤 {removed_count} 条非树形边")
        
        return {
            'nodes': nodes,
            'edges': cleaned_edges
        }
    
    def extract_entities_with_glm(self, text, filename, max_nodes_config=None):
        """使用GLM模型提取实体和关系，支持长文本分段提取"""
        
        # 获取节点数量上限配置
        max_level2 = max_nodes_config.get('level2', self.DEFAULT_MAX_LEVEL2) if max_nodes_config else self.DEFAULT_MAX_LEVEL2
        max_level3 = max_nodes_config.get('level3', self.DEFAULT_MAX_LEVEL3) if max_nodes_config else self.DEFAULT_MAX_LEVEL3
        max_level4 = max_nodes_config.get('level4', self.DEFAULT_MAX_LEVEL4) if max_nodes_config else self.DEFAULT_MAX_LEVEL4
        
        log_info(f"    [AI提取] 使用提示词模板: {self.current_prompt_template}")
        log_info(f"    [AI提取] 节点数量上限: 第2层≤{max_level2}, 第3层≤{max_level3}, 第4层≤{max_level4}")
        log_info(f"    [AI提取] 文本总长度: {len(text)} 字符")
        
        # 分段提取阈值：超过 5000 字符时启用分段
        CHUNK_THRESHOLD = 5000
        CHUNK_SIZE = 4000
        OVERLAP = 500
        
        if len(text) > CHUNK_THRESHOLD:
            # === 分段提取模式 ===
            chunks = self._split_text_with_overlap(text, chunk_size=CHUNK_SIZE, overlap=OVERLAP)
            log_info(f"    [分段提取] 文本过长，分成 {len(chunks)} 段进行提取（每段 {CHUNK_SIZE} 字符，重叠 {OVERLAP} 字符）")
            
            chunk_results = []
            failed_chunks = []
            
            for i, chunk in enumerate(chunks):
                # 检查是否被取消
                if self._check_cancelled():
                    log_warning("    [分段提取] 任务已取消")
                    return None
                
                log_info(f"    [分段提取] 正在处理第 {i+1}/{len(chunks)} 段...")
                result = self._extract_from_single_chunk(chunk, i, len(chunks), max_nodes_config)
                if result:
                    chunk_results.append(result)
                else:
                    failed_chunks.append(i + 1)
            
            # 统计结果
            success_count = len(chunk_results)
            total_count = len(chunks)
            
            if failed_chunks:
                log_warning(f"    [分段提取] 失败的段落: {failed_chunks}")
            
            # 只要有至少 1 段成功，就尝试合并（容错处理）
            if success_count >= 1:
                success_rate = success_count / total_count * 100
                log_info(f"    [分段提取] 成功提取 {success_count}/{total_count} 段 ({success_rate:.0f}%)，开始合并...")
                
                if success_rate < 50:
                    log_warning(f"    [分段提取] 成功率较低，结果可能不完整")
                
                merged_graph = self._merge_chunk_results(chunk_results, max_nodes_config)
                
                # 清洗合并后的图结构
                merged_graph = self.clean_graph_structure(merged_graph)
                
                # 更新 root 节点的 label（用主题提取）；直播模板下保持固定文案
                if getattr(self, "current_prompt_template", "") != "livestream":
                    topic = self.extract_topic_with_glm(text, filename)
                    for node in merged_graph['nodes']:
                        if node['id'] == 'root':
                            node['label'] = topic
                            break
                
                log_info(f"    [分段提取] 最终结果: {len(merged_graph['nodes'])} 个节点，{len(merged_graph['edges'])} 条边")
                return merged_graph
            else:
                log_warning("    [分段提取] 所有段落提取失败，回退到简化结构")
                fallback_topic = self.extract_topic_with_glm(text, filename)
                return self.create_simple_structure(fallback_topic, text)
        
        else:
            # === 单次提取模式（短文本）===
            prompt = self._get_prompt(
                'extract_entities',
                text=text,
                max_level2=max_level2,
                max_level3=max_level3,
                max_level4=max_level4
            )

            max_retries = 3
            for attempt in range(max_retries):
                try:
                    result = self._call_api(
                        messages=[
                            {"role": "user", "content": prompt}
                        ],
                        temperature=0.5,
                        max_tokens=4000
                    )
                    
                    # 尝试多种方式提取JSON
                    json_str = None
                    
                    # 方法1: 直接解析
                    if result.startswith('{'):
                        json_str = result
                    else:
                        # 方法2: 使用正则提取
                        json_match = re.search(r'\{[\s\S]*\}', result)
                        if json_match:
                            json_str = json_match.group()
                    
                    if json_str:
                        # 尝试修复常见的JSON错误
                        json_str = json_str.replace('，', ',').replace('：', ':')
                        graph_data = json.loads(json_str)
                        
                        # 验证数据结构
                        if 'nodes' in graph_data and 'edges' in graph_data:
                            if len(graph_data['nodes']) >= 10:  # 放宽到至少10个节点
                                # 清洗图结构：过滤同层边，确保严格树形层级
                                graph_data = self.clean_graph_structure(graph_data)

                                # 直播话题提取场景下，固定 root 节点文案为「本场直播话题」
                                if getattr(self, "current_prompt_template", "") == "livestream":
                                    for node in graph_data['nodes']:
                                        if node.get('id') == 'root':
                                            node['label'] = "本场直播话题"
                                            break

                                log_info(f"成功提取 {len(graph_data['nodes'])} 个节点，{len(graph_data['edges'])} 条边")
                                return graph_data
                            else:
                                log_warning(f"节点数量不足({len(graph_data['nodes'])}个)，重试...")
                        else:
                            log_warning("JSON结构不完整，重试...")
                    else:
                        log_warning(f"无法提取JSON，尝试 {attempt + 1}/{max_retries}")
                        
                except json.JSONDecodeError as e:
                    log_error(f"JSON解析错误: {e}，尝试 {attempt + 1}/{max_retries}")
                except Exception as e:
                    log_error(f"GLM API调用失败: {e}，尝试 {attempt + 1}/{max_retries}")
            
            # 所有重试失败后，生成简化结构
            log_warning("GLM提取失败，生成基于主题的简化结构")
            fallback_topic = self.extract_topic_with_glm(text, filename)
            return self.create_simple_structure(fallback_topic, text)
    
    def extract_topic_with_glm(self, text, filename):
        """使用GLM从访谈内容中提取主题"""
        try:
            # 使用可配置的提示词
            prompt = self._get_prompt('extract_topic', text=text[:800])

            raw = self._call_api(
                messages=[
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2,
                max_tokens=50
            )
            log_debug(f"主题原始回复: {raw[:80]}...")

            topic = ""
            json_str = None
            # 直接是 JSON
            if raw.startswith("{"):
                json_str = raw
            else:
                # 从长文本中用正则抽取第一个 JSON 块
                m = re.search(r'\{[\s\S]*?\}', raw)
                if m:
                    json_str = m.group(0)

            if json_str:
                try:
                    data = json.loads(json_str)
                    if isinstance(data, dict):
                        topic = str(data.get("topic", "")).strip()
                except Exception as e:
                    log_error(f"主题JSON解析失败: {e}")

            # 清理可能的引号、符号和多余空格
            topic = topic.replace('"', '').replace("'", '').replace('《', '').replace('》', '')
            topic = topic.replace('：', '').replace(':', '').replace('。', '').strip()
            
            # 如果主题为空或长度不合适，回退到基于文件名
            if not topic or len(topic) < 2 or len(topic) > 10:
                # 尝试从文件名提取主题
                topic = self._extract_topic_from_filename(filename)
                log_warning(f"主题提取异常，使用文件名生成主题: {topic}")
            else:
                log_info(f"提取的主题: {topic}")
            
            return topic
            
        except Exception as e:
            log_error(f"主题提取失败: {e}")
            return self._extract_topic_from_filename(filename)
    
    def _extract_topic_from_filename(self, filename):
        """从文件名提取主题（备用方法）"""
        # 移除扩展名
        name = os.path.splitext(filename)[0]
        # 移除常见前缀
        for prefix in ['【直播回放】', '【录播】', '【']:
            if name.startswith(prefix):
                name = name[len(prefix):]
        # 移除常见后缀
        for suffix in ['】']:
            if suffix in name:
                name = name.split(suffix)[0]
        # 截取合适长度
        if len(name) > 8:
            name = name[:8]
        if len(name) < 2:
            name = "内容分析"
        return name
    
    def extract_categories_from_text(self, text):
        """从文本中提取6个主要类别"""
        try:
            prompt = f"""
访谈内容：{text[:800]}

提取6个主题类别，每个2-4字，用逗号分隔，直接输出：
"""
            
            result = self._call_api(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=100
            )
            categories_text = result.replace('，', ',').replace('、', ',')
            category_labels = [cat.strip() for cat in categories_text.split(',') if cat.strip()]
            
            if len(category_labels) >= 4:
                # 生成ID和标签对
                categories = []
                for i, label in enumerate(category_labels[:6]):
                    cat_id = f"cat{i+1}"
                    categories.append((cat_id, label))
                log_info(f"从文本提取的类别: {[c[1] for c in categories]}")
                return categories
            else:
                log_warning("提取的类别数量不足，使用默认类别")
                return None
        except Exception as e:
            log_error(f"类别提取失败: {e}")
            return None
    
    def create_simple_structure(self, topic, text):
        """基于文本关键词创建简化结构"""
        log_info(f"正在基于文本关键词生成图结构...")
        
        # 从文本中提取关键词来确定类别
        categories = self.extract_categories_from_text(text)
        if not categories:
            # 如果提取失败，使用默认类别
            categories = [
                ("service", "服务态度"),
                ("medical", "医疗质量"),
                ("environment", "环境设施"),
                ("management", "管理制度"),
                ("emotion", "情感体验"),
                ("behavior", "行为反应")
            ]
        
        nodes = [{"id": "root", "label": topic, "level": 1, "category": "主题"}]
        edges = []
        
        # 添加第2层类别节点
        for cat_id, cat_label in categories:
            nodes.append({"id": cat_id, "label": cat_label, "level": 2, "category": "分类"})
            edges.append({"source": "root", "target": cat_id, "type": "层级", "label": "次级"})
        
        # 为每个类别添加4个第3层节点
        level3_labels = {
            "service": ["服务热情", "态度友好", "响应及时", "处理细致"],
            "medical": ["技术专业", "诊断准确", "治疗有效", "护理周到"],
            "environment": ["环境整洁", "设施完善", "布局合理", "氛围温馨"],
            "management": ["制度规范", "流程清晰", "管理有序", "监督到位"],
            "emotion": ["感到满意", "建立信任", "心存感激", "印象深刻"],
            "behavior": ["积极配合", "主动沟通", "耐心等待", "理解支持"]
        }
        
        level4_labels = ["表现突出", "效果显著", "体验良好", "印象深刻", "值得称赞", "令人满意"]
        
        node_counter = 0
        for cat_id, cat_label in categories:
            labels = level3_labels.get(cat_id, [f"{cat_label}表现{i+1}" for i in range(4)])
            for i, label in enumerate(labels):
                node_id = f"{cat_id}_item{i+1}"
                nodes.append({"id": node_id, "label": label, "level": 3, "category": "表现"})
                edges.append({"source": cat_id, "target": node_id, "type": "层级", "label": "次级"})
                
                # 部分第3层节点添加第4层描述
                if i < 2:  # 每个类别的前2个第3层节点添加描述
                    for j in range(2):
                        desc_id = f"{node_id}_desc{j+1}"
                        desc_label = level4_labels[node_counter % len(level4_labels)]
                        nodes.append({"id": desc_id, "label": desc_label, "level": 4, "category": "描述"})
                        edges.append({"source": node_id, "target": desc_id, "type": "层级", "label": "次级"})
                        node_counter += 1
        
        log_info(f"生成了 {len(nodes)} 个节点，{len(edges)} 条边")
        return {"nodes": nodes, "edges": edges}
    
    def create_default_structure(self, topic="医院服务体验访谈"):
        """创建默认的层次结构，包含50-60个节点"""
        nodes = [
            # 第1层 - 主题
            {"id": "root", "label": topic, "level": 1, "category": "主题"},
            
            # 第2层 - 主要类别 (6个)
            {"id": "service_attitude", "label": "服务态度", "level": 2, "category": "服务评价"},
            {"id": "medical_quality", "label": "医疗质量", "level": 2, "category": "医疗评价"},
            {"id": "environment_facility", "label": "环境设施", "level": 2, "category": "硬件评价"},
            {"id": "management_system", "label": "管理制度", "level": 2, "category": "管理评价"},
            {"id": "emotional_experience", "label": "情感体验", "level": 2, "category": "情感评价"},
            {"id": "behavior_response", "label": "行为反应", "level": 2, "category": "行为评价"},
        ]
        
        # 第3层 - 具体表现 (24个)
        level3_nodes = [
            # 服务态度下的子节点
            {"id": "enthusiastic_service", "label": "服务热情", "level": 3, "category": "态度表现"},
            {"id": "patient_handling", "label": "办事耐心", "level": 3, "category": "态度表现"},
            {"id": "polite_treatment", "label": "待人有礼", "level": 3, "category": "态度表现"},
            {"id": "meticulous_handling", "label": "处理细致", "level": 3, "category": "态度表现"},
            
            # 医疗质量下的子节点
            {"id": "thoughtful_nursing", "label": "护理体贴", "level": 3, "category": "护理质量"},
            {"id": "dedicated_work", "label": "工作敬业", "level": 3, "category": "工作态度"},
            {"id": "timely_response", "label": "响应及时", "level": 3, "category": "响应效率"},
            {"id": "noble_medical_ethics", "label": "医德高尚", "level": 3, "category": "职业道德"},
            
            # 环境设施下的子节点
            {"id": "complete_facilities", "label": "设施齐全", "level": 3, "category": "设施评价"},
            {"id": "comfortable_environment", "label": "环境舒适", "level": 3, "category": "环境评价"},
            {"id": "advanced_equipment", "label": "设备先进", "level": 3, "category": "设备评价"},
            {"id": "clean_sanitation", "label": "卫生清洁", "level": 3, "category": "卫生评价"},
            
            # 管理制度下的子节点
            {"id": "perfect_system", "label": "制度完善", "level": 3, "category": "制度评价"},
            {"id": "system_culture", "label": "制度文化", "level": 3, "category": "文化建设"},
            {"id": "material_culture", "label": "物质文化", "level": 3, "category": "文化建设"},
            {"id": "behavioral_culture", "label": "行为文化", "level": 3, "category": "文化建设"},
            
            # 情感体验下的子节点
            {"id": "satisfaction", "label": "满意", "level": 3, "category": "情感表达"},
            {"id": "trust", "label": "信任", "level": 3, "category": "情感表达"},
            {"id": "gratitude", "label": "感谢", "level": 3, "category": "情感表达"},
            {"id": "pride", "label": "骄傲", "level": 3, "category": "情感表达"},
            
            # 行为反应下的子节点
            {"id": "acceptance", "label": "接受", "level": 3, "category": "行为表现"},
            {"id": "understanding", "label": "理解", "level": 3, "category": "行为表现"},
            {"id": "support", "label": "支持", "level": 3, "category": "行为表现"},
            {"id": "encouragement", "label": "鼓励", "level": 3, "category": "行为表现"},
        ]
        
        # 第4层 - 详细描述 (29个)
        level4_nodes = [
            # 服务热情的详细描述
            {"id": "warm_greeting", "label": "热情问候", "level": 4, "category": "具体行为"},
            {"id": "active_help", "label": "主动帮助", "level": 4, "category": "具体行为"},
            {"id": "friendly_communication", "label": "友好沟通", "level": 4, "category": "具体行为"},
            {"id": "caring_attitude", "label": "关怀态度", "level": 4, "category": "具体行为"},
            
            # 医疗质量的详细描述
            {"id": "professional_skills", "label": "专业技能", "level": 4, "category": "专业能力"},
            {"id": "accurate_diagnosis", "label": "准确诊断", "level": 4, "category": "专业能力"},
            {"id": "effective_treatment", "label": "有效治疗", "level": 4, "category": "治疗效果"},
            {"id": "safety_assurance", "label": "安全保障", "level": 4, "category": "安全管理"},
            
            # 环境设施的详细描述
            {"id": "spacious_layout", "label": "布局宽敞", "level": 4, "category": "空间设计"},
            {"id": "good_ventilation", "label": "通风良好", "level": 4, "category": "环境质量"},
            {"id": "modern_decoration", "label": "装修现代", "level": 4, "category": "视觉环境"},
            {"id": "quiet_atmosphere", "label": "氛围安静", "level": 4, "category": "环境氛围"},
            
            # 管理制度的详细描述
            {"id": "clear_processes", "label": "流程清晰", "level": 4, "category": "流程管理"},
            {"id": "efficient_coordination", "label": "协调高效", "level": 4, "category": "协作管理"},
            {"id": "strict_supervision", "label": "监督严格", "level": 4, "category": "质量管理"},
            {"id": "continuous_improvement", "label": "持续改进", "level": 4, "category": "发展管理"},
            
            # 情感体验的详细描述
            {"id": "deep_impression", "label": "印象深刻", "level": 4, "category": "深度感受"},
            {"id": "emotional_resonance", "label": "情感共鸣", "level": 4, "category": "情感连接"},
            {"id": "spiritual_comfort", "label": "心灵慰藉", "level": 4, "category": "心理支持"},
            {"id": "sense_of_belonging", "label": "归属感", "level": 4, "category": "情感认同"},
            
            # 行为反应的详细描述
            {"id": "positive_feedback", "label": "积极反馈", "level": 4, "category": "反馈行为"},
            {"id": "constructive_suggestions", "label": "建设建议", "level": 4, "category": "建议行为"},
            {"id": "cooperative_attitude", "label": "合作态度", "level": 4, "category": "协作行为"},
            {"id": "word_of_mouth", "label": "口碑传播", "level": 4, "category": "传播行为"},
            
            # 其他详细描述
            {"id": "praise_recognition", "label": "赞扬认可", "level": 4, "category": "评价行为"},
            {"id": "respect_appreciation", "label": "敬重欣赏", "level": 4, "category": "态度表达"},
            {"id": "liking_preference", "label": "喜爱偏好", "level": 4, "category": "偏好表达"},
            {"id": "apology_forgiveness", "label": "道歉谅解", "level": 4, "category": "情感处理"},
            {"id": "advanced_concepts", "label": "理念先进", "level": 4, "category": "思想理念"},
        ]
        
        nodes.extend(level3_nodes)
        nodes.extend(level4_nodes)
        
        # 创建边关系
        edges = []
        
        # 第1层到第2层的连接
        edges.extend([
            {"source": "root", "target": "service_attitude", "type": "层级", "label": "次级"},
            {"source": "root", "target": "medical_quality", "type": "层级", "label": "次级"},
            {"source": "root", "target": "environment_facility", "type": "层级", "label": "次级"},
            {"source": "root", "target": "management_system", "type": "层级", "label": "次级"},
            {"source": "root", "target": "emotional_experience", "type": "层级", "label": "次级"},
            {"source": "root", "target": "behavior_response", "type": "层级", "label": "次级"},
        ])
        
        # 第2层到第3层的连接
        edges.extend([
            {"source": "service_attitude", "target": "enthusiastic_service", "type": "层级", "label": "次级"},
            {"source": "service_attitude", "target": "patient_handling", "type": "层级", "label": "次级"},
            {"source": "service_attitude", "target": "polite_treatment", "type": "层级", "label": "次级"},
            {"source": "service_attitude", "target": "meticulous_handling", "type": "层级", "label": "次级"},
            
            {"source": "medical_quality", "target": "thoughtful_nursing", "type": "层级", "label": "次级"},
            {"source": "medical_quality", "target": "dedicated_work", "type": "层级", "label": "次级"},
            {"source": "medical_quality", "target": "timely_response", "type": "层级", "label": "次级"},
            {"source": "medical_quality", "target": "noble_medical_ethics", "type": "层级", "label": "次级"},
            
            {"source": "environment_facility", "target": "complete_facilities", "type": "层级", "label": "次级"},
            {"source": "environment_facility", "target": "comfortable_environment", "type": "层级", "label": "次级"},
            {"source": "environment_facility", "target": "advanced_equipment", "type": "层级", "label": "次级"},
            {"source": "environment_facility", "target": "clean_sanitation", "type": "层级", "label": "次级"},
            
            {"source": "management_system", "target": "perfect_system", "type": "层级", "label": "次级"},
            {"source": "management_system", "target": "system_culture", "type": "层级", "label": "次级"},
            {"source": "management_system", "target": "material_culture", "type": "层级", "label": "次级"},
            {"source": "management_system", "target": "behavioral_culture", "type": "层级", "label": "次级"},
            
            {"source": "emotional_experience", "target": "satisfaction", "type": "层级", "label": "次级"},
            {"source": "emotional_experience", "target": "trust", "type": "层级", "label": "次级"},
            {"source": "emotional_experience", "target": "gratitude", "type": "层级", "label": "次级"},
            {"source": "emotional_experience", "target": "pride", "type": "层级", "label": "次级"},
            
            {"source": "behavior_response", "target": "acceptance", "type": "层级", "label": "次级"},
            {"source": "behavior_response", "target": "understanding", "type": "层级", "label": "次级"},
            {"source": "behavior_response", "target": "support", "type": "层级", "label": "次级"},
            {"source": "behavior_response", "target": "encouragement", "type": "层级", "label": "次级"},
        ])
        
        # 第3层到第4层的连接
        edges.extend([
            {"source": "enthusiastic_service", "target": "warm_greeting", "type": "层级", "label": "次级"},
            {"source": "enthusiastic_service", "target": "active_help", "type": "层级", "label": "次级"},
            {"source": "thoughtful_nursing", "target": "friendly_communication", "type": "层级", "label": "次级"},
            {"source": "thoughtful_nursing", "target": "caring_attitude", "type": "层级", "label": "次级"},
            
            {"source": "dedicated_work", "target": "professional_skills", "type": "层级", "label": "次级"},
            {"source": "dedicated_work", "target": "accurate_diagnosis", "type": "层级", "label": "次级"},
            {"source": "timely_response", "target": "effective_treatment", "type": "层级", "label": "次级"},
            {"source": "timely_response", "target": "safety_assurance", "type": "层级", "label": "次级"},
            
            {"source": "complete_facilities", "target": "spacious_layout", "type": "层级", "label": "次级"},
            {"source": "complete_facilities", "target": "good_ventilation", "type": "层级", "label": "次级"},
            {"source": "comfortable_environment", "target": "modern_decoration", "type": "层级", "label": "次级"},
            {"source": "comfortable_environment", "target": "quiet_atmosphere", "type": "层级", "label": "次级"},
            
            {"source": "perfect_system", "target": "clear_processes", "type": "层级", "label": "次级"},
            {"source": "perfect_system", "target": "efficient_coordination", "type": "层级", "label": "次级"},
            {"source": "system_culture", "target": "strict_supervision", "type": "层级", "label": "次级"},
            {"source": "system_culture", "target": "continuous_improvement", "type": "层级", "label": "次级"},
            
            {"source": "satisfaction", "target": "deep_impression", "type": "层级", "label": "次级"},
            {"source": "satisfaction", "target": "emotional_resonance", "type": "层级", "label": "次级"},
            {"source": "trust", "target": "spiritual_comfort", "type": "层级", "label": "次级"},
            {"source": "trust", "target": "sense_of_belonging", "type": "层级", "label": "次级"},
            
            {"source": "acceptance", "target": "positive_feedback", "type": "层级", "label": "次级"},
            {"source": "acceptance", "target": "constructive_suggestions", "type": "层级", "label": "次级"},
            {"source": "understanding", "target": "cooperative_attitude", "type": "层级", "label": "次级"},
            {"source": "understanding", "target": "word_of_mouth", "type": "层级", "label": "次级"},
            
            {"source": "pride", "target": "praise_recognition", "type": "层级", "label": "次级"},
            {"source": "pride", "target": "respect_appreciation", "type": "层级", "label": "次级"},
            {"source": "gratitude", "target": "liking_preference", "type": "层级", "label": "次级"},
            {"source": "gratitude", "target": "apology_forgiveness", "type": "层级", "label": "次级"},
            
            {"source": "advanced_equipment", "target": "advanced_concepts", "type": "层级", "label": "次级"},
        ])
        
        return {"nodes": nodes, "edges": edges}
    
    def draw_graph(self, nodes, edges, output_path):
        """Draw graph using graph_layout module and save JSON"""
        # Calculate positions and draw
        positions = graph_layout.calculate_positions_hierarchical(nodes, edges)
        # Determine node size based on node count
        n_nodes = len(nodes)
        node_size = 0.5 if n_nodes <= 30 else (0.4 if n_nodes <= 50 else 0.3)
        positions = graph_layout.adjust_positions_to_avoid_overlap(positions, nodes, node_size=node_size)
        graph_layout.draw_graph(nodes, edges, output_path, positions=positions)
        
        # Save JSON file
        json_path = output_path.replace('.png', '.json')
        graph_data = {
            "nodes": nodes,
            "edges": edges,
            "metadata": {
                "total_nodes": len(nodes),
                "total_edges": len(edges),
                "levels": list(set(node['level'] for node in nodes)),
                "categories": list(set(node.get('category', '') for node in nodes))
            }
        }
        
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(graph_data, f, ensure_ascii=False, indent=2)
        
        log_info(f"图结构已保存到: {output_path}")
        log_info(f"JSON数据已保存到: {json_path}")
    
    def process_all_documents(self, data_folder="data"):
        """批量处理所有Word文档"""
        if not os.path.exists(data_folder):
            log_error(f"数据文件夹不存在: {data_folder}")
            return
        
        # 查找所有Word文档
        docx_files = []
        for file in os.listdir(data_folder):
            if file.endswith('.docx') and not file.endswith('_graph.png'):
                docx_files.append(os.path.join(data_folder, file))
        
        log_info(f"找到 {len(docx_files)} 个Word文档")
        
        # 处理每个文档
        results = []
        for docx_file in docx_files:
            try:
                log_info(f"\n{'='*50}")
                result = self.process_single_document(docx_file)
                results.append(result)
                log_info(f"完成处理: {os.path.basename(docx_file)}")
            except Exception as e:
                log_error(f"处理 {docx_file} 时出错: {e}")
                results.append(None)
        
        log_info(f"\n{'='*50}")
        log_info(f"批量处理完成！成功处理 {sum(1 for r in results if r is not None)} 个文档")
        
        return results
    
    def process_single_document(self, docx_path):
        """处理单个Word文档"""
        log_info(f"正在处理: {docx_path}")
        
        # 提取文本
        text = self.extract_text_from_docx(docx_path)
        log_info(f"提取文本长度: {len(text)} 字符")
        
        # 提取实体和关系
        filename = os.path.basename(docx_path)
        graph_data = self.extract_entities_with_glm(text, filename)
        
        log_info(f"提取到 {len(graph_data['nodes'])} 个节点，{len(graph_data['edges'])} 条边")
        
        # 生成图结构
        output_path = docx_path.replace('.docx', '_graph.png')
        self.draw_graph(graph_data['nodes'], graph_data['edges'], output_path)
        
        return output_path

def get_test_file():
    """获取测试文件路径：优先使用环境变量，否则自动选择 data 目录中的第一个 .docx 文件"""
    # 优先使用环境变量中的配置
    test_file = os.getenv("TEST_FILE_PATH", "").strip()
    if test_file and os.path.exists(test_file):
        return test_file
    
    # 自动选择 data 目录中的第一个 .docx 文件
    data_folder = "data"
    if os.path.exists(data_folder):
        docx_files = [f for f in os.listdir(data_folder) if f.endswith('.docx')]
        if docx_files:
            return os.path.join(data_folder, docx_files[0])
    
    return None

# 主函数
def main():
    generator = InterviewGraphGenerator()
    
    print("访谈图结构生成器")
    print("1. 测试单个文档")
    print("2. 批量处理所有文档")
    
    choice = input("请选择处理模式 (1/2): ").strip()
    
    if choice == "1":
        # 测试单个文档
        test_file = get_test_file()
        if test_file:
            print(f"使用测试文件: {test_file}")
            result = generator.process_single_document(test_file)
            print(f"测试完成，生成的图: {result}")
        else:
            print("未找到测试文件。请在 .env 中设置 TEST_FILE_PATH 或在 data 目录中放入 .docx 文件")
    elif choice == "2":
        # 批量处理所有文档
        results = generator.process_all_documents()
        print("批量处理完成！生成的图片文件：")
        for result in results:
            if result:
                log_info(f"  - {result}")
    else:
        log_warning("无效选择，默认进行单个文档测试")
        test_file = get_test_file()
        if test_file:
            print(f"使用测试文件: {test_file}")
            result = generator.process_single_document(test_file)
            print(f"测试完成，生成的图: {result}")
        else:
            print("未找到测试文件。请在 .env 中设置 TEST_FILE_PATH 或在 data 目录中放入 .docx 文件")

if __name__ == "__main__":
    main()
