"""
文本预处理模块 - 使用 Reranker 筛选高质量内容

主要用于处理直播字幕等噪声较多的文本，在送给 LLM 提取图谱前：
1. 分句
2. 基础规则过滤（去除无意义短句）
3. Reranker 评分筛选（保留与"话题内容"相关度高的句子）
"""

import os
import re
import httpx
from typing import List, Tuple, Optional
from dotenv import load_dotenv
from logger_config import log_info, log_warning, log_error, log_debug

load_dotenv()


class TextPreprocessor:
    """文本预处理器，支持 Reranker 筛选"""
    
    # 用于 Reranker 评分的查询语句（评估句子是否包含有价值的话题信息）
    RERANKER_QUERY = "这是一段有信息量的直播聊天内容，包含具体话题、观点、经历或讨论"
    
    # 无意义内容的正则模式
    NOISE_PATTERNS = [
        r'^[哈嘿呵嗯啊噢哦嘻]+$',  # 纯语气词
        r'^[0-9]+$',  # 纯数字（如666）
        r'^[.。,，!！?？~～]+$',  # 纯标点
        r'^(谢谢|感谢|thanks|thx).*?(礼物|送的|打赏)',  # 感谢礼物
        r'^(欢迎|welcome).*?(来到|进入|直播间)',  # 欢迎语
        r'^(大家好|hello|hi|嗨|哈喽)',  # 打招呼
        r'^(拜拜|再见|bye|下播|结束)',  # 下播语
        r'^(关注|点赞|订阅|subscribe)',  # 引导关注
    ]
    
    # 最短有效句子长度
    MIN_SENTENCE_LENGTH = 4
    
    def __init__(self):
        """初始化预处理器"""
        self.enabled = os.getenv("ENABLE_RERANKER_PREPROCESS", "false").lower() == "true"
        self.api_key = os.getenv("RERANKER_API_KEY", "")
        self.base_url = os.getenv("RERANKER_BASE_URL", "https://api.siliconflow.cn/v1")
        self.model = os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
        self.top_ratio = float(os.getenv("RERANKER_TOP_RATIO", "0.8"))
        
        # 编译正则表达式
        self.noise_regex = [re.compile(p, re.IGNORECASE) for p in self.NOISE_PATTERNS]
    
    def is_enabled(self) -> bool:
        """检查预处理是否启用"""
        return self.enabled and bool(self.api_key)
    
    def preprocess(self, text: str, file_ext: str = "") -> str:
        """
        预处理文本
        
        Args:
            text: 原始文本
            file_ext: 文件扩展名（如 .ass），用于判断是否需要预处理
            
        Returns:
            处理后的文本
        """
        # 只对字幕文件启用预处理
        if file_ext.lower() not in ['.ass', '.srt']:
            log_debug(f"[预处理] 跳过非字幕文件: {file_ext}")
            return text
        
        if not self.is_enabled():
            log_debug("[预处理] Reranker 未启用，跳过预处理")
            return text
        
        log_info(f"[预处理] 开始 Reranker 筛选，原始文本长度: {len(text)} 字符")
        
        # 1. 分句
        sentences = self._split_sentences(text)
        log_info(f"[预处理] 分句完成，共 {len(sentences)} 句")
        
        # 2. 规则过滤
        filtered = self._rule_filter(sentences)
        log_info(f"[预处理] 规则过滤后剩余 {len(filtered)} 句")
        
        if len(filtered) < 10:
            log_warning("[预处理] 过滤后句子太少，跳过 Reranker")
            return '\n'.join(filtered)
        
        # 3. Reranker 评分筛选
        try:
            selected = self._reranker_filter(filtered)
            log_info(f"[预处理] Reranker 筛选后剩余 {len(selected)} 句")
            result = '\n'.join(selected)
            log_info(f"[预处理] 完成，处理后文本长度: {len(result)} 字符 (压缩率: {len(result)/len(text)*100:.1f}%)")
            return result
        except Exception as e:
            log_error(f"[预处理] Reranker 调用失败: {e}，返回规则过滤结果")
            return '\n'.join(filtered)
    
    def _split_sentences(self, text: str) -> List[str]:
        """
        分句：按换行符分割，并合并过短的句子
        """
        lines = text.split('\n')
        sentences = []
        buffer = ""
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # 如果当前行很短，尝试与下一行合并
            if len(line) < 10 and buffer:
                buffer += " " + line
            elif len(line) < 10:
                buffer = line
            else:
                if buffer:
                    sentences.append(buffer)
                    buffer = ""
                sentences.append(line)
        
        if buffer:
            sentences.append(buffer)
        
        return sentences
    
    def _rule_filter(self, sentences: List[str]) -> List[str]:
        """
        规则过滤：去除明显的噪声内容
        """
        result = []
        for sent in sentences:
            # 长度过滤
            if len(sent) < self.MIN_SENTENCE_LENGTH:
                continue
            
            # 正则过滤
            is_noise = False
            for pattern in self.noise_regex:
                if pattern.search(sent):
                    is_noise = True
                    break
            
            if not is_noise:
                result.append(sent)
        
        return result
    
    def _reranker_filter(self, sentences: List[str]) -> List[str]:
        """
        使用 Reranker API 评分并筛选句子
        支持两种模式：
        1. 专用 rerank 端点（如 SiliconFlow）
        2. OpenAI 兼容的 chat completions 端点（如 CherryIn qwen3-reranker）
        """
        if not sentences:
            return []
        
        # 使用专用 rerank 端点
        return self._reranker_filter_native_mode(sentences)
    
    def _reranker_filter_openai_mode(self, sentences: List[str]) -> List[str]:
        """
        使用 OpenAI 兼容接口调用 Reranker（如 qwen3-reranker）
        qwen3-reranker 通过 chat completions 接口，返回 true/false 判断相关性
        """
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        # 分批处理
        batch_size = 20  # OpenAI 模式每批少一些，避免超时
        all_scored = []
        
        for i in range(0, len(sentences), batch_size):
            batch = sentences[i:i + batch_size]
            log_info(f"[Reranker] 处理批次 {i//batch_size + 1}/{(len(sentences)-1)//batch_size + 1}...")
            
            for sent in batch:
                try:
                    # qwen3-reranker 的调用格式
                    payload = {
                        "model": self.model,
                        "messages": [
                            {
                                "role": "user",
                                "content": f"Query: {self.RERANKER_QUERY}\nDocument: {sent}"
                            }
                        ],
                        "max_tokens": 5
                    }
                    
                    with httpx.Client(timeout=15.0) as client:
                        response = client.post(url, headers=headers, json=payload)
                        response.raise_for_status()
                        data = response.json()
                    
                    # 解析返回结果
                    content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip().lower()
                    
                    # qwen3-reranker 返回 "true" 或 "false"
                    if "true" in content or "yes" in content or "是" in content:
                        score = 1.0
                    elif "false" in content or "no" in content or "否" in content:
                        score = 0.0
                    else:
                        # 尝试解析数字分数
                        try:
                            score = float(content)
                        except:
                            score = 0.5  # 无法解析时给中等分数
                    
                    all_scored.append((sent, score))
                    
                except Exception as e:
                    log_warning(f"[Reranker] 句子评分失败: {e}")
                    all_scored.append((sent, 0.5))
        
        # 按分数排序，保留高分句子
        all_scored.sort(key=lambda x: x[1], reverse=True)
        
        # 计算保留数量
        keep_count = max(10, int(len(all_scored) * self.top_ratio))
        
        # 筛选
        selected = [text for text, score in all_scored[:keep_count] if score >= self.min_score]
        
        # 如果筛选后太少，放宽条件
        if len(selected) < 10 and len(all_scored) >= 10:
            selected = [text for text, _ in all_scored[:20]]
        
        return selected
    
    def _reranker_filter_native_mode(self, sentences: List[str]) -> List[str]:
        """
        使用专用 rerank 端点（如 SiliconFlow bge-reranker）
        """
        url = f"{self.base_url}/rerank"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        # 分批处理
        batch_size = 100
        all_scored = []
        total_batches = (len(sentences) - 1) // batch_size + 1
        
        for i in range(0, len(sentences), batch_size):
            batch = sentences[i:i + batch_size]
            batch_num = i // batch_size + 1
            
            payload = {
                "model": self.model,
                "query": self.RERANKER_QUERY,
                "documents": batch,
                "top_n": len(batch),
                "return_documents": True
            }
            
            log_info(f"[Reranker] 处理批次 {batch_num}/{total_batches} ({len(batch)} 句)...")
            
            try:
                # 设置更细粒度的超时：连接5秒，读取60秒
                timeout = httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=5.0)
                with httpx.Client(timeout=timeout) as client:
                    response = client.post(url, headers=headers, json=payload)
                    response.raise_for_status()
                    data = response.json()
                
                for item in data.get("results", []):
                    score = item.get("relevance_score", 0)
                    text = item.get("document", {}).get("text", "")
                    if text:
                        all_scored.append((text, score))
                
                log_info(f"[Reranker] 批次 {batch_num} 完成，获得 {len(data.get('results', []))} 个结果")
                        
            except httpx.TimeoutException as e:
                log_warning(f"[Reranker] 批次 {batch_num} 超时: {e}，保留原句子")
                for sent in batch:
                    all_scored.append((sent, 0.5))
            except httpx.ConnectError as e:
                log_warning(f"[Reranker] 批次 {batch_num} 连接失败: {e}，保留原句子")
                for sent in batch:
                    all_scored.append((sent, 0.5))
            except Exception as e:
                log_warning(f"[Reranker] 批次 {batch_num} 调用失败: {e}，保留原句子")
                for sent in batch:
                    all_scored.append((sent, 0.5))
        
        # 按分数排序
        all_scored.sort(key=lambda x: x[1], reverse=True)
        
        # 计算保留数量（按比例，至少保留50句）
        keep_count = max(50, int(len(all_scored) * self.top_ratio))
        
        # 直接按排名保留 top N%（不用绝对分数阈值，因为 reranker 分数普遍较低）
        selected = [text for text, score in all_scored[:keep_count]]
        
        # 记录分数分布供调试
        if all_scored:
            top_score = all_scored[0][1]
            mid_idx = len(all_scored) // 2
            mid_score = all_scored[mid_idx][1] if mid_idx < len(all_scored) else 0
            cutoff_score = all_scored[keep_count-1][1] if keep_count <= len(all_scored) else 0
            log_debug(f"[Reranker] 分数分布: 最高={top_score:.4f}, 中位={mid_score:.4f}, 截止={cutoff_score:.4f}")
        
        return selected
    
    def get_stats(self, original: str, processed: str) -> dict:
        """获取预处理统计信息"""
        return {
            "original_length": len(original),
            "processed_length": len(processed),
            "compression_ratio": len(processed) / len(original) if original else 0,
            "original_lines": len(original.split('\n')),
            "processed_lines": len(processed.split('\n'))
        }


# 全局单例
_preprocessor = None

def get_preprocessor() -> TextPreprocessor:
    """获取预处理器单例"""
    global _preprocessor
    if _preprocessor is None:
        _preprocessor = TextPreprocessor()
    return _preprocessor


def preprocess_text(text: str, file_ext: str = "") -> str:
    """便捷函数：预处理文本"""
    return get_preprocessor().preprocess(text, file_ext)
