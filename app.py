import os
import json
import shutil
import threading
import queue
from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, send_file, send_from_directory, Response
from interview_graph_generator import InterviewGraphGenerator
from merge_graphs import GraphMerger
from logger_config import init_logger, log_info, log_debug, log_warning, log_error, log_stream, log_stream_line

# 加载环境变量
load_dotenv()

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'data'
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB max file size
app.config['SECRET_KEY'] = 'your-secret-key-here'

# 支持的文件格式
SUPPORTED_EXTENSIONS = {'.docx', '.doc', '.txt', '.md', '.json', '.ass', '.srt'}

def is_supported_file(filename):
    """检查文件是否为支持的格式"""
    ext = os.path.splitext(filename)[1].lower()
    return ext in SUPPORTED_EXTENSIONS

def get_output_path(input_path):
    """根据输入文件路径生成输出图片路径"""
    base = os.path.splitext(input_path)[0]
    return f"{base}_graph.png"

# 初始化日志记录器
stream_to_terminal = os.getenv("STREAM_TO_TERMINAL", "true").lower() == "true"
init_logger(stream_to_terminal=stream_to_terminal)

# 全局变量用于任务控制
current_tasks = {}
task_lock = threading.Lock()
cancelled_tasks = set()  # 已取消的任务ID集合

# SSE 消息队列：task_id -> queue
sse_queues = {}
sse_queue_lock = threading.Lock()

def get_task_status(task_id):
    """获取任务状态"""
    with task_lock:
        return current_tasks.get(task_id, {'status': 'not_found', 'progress': 0, 'message': ''})

def set_task_status(task_id, status, progress=0, message=''):
    """设置任务状态"""
    with task_lock:
        if task_id not in current_tasks:
            current_tasks[task_id] = {}
        current_tasks[task_id].update({
            'status': status,
            'progress': progress,
            'message': message
        })

def cancel_task(task_id):
    """取消任务"""
    with task_lock:
        if task_id in current_tasks:
            current_tasks[task_id]['status'] = 'cancelled'
            cancelled_tasks.add(task_id)
            log_info(f"[任务中断] 任务 {task_id} 已被用户取消")
            return True
    return False

def send_sse_message(task_id, message, message_type='log'):
    """发送 SSE 消息到前端"""
    with sse_queue_lock:
        if task_id in sse_queues:
            try:
                sse_queues[task_id].put({
                    'type': message_type,
                    'content': message
                }, timeout=0.1)
            except queue.Full:
                pass  # 队列满时忽略

def is_task_cancelled(task_id):
    """检查任务是否被取消"""
    with task_lock:
        return task_id in cancelled_tasks or current_tasks.get(task_id, {}).get('status') == 'cancelled'

@app.route('/')
def index():
    """主页面"""
    return render_template('index.html')

@app.route('/api/stream_logs/<task_id>')
def stream_logs(task_id):
    """SSE 端点：实时推送日志内容"""
    def generate():
        # 创建消息队列
        with sse_queue_lock:
            if task_id not in sse_queues:
                sse_queues[task_id] = queue.Queue(maxsize=1000)
            msg_queue = sse_queues[task_id]
        
        try:
            # 发送初始连接消息（不发送，因为前端只显示流式输出）
            # yield f"data: {json.dumps({'type': 'log', 'message': '[连接成功] 开始接收实时推理输出...'}, ensure_ascii=False)}\n\n"
            
            while True:
                try:
                    # 从队列获取消息（超时1秒）
                    msg = msg_queue.get(timeout=1)
                    # 确保消息格式正确
                    if isinstance(msg, dict):
                        formatted_msg = {
                            'type': msg.get('type', 'log'),
                            'message': msg.get('content', msg.get('message', ''))
                        }
                    else:
                        formatted_msg = {'type': 'log', 'message': str(msg)}
                    yield f"data: {json.dumps(formatted_msg, ensure_ascii=False)}\n\n"
                except queue.Empty:
                    # 发送心跳保持连接
                    yield f"data: {json.dumps({'type': 'heartbeat', 'message': ''}, ensure_ascii=False)}\n\n"
        except GeneratorExit:
            # 清理队列
            with sse_queue_lock:
                if task_id in sse_queues:
                    del sse_queues[task_id]
        except Exception as e:
            log_error(f"SSE连接错误: {str(e)}")
            yield f"data: {json.dumps({'type': 'error', 'message': f'连接错误: {str(e)}'}, ensure_ascii=False)}\n\n"
    
    return Response(generate(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no',
        'Connection': 'keep-alive'
    })

@app.route('/api/upload', methods=['POST'])
def upload_files():
    """批量上传文件（支持多种格式）"""
    if 'files' not in request.files:
        return jsonify({'success': False, 'message': '没有选择文件'}), 400
    
    files = request.files.getlist('files')
    if not files or files[0].filename == '':
        return jsonify({'success': False, 'message': '没有选择文件'}), 400
    
    uploaded_files = []
    skipped_files = []
    for file in files:
        if file and is_supported_file(file.filename):
            # 保持原始文件名，只处理路径安全问题
            filename = file.filename
            # 只移除路径分隔符，保持中文字符和其他字符
            # 获取文件名（去除路径）
            if '\\' in filename:
                filename = filename.split('\\')[-1]
            if '/' in filename:
                filename = filename.split('/')[-1]
            # 移除危险字符但保持中文
            filename = filename.replace('..', '_')
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            uploaded_files.append(filename)
        elif file and file.filename:
            skipped_files.append(file.filename)
    
    return jsonify({
        'success': True,
        'message': f'成功上传 {len(uploaded_files)} 个文件',
        'files': uploaded_files
    })

@app.route('/api/clear', methods=['POST'])
def clear_files():
    """一键清除data文件夹中的所有文件（除了merged_graph相关文件）"""
    data_folder = app.config['UPLOAD_FOLDER']
    if not os.path.exists(data_folder):
        return jsonify({'success': False}), 400
    
    for filename in os.listdir(data_folder):
        if filename.startswith('merged_graph'):
            continue
        filepath = os.path.join(data_folder, filename)
        try:
            if os.path.isfile(filepath):
                os.remove(filepath)
        except:
            pass
    
    return jsonify({'success': True})

@app.route('/api/list_files', methods=['GET'])
def list_files():
    """列出data文件夹中的支持格式文件"""
    data_folder = app.config['UPLOAD_FOLDER']
    if not os.path.exists(data_folder):
        return jsonify({'files': []})
    
    files = []
    for filename in os.listdir(data_folder):
        if is_supported_file(filename):
            filepath = os.path.join(data_folder, filename)
            file_size = os.path.getsize(filepath)
            files.append({
                'name': filename,
                'size': file_size,
                'has_graph': os.path.exists(get_output_path(filepath))
            })
    
    return jsonify({'files': files})

@app.route('/api/generate_subgraphs', methods=['POST'])
def generate_subgraphs():
    """批量生成子图"""
    data = request.json
    max_nodes = data.get('max_nodes', {})
    
    task_id = f"subgraph_{threading.get_ident()}_{os.urandom(4).hex()}"
    set_task_status(task_id, 'running', 0, '开始生成子图...')
    
    def generate_task():
        try:
            generator = InterviewGraphGenerator()
            generator.set_task_context(task_id, is_task_cancelled, send_sse_message)
            data_folder = app.config['UPLOAD_FOLDER']
            
            if not os.path.exists(data_folder):
                set_task_status(task_id, 'error', 0, 'data文件夹不存在')
                return
            
            # 查找所有支持格式的文件
            input_files = []
            for file in os.listdir(data_folder):
                if is_supported_file(file):
                    file_path = os.path.join(data_folder, file)
                    # 验证文件确实存在
                    if os.path.isfile(file_path):
                        input_files.append(file_path)
            
            total = len(input_files)
            if total == 0:
                # 列出data文件夹中的所有文件，帮助调试
                all_files = os.listdir(data_folder)
                file_list = ', '.join(all_files[:10])  # 只显示前10个
                supported = ', '.join(SUPPORTED_EXTENSIONS)
                set_task_status(task_id, 'error', 0, f'没有找到支持的文件（{supported}）。data文件夹中的文件: {file_list}...')
                return
            
            set_task_status(task_id, 'running', 0, f'找到 {total} 个文件，开始生成...')
            
            # 打印节点上限配置
            config_msg = f"\n{'='*60}\n[配置信息] 节点数量上限设置:\n  第2层: {max_nodes.get('level2', '未设置')} 个节点\n  第3层: {max_nodes.get('level3', '未设置')} 个节点\n  第4层: {max_nodes.get('level4', '未设置')} 个节点\n{'='*60}\n"
            log_info(config_msg)
            send_sse_message(task_id, config_msg, 'log')
            
            results = []
            for i, input_file in enumerate(input_files):
                # 检查是否被取消（在每个关键点都检查）
                if is_task_cancelled(task_id):
                    cancel_msg = f"[任务中断] 检测到取消信号，停止处理"
                    log_info(cancel_msg)
                    send_sse_message(task_id, cancel_msg, 'log')
                    set_task_status(task_id, 'cancelled', int((i/total)*100), '任务已中断')
                    return
                
                try:
                    filename = os.path.basename(input_file)
                    base_progress = int((i/total) * 100)
                    
                    file_msg = f"\n[处理文件 {i+1}/{total}] {filename}\n  进度: {base_progress}%"
                    log_info(file_msg)
                    send_sse_message(task_id, file_msg, 'log')
                    
                    # 步骤1: 提取文本
                    step1_msg = f"  [步骤1] 提取文本..."
                    log_info(step1_msg)
                    send_sse_message(task_id, step1_msg, 'log')
                    set_task_status(task_id, 'running', base_progress, f'[{i+1}/{total}] 提取文本: {filename}')
                    text = generator.extract_text(input_file)
                    step1_done_msg = f"  [步骤1完成] 文本长度: {len(text)} 字符"
                    log_info(step1_done_msg)
                    send_sse_message(task_id, step1_done_msg, 'log')
                    set_task_status(task_id, 'running', base_progress + 5, f'[{i+1}/{total}] 文本提取完成 ({len(text)} 字符)')
                    
                    # 检查取消
                    if is_task_cancelled(task_id):
                        cancel_msg = f"[任务中断] 在提取文本后检测到取消信号"
                        log_info(cancel_msg)
                        send_sse_message(task_id, cancel_msg, 'log')
                        set_task_status(task_id, 'cancelled', base_progress + 5, '任务已中断')
                        return
                    
                    # 步骤2: 调用AI提取实体
                    step2_msg = f"  [步骤2] 调用AI提取实体和关系...\n    节点上限配置: L2≤{max_nodes.get('level2', 'N/A')}, L3≤{max_nodes.get('level3', 'N/A')}, L4≤{max_nodes.get('level4', 'N/A')}"
                    log_info(step2_msg)
                    send_sse_message(task_id, step2_msg, 'log')
                    set_task_status(task_id, 'running', base_progress + 10, f'[{i+1}/{total}] 正在调用AI提取实体...')
                    graph_data = generator.extract_entities_with_glm(text, filename, max_nodes_config=max_nodes)
                    
                    if graph_data and 'nodes' in graph_data:
                        level1_count = sum(1 for n in graph_data['nodes'] if n.get('level') == 1)
                        level2_count = sum(1 for n in graph_data['nodes'] if n.get('level') == 2)
                        level3_count = sum(1 for n in graph_data['nodes'] if n.get('level') == 3)
                        level4_count = sum(1 for n in graph_data['nodes'] if n.get('level') == 4)
                        step2_done_msg = f"  [步骤2完成] 提取结果: L1={level1_count}, L2={level2_count}, L3={level3_count}, L4={level4_count}"
                        log_info(step2_done_msg)
                        send_sse_message(task_id, step2_done_msg, 'log')
                        set_task_status(task_id, 'running', base_progress + 50, 
                                      f'[{i+1}/{total}] AI提取完成: L1={level1_count}, L2={level2_count}, L3={level3_count}, L4={level4_count}')
                    else:
                        step2_done_msg = f"  [步骤2完成] AI提取完成（无详细统计）"
                        log_info(step2_done_msg)
                        send_sse_message(task_id, step2_done_msg, 'log')
                        set_task_status(task_id, 'running', base_progress + 50, f'[{i+1}/{total}] AI提取完成')
                    
                    # 检查取消
                    if is_task_cancelled(task_id):
                        cancel_msg = f"[任务中断] 在AI提取后检测到取消信号"
                        log_info(cancel_msg)
                        send_sse_message(task_id, cancel_msg, 'log')
                        set_task_status(task_id, 'cancelled', base_progress + 50, '任务已中断')
                        return
                    
                    # 步骤3: 生成图片
                    step3_msg = f"  [步骤3] 生成图片..."
                    log_info(step3_msg)
                    send_sse_message(task_id, step3_msg, 'log')
                    set_task_status(task_id, 'running', base_progress + 60, f'[{i+1}/{total}] 正在生成图片...')
                    output_path = get_output_path(input_file)
                    generator.draw_graph(graph_data['nodes'], graph_data['edges'], output_path)
                    step3_done_msg = f"  [步骤3完成] 图片已保存: {output_path}"
                    log_info(step3_done_msg)
                    send_sse_message(task_id, step3_done_msg, 'log')
                    set_task_status(task_id, 'running', base_progress + 90, f'[{i+1}/{total}] 图片生成完成')
                    
                    # 步骤4: 验证
                    if os.path.exists(output_path):
                        file_size = os.path.getsize(output_path) / 1024  # KB
                        step4_done_msg = f"  [步骤4完成] 文件验证成功，大小: {file_size:.2f} KB"
                        log_info(step4_done_msg)
                        send_sse_message(task_id, step4_done_msg, 'log')
                        results.append({
                            'filename': filename,
                            'success': True,
                            'graph_path': output_path.replace('\\', '/')
                        })
                        final_progress = int(((i+1)/total) * 100)
                        set_task_status(task_id, 'running', final_progress, f'✓ 完成 ({i+1}/{total}): {filename}')
                    else:
                        step4_fail_msg = f"  [步骤4失败] 文件未创建"
                        log_error(step4_fail_msg)
                        send_sse_message(task_id, step4_fail_msg, 'error')
                        results.append({
                            'filename': filename,
                            'success': False,
                            'error': '生成失败：文件未创建'
                        })
                except InterruptedError as e:
                    error_msg = f"  [任务中断] {str(e)}"
                    log_warning(error_msg)
                    send_sse_message(task_id, error_msg, 'error')
                    set_task_status(task_id, 'cancelled', base_progress, '任务已中断')
                    return
                except Exception as e:
                    import traceback
                    error_msg = str(e)
                    log_error(f"  [错误] 处理失败: {error_msg}")
                    log_error(traceback.format_exc())
                    send_sse_message(task_id, f"  [错误] 处理失败: {error_msg}", 'error')
                    results.append({
                        'filename': os.path.basename(input_file),
                        'success': False,
                        'error': error_msg
                    })
            
            set_task_status(task_id, 'completed', 100, f'完成！成功处理 {sum(1 for r in results if r["success"])} 个文件')
            current_tasks[task_id]['results'] = results
            
        except InterruptedError as e:
            error_msg = f"[任务中断] {str(e)}"
            log_warning(error_msg)
            send_sse_message(task_id, error_msg, 'error')
            set_task_status(task_id, 'cancelled', 0, '任务已中断')
        except Exception as e:
            error_msg = f'生成失败: {str(e)}'
            log_error(error_msg)
            send_sse_message(task_id, error_msg, 'error')
            set_task_status(task_id, 'error', 0, error_msg)
    
    thread = threading.Thread(target=generate_task)
    thread.daemon = True
    thread.start()
    
    return jsonify({'success': True, 'task_id': task_id})

@app.route('/api/regenerate_subgraph', methods=['POST'])
def regenerate_subgraph():
    """单独重新生成某个子图"""
    data = request.json
    graph_name = data.get('graph_name')  # 子图名称（不包含_graph.png）
    max_nodes = data.get('max_nodes', {})
    
    if not graph_name:
        return jsonify({'success': False, 'message': '未指定子图名称'}), 400
    
    task_id = f"regenerate_{threading.get_ident()}_{os.urandom(4).hex()}"
    set_task_status(task_id, 'running', 0, f'开始重新生成: {graph_name}...')
    
    def generate_task():
        try:
            generator = InterviewGraphGenerator()
            generator.set_task_context(task_id, is_task_cancelled, send_sse_message)
            data_folder = app.config['UPLOAD_FOLDER']
            
            # 查找对应的源文件（支持多种格式）
            input_path = None
            for ext in SUPPORTED_EXTENSIONS:
                candidate = os.path.join(data_folder, f"{graph_name}{ext}")
                if os.path.exists(candidate):
                    input_path = candidate
                    break
            
            if not input_path:
                supported = ', '.join(SUPPORTED_EXTENSIONS)
                set_task_status(task_id, 'error', 0, f'找不到对应的源文件: {graph_name}.*（支持: {supported}）')
                return
            
            config_msg = f"\n{'='*60}\n[单独重新生成] {graph_name}\n[配置信息] 节点数量上限设置:\n  第2层: {max_nodes.get('level2', '未设置')} 个节点\n  第3层: {max_nodes.get('level3', '未设置')} 个节点\n  第4层: {max_nodes.get('level4', '未设置')} 个节点\n{'='*60}\n"
            log_info(config_msg)
            send_sse_message(task_id, config_msg, 'log')
            
            # 删除旧的图文件
            old_png = os.path.join(data_folder, f"{graph_name}_graph.png")
            old_json = os.path.join(data_folder, f"{graph_name}_graph.json")
            if os.path.exists(old_png):
                try:
                    os.remove(old_png)
                    log_info(f"  已删除旧PNG文件")
                    send_sse_message(task_id, "  已删除旧PNG文件", 'log')
                except:
                    pass
            if os.path.exists(old_json):
                try:
                    os.remove(old_json)
                    log_info(f"  已删除旧JSON文件")
                    send_sse_message(task_id, "  已删除旧JSON文件", 'log')
                except:
                    pass
            
            filename = os.path.basename(input_path)
            
            # 步骤1: 提取文本
            step1_msg = f"  [步骤1] 提取文本..."
            log_info(step1_msg)
            send_sse_message(task_id, step1_msg, 'log')
            set_task_status(task_id, 'running', 10, '提取文本...')
            text = generator.extract_text(input_path)
            step1_done_msg = f"  [步骤1完成] 文本长度: {len(text)} 字符"
            log_info(step1_done_msg)
            send_sse_message(task_id, step1_done_msg, 'log')
            set_task_status(task_id, 'running', 20, f'文本提取完成 ({len(text)} 字符)')
            
            # 检查取消
            if is_task_cancelled(task_id):
                cancel_msg = f"[任务中断] 在提取文本后检测到取消信号"
                log_info(cancel_msg)
                send_sse_message(task_id, cancel_msg, 'error')
                set_task_status(task_id, 'cancelled', 20, '任务已中断')
                return
            
            # 步骤2: 调用AI提取实体
            step2_msg = f"  [步骤2] 调用AI提取实体和关系...\n    节点上限配置: L2≤{max_nodes.get('level2', 'N/A')}, L3≤{max_nodes.get('level3', 'N/A')}, L4≤{max_nodes.get('level4', 'N/A')}"
            log_info(step2_msg)
            send_sse_message(task_id, step2_msg, 'log')
            set_task_status(task_id, 'running', 30, '正在调用AI提取实体...')
            graph_data = generator.extract_entities_with_glm(text, filename, max_nodes_config=max_nodes)
            
            if graph_data and 'nodes' in graph_data:
                level1_count = sum(1 for n in graph_data['nodes'] if n.get('level') == 1)
                level2_count = sum(1 for n in graph_data['nodes'] if n.get('level') == 2)
                level3_count = sum(1 for n in graph_data['nodes'] if n.get('level') == 3)
                level4_count = sum(1 for n in graph_data['nodes'] if n.get('level') == 4)
                step2_done_msg = f"  [步骤2完成] 提取结果: L1={level1_count}, L2={level2_count}, L3={level3_count}, L4={level4_count}"
                log_info(step2_done_msg)
                send_sse_message(task_id, step2_done_msg, 'log')
                set_task_status(task_id, 'running', 70, 
                              f'AI提取完成: L1={level1_count}, L2={level2_count}, L3={level3_count}, L4={level4_count}')
            else:
                step2_done_msg = f"  [步骤2完成] AI提取完成（无详细统计）"
                log_info(step2_done_msg)
                send_sse_message(task_id, step2_done_msg, 'log')
                set_task_status(task_id, 'running', 70, 'AI提取完成')
            
            # 检查取消
            if is_task_cancelled(task_id):
                cancel_msg = f"[任务中断] 在AI提取后检测到取消信号"
                log_info(cancel_msg)
                send_sse_message(task_id, cancel_msg, 'error')
                set_task_status(task_id, 'cancelled', 70, '任务已中断')
                return
            
            # 步骤3: 生成图片
            step3_msg = f"  [步骤3] 生成图片..."
            log_info(step3_msg)
            send_sse_message(task_id, step3_msg, 'log')
            set_task_status(task_id, 'running', 80, '正在生成图片...')
            output_path = get_output_path(input_path)
            generator.draw_graph(graph_data['nodes'], graph_data['edges'], output_path)
            step3_done_msg = f"  [步骤3完成] 图片已保存: {output_path}"
            log_info(step3_done_msg)
            send_sse_message(task_id, step3_done_msg, 'log')
            set_task_status(task_id, 'running', 95, '图片生成完成')
            
            # 步骤4: 验证
            if os.path.exists(output_path):
                file_size = os.path.getsize(output_path) / 1024  # KB
                step4_done_msg = f"  [步骤4完成] 文件验证成功，大小: {file_size:.2f} KB"
                log_info(step4_done_msg)
                send_sse_message(task_id, step4_done_msg, 'log')
                set_task_status(task_id, 'completed', 100, f'重新生成完成！')
                current_tasks[task_id]['result'] = {
                    'graph_path': output_path.replace('\\', '/'),
                    'graph_name': graph_name
                }
            else:
                step4_fail_msg = f"  [步骤4失败] 文件未创建"
                log_error(step4_fail_msg)
                send_sse_message(task_id, step4_fail_msg, 'error')
                set_task_status(task_id, 'error', 0, '生成失败：文件未创建')
                
        except InterruptedError as e:
            error_msg = f"[任务中断] {str(e)}"
            log_warning(error_msg)
            send_sse_message(task_id, error_msg, 'error')
            set_task_status(task_id, 'cancelled', 0, '任务已中断')
        except Exception as e:
            import traceback
            error_msg = str(e)
            log_error(f"  [错误] 重新生成失败: {error_msg}")
            log_error(traceback.format_exc())
            send_sse_message(task_id, f"  [错误] 重新生成失败: {error_msg}", 'error')
            set_task_status(task_id, 'error', 0, f'重新生成失败: {error_msg}')
    
    thread = threading.Thread(target=generate_task)
    thread.daemon = True
    thread.start()
    
    return jsonify({'success': True, 'task_id': task_id})

@app.route('/api/generate_merged_graph', methods=['POST'])
def generate_merged_graph():
    """生成总编码图"""
    try:
        data = request.json
        if not data:
            return jsonify({'success': False, 'message': '请求数据为空'}), 400
        
        max_nodes = data.get('max_nodes', {})
        
        task_id = f"merged_{threading.get_ident()}_{os.urandom(4).hex()}"
        set_task_status(task_id, 'running', 0, '开始生成总编码图...')
        log_info(f"[任务开始] 生成总编码图，任务ID: {task_id}")
        
        def generate_task():
            try:
                # 构建节点数量限制配置
                max_nodes_config = {
                    2: max_nodes.get('level2', 8),
                    3: max_nodes.get('level3', 20),
                    4: max_nodes.get('level4', 30)
                }
                
                # 创建合并器，传入节点数量限制
                merger = GraphMerger(
                    data_folder=app.config['UPLOAD_FOLDER'],
                    max_nodes_config=max_nodes_config
                )
                merger.set_task_context(task_id, is_task_cancelled, send_sse_message)
                
                set_task_status(task_id, 'running', 5, '正在加载子图JSON文件...')
                # 手动加载子图以获取进度
                num_graphs = merger.load_all_graphs()
                if num_graphs == 0:
                    set_task_status(task_id, 'error', 0, '没有找到子图JSON文件')
                    return
                
                set_task_status(task_id, 'running', 10, f'已加载 {num_graphs} 个子图，开始逐层合并...')
                
                # 初始化数据结构
                all_merged_nodes = []
                all_edges = set()
                max_level = 4
                
                # 打印节点上限配置
                config_msg = f"\n{'='*60}\n[总图生成] 节点数量上限设置:\n  第2层: {max_nodes_config.get(2, 'N/A')} 个节点\n  第3层: {max_nodes_config.get(3, 'N/A')} 个节点\n  第4层: {max_nodes_config.get(4, 'N/A')} 个节点\n{'='*60}\n"
                log_info(config_msg)
                send_sse_message(task_id, config_msg, 'log')
                
                # 逐层处理：合并一层后立即找父节点连线
                for level in range(1, max_level + 1):
                    # 检查是否被取消
                    if is_task_cancelled(task_id):
                        cancel_msg = f"[任务中断] 在处理第{level}层时检测到取消信号"
                        log_info(cancel_msg)
                        send_sse_message(task_id, cancel_msg, 'error')
                        set_task_status(task_id, 'cancelled', int((level-1)/max_level*80 + 10), '任务已中断')
                        return
                    
                    base_progress = 10 + int((level - 1) / max_level * 70)
                    
                    level_msg = f"\n[处理第{level}层] 进度: {base_progress}%"
                    log_info(level_msg)
                    send_sse_message(task_id, level_msg, 'log')
                    
                    # 提取当前层的所有节点
                    step1_msg = f"  [步骤1] 提取第{level}层节点..."
                    log_info(step1_msg)
                    send_sse_message(task_id, step1_msg, 'log')
                    set_task_status(task_id, 'running', base_progress, f'正在提取第{level}层节点...')
                    nodes = merger.extract_nodes_by_level(level)
                    if not nodes:
                        skip_msg = f"  [跳过] 第{level}层没有节点"
                        log_info(skip_msg)
                        send_sse_message(task_id, skip_msg, 'log')
                        set_task_status(task_id, 'running', base_progress + 5, f'第{level}层没有节点，跳过')
                        continue
                    
                    step1_done_msg = f"  [步骤1完成] 提取到 {len(nodes)} 个原始节点"
                    log_info(step1_done_msg)
                    send_sse_message(task_id, step1_done_msg, 'log')
                    set_task_status(task_id, 'running', base_progress + 5, f'提取到 {len(nodes)} 个第{level}层节点，正在调用AI合并...')
                    
                    # 检查取消
                    if is_task_cancelled(task_id):
                        cancel_msg = f"[任务中断] 在提取节点后检测到取消信号"
                        log_info(cancel_msg)
                        send_sse_message(task_id, cancel_msg, 'error')
                        set_task_status(task_id, 'cancelled', base_progress + 5, '任务已中断')
                        return
                    
                    # 合并当前层节点
                    step2_msg = f"  [步骤2] 调用AI合并第{level}层节点（上限: {max_nodes_config.get(level, 'N/A')}）..."
                    log_info(step2_msg)
                    send_sse_message(task_id, step2_msg, 'log')
                    merged_nodes = merger.merge_level_with_ai(level, nodes)
                    all_merged_nodes.extend(merged_nodes)
                    
                    # 更新缓存
                    for node in merged_nodes:
                        merger.merged_node_dict[node['id']] = node
                        merger.merged_nodes_by_level[node['level']].append(node)
                    
                    step2_done_msg = f"  [步骤2完成] 合并后剩余 {len(merged_nodes)} 个节点"
                    log_info(step2_done_msg)
                    send_sse_message(task_id, step2_done_msg, 'log')
                    set_task_status(task_id, 'running', base_progress + 15, f'第{level}层合并完成，剩余 {len(merged_nodes)} 个节点')
                    
                    # 如果不是第1层，立即找父节点并连线
                    if level > 1:
                        step3_msg = f"  [步骤3] 为第{level}层节点找父节点..."
                        log_info(step3_msg)
                        send_sse_message(task_id, step3_msg, 'log')
                        set_task_status(task_id, 'running', base_progress + 20, f'正在为第{level}层节点找父节点...')
                        parent_nodes = merger.merged_nodes_by_level.get(level - 1, [])
                        
                        if not parent_nodes:
                            warn_msg = f"  [警告] 第{level-1}层没有父节点，跳过连线"
                            log_warning(warn_msg)
                            send_sse_message(task_id, warn_msg, 'log')
                            set_task_status(task_id, 'running', base_progress + 25, f'警告：第{level-1}层没有父节点，跳过连线')
                        else:
                            # 第2层节点直接连接到root，不需要AI
                            if level == 2:
                                root_node = merger.merged_nodes_by_level.get(1, [])
                                if root_node:
                                    root_id = root_node[0]['id']
                                    edges_added = 0
                                    for child_node in merged_nodes:
                                        all_edges.add((root_id, child_node['id']))
                                        edges_added += 1
                                    step3_done_msg = f"  [步骤3完成] 第2层连线完成，添加了 {edges_added} 条边"
                                    log_info(step3_done_msg)
                                    send_sse_message(task_id, step3_done_msg, 'log')
                                    set_task_status(task_id, 'running', base_progress + 25, f'第2层连线完成，添加了 {edges_added} 条边')
                                else:
                                    warn_msg = f"  [警告] 找不到root节点"
                                    log_warning(warn_msg)
                                    send_sse_message(task_id, warn_msg, 'log')
                                    set_task_status(task_id, 'running', base_progress + 25, '警告：找不到root节点')
                            else:
                                # 第3层及以下：使用AI确定每个子节点的父节点
                                step3_ai_msg = f"  [步骤3] 调用AI确定第{level}层节点的父节点..."
                                log_info(step3_ai_msg)
                                send_sse_message(task_id, step3_ai_msg, 'log')
                                set_task_status(task_id, 'running', base_progress + 20, f'正在调用AI确定第{level}层节点的父节点...')
                                
                                # 检查取消
                                if is_task_cancelled(task_id):
                                    cancel_msg = f"[任务中断] 在确定父节点前检测到取消信号"
                                    log_info(cancel_msg)
                                    send_sse_message(task_id, cancel_msg, 'error')
                                    set_task_status(task_id, 'cancelled', base_progress + 20, '任务已中断')
                                    return
                                
                                child_parent_mapping = merger._determine_parents_with_ai(merged_nodes, parent_nodes, level)
                                
                                # 添加边
                                edges_added = 0
                                for child_id, parent_id in child_parent_mapping.items():
                                    if parent_id in merger.merged_node_dict and child_id in merger.merged_node_dict:
                                        all_edges.add((parent_id, child_id))
                                        edges_added += 1
                                
                                step3_done_msg = f"  [步骤3完成] 第{level}层连线完成，添加了 {edges_added} 条边"
                                log_info(step3_done_msg)
                                send_sse_message(task_id, step3_done_msg, 'log')
                                set_task_status(task_id, 'running', base_progress + 25, f'第{level}层连线完成，添加了 {edges_added} 条边')
                
                # 转换为边列表格式
                set_task_status(task_id, 'running', 85, '正在构建图结构...')
                merged_edges = []
                for source_id, target_id in all_edges:
                    merged_edges.append({
                        "source": source_id,
                        "target": target_id,
                        "type": "层级",
                        "label": "次级"
                    })
                
                # 清洗图结构
                set_task_status(task_id, 'running', 87, '正在清洗图结构...')
                cleaned_graph = merger.clean_graph_structure({
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
                
                set_task_status(task_id, 'running', 90, '正在生成图片...')
                output_path, json_path = merger.save_merged_graph(merged_graph)
                
                # 这里 output_path/json_path 是磁盘路径，例如 data/merged_graph.png
                # 前端只能通过 /api/files/ 访问，所以统一转换为文件服务URL
                from urllib.parse import quote
                png_name = os.path.basename(output_path)
                json_name = os.path.basename(json_path)
                graph_url = f"/api/files/{quote(png_name, safe='')}"
                json_url = f"/api/files/{quote(json_name, safe='')}"
                
                complete_msg = f'生成完成！共 {len(cleaned_graph["nodes"])} 个节点，{len(cleaned_graph["edges"])} 条边'
                set_task_status(task_id, 'completed', 100, complete_msg)
                log_info(f"[任务完成] {complete_msg}")
                send_sse_message(task_id, f"[任务完成] {complete_msg}", 'log')
                current_tasks[task_id]['result'] = {
                    'graph_path': graph_url,
                    'json_path': json_url
                }
                
            except InterruptedError as e:
                error_msg = f"[任务中断] {str(e)}"
                log_warning(error_msg)
                send_sse_message(task_id, error_msg, 'error')
                set_task_status(task_id, 'cancelled', 0, '任务已中断')
            except Exception as e:
                import traceback
                error_msg = str(e)
                log_error(f"生成总图失败: {error_msg}")
                log_error(traceback.format_exc())
                send_sse_message(task_id, f"生成总图失败: {error_msg}", 'error')
                set_task_status(task_id, 'error', 0, f'生成失败: {error_msg}')
        
        thread = threading.Thread(target=generate_task)
        thread.daemon = True
        thread.start()
        
        return jsonify({'success': True, 'task_id': task_id})
    except Exception as e:
        import traceback
        log_error(f"生成总编码图请求处理失败: {str(e)}")
        log_error(traceback.format_exc())
        return jsonify({'success': False, 'message': f'请求处理失败: {str(e)}'}), 500

@app.route('/api/task_status/<task_id>', methods=['GET'])
def task_status(task_id):
    """获取任务状态"""
    status = get_task_status(task_id)
    return jsonify(status)

@app.route('/api/cancel_task/<task_id>', methods=['POST'])
def cancel_task_route(task_id):
    """取消任务"""
    success = cancel_task(task_id)
    return jsonify({'success': success})

@app.route('/api/list_subgraphs', methods=['GET'])
def list_subgraphs():
    """列出所有子图"""
    data_folder = app.config['UPLOAD_FOLDER']
    if not os.path.exists(data_folder):
        return jsonify({'graphs': []})
    
    graphs = []
    for filename in os.listdir(data_folder):
        # 过滤掉merged_graph.png，只显示子图
        if filename.endswith('_graph.png') and filename != 'merged_graph.png':
            base_name = filename.replace('_graph.png', '')
            filepath = os.path.join(data_folder, filename)
            
            # 验证文件确实存在且可读
            if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
                continue
            
            file_size = os.path.getsize(filepath)
            # URL编码文件名以支持中文
            from urllib.parse import quote
            encoded_filename = quote(filename, safe='')
            graphs.append({
                'name': base_name,
                'filename': filename,
                'size': file_size,
                'path': f'/api/files/{encoded_filename}'
            })
    
    return jsonify({'graphs': graphs})

@app.route('/api/files/<path:filename>')
def serve_file(filename):
    """提供文件下载和预览"""
    try:
        from urllib.parse import unquote
        # 解码URL编码的文件名
        filename = unquote(filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        
        # 验证文件是否存在
        if not os.path.exists(filepath):
            return jsonify({'error': f'文件不存在: {filename}'}), 404
        
        return send_from_directory(app.config['UPLOAD_FOLDER'], filename)
    except Exception as e:
        import traceback
        return jsonify({'error': f'文件服务错误: {str(e)}', 'trace': traceback.format_exc()}), 500

@app.route('/api/check_merged_graph', methods=['GET'])
def check_merged_graph():
    """检查是否存在总编码图"""
    merged_png = os.path.join(app.config['UPLOAD_FOLDER'], 'merged_graph.png')
    if os.path.exists(merged_png):
        return jsonify({
            'exists': True,
            'path': '/api/files/merged_graph.png'
        })
    return jsonify({'exists': False})

@app.route('/api/switch_provider', methods=['POST'])
def switch_provider():
    """切换API provider"""
    data = request.json
    provider = data.get('provider')
    
    if not provider:
        return jsonify({'success': False, 'message': '未指定 provider'}), 400
    
    try:
        # 验诈1provider是否可用
        generator = InterviewGraphGenerator()
        available_providers = generator.get_available_providers()
        
        if provider not in available_providers:
            return jsonify({
                'success': False, 
                'message': f'不支持的API provider: {provider}，可用: {", ".join(available_providers)}'
            }), 400
        
        # 切换provider
        generator.switch_api_provider(provider)
        
        # 更新 .env 文件中的默认provider
        env_path = os.path.join(os.path.dirname(__file__), ".env")
        if os.path.exists(env_path):
            with open(env_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            with open(env_path, 'w', encoding='utf-8') as f:
                for line in lines:
                    if line.startswith('DEFAULT_PROVIDER='):
                        f.write(f'DEFAULT_PROVIDER={provider}\n')
                    else:
                        f.write(line)
        
        return jsonify({
            'success': True, 
            'message': f'已切换到 {provider} API',
            'current_provider': provider
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/get_current_provider', methods=['GET'])
def get_current_provider():
    """获取当前的API provider"""
    try:
        current_provider = os.getenv('DEFAULT_PROVIDER')
        
        # 获取所有可用的provider
        generator = InterviewGraphGenerator()
        available_providers = generator.get_available_providers()
        
        return jsonify({
            'success': True,
            'current_provider': current_provider,
            'available_providers': available_providers
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# ==================== 提示词管理 API ====================

@app.route('/api/prompts', methods=['GET'])
def get_prompts():
    """获取所有提示词模板"""
    try:
        generator = InterviewGraphGenerator()
        templates = generator.get_prompt_templates()
        current = generator.get_current_prompt_template()
        
        return jsonify({
            'success': True,
            'templates': templates,
            'current': current
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/prompts/current', methods=['GET'])
def get_current_prompt():
    """获取当前使用的提示词模板"""
    try:
        generator = InterviewGraphGenerator()
        current = generator.get_current_prompt_template()
        templates = generator.get_prompt_templates()
        template_data = templates.get(current, {})
        
        return jsonify({
            'success': True,
            'current': current,
            'template': template_data
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/prompts/switch', methods=['POST'])
def switch_prompt():
    """切换提示词模板"""
    try:
        data = request.json
        template_name = data.get('template')
        
        if not template_name:
            return jsonify({'success': False, 'message': '未指定模板名称'}), 400
        
        generator = InterviewGraphGenerator()
        generator.switch_prompt_template(template_name)
        
        return jsonify({
            'success': True,
            'message': f'已切换到提示词模板: {template_name}',
            'current': template_name
        })
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/prompts/save', methods=['POST'])
def save_prompt():
    """保存或更新提示词模板"""
    try:
        data = request.json
        template_name = data.get('name')
        template_data = {
            'name': data.get('display_name', template_name),
            'description': data.get('description', ''),
            'extract_entities': data.get('extract_entities', ''),
            'extract_topic': data.get('extract_topic', '')
        }
        
        if not template_name:
            return jsonify({'success': False, 'message': '未指定模板名称'}), 400
        
        if not template_data['extract_entities']:
            return jsonify({'success': False, 'message': '实体提取提示词不能为空'}), 400
        
        generator = InterviewGraphGenerator()
        generator.update_prompt_template(template_name, template_data)
        
        return jsonify({
            'success': True,
            'message': f'已保存提示词模板: {template_name}'
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/prompts/delete', methods=['POST'])
def delete_prompt():
    """删除提示词模板"""
    try:
        data = request.json
        template_name = data.get('template')
        
        if not template_name:
            return jsonify({'success': False, 'message': '未指定模板名称'}), 400
        
        generator = InterviewGraphGenerator()
        result = generator.delete_prompt_template(template_name)
        
        if result:
            return jsonify({
                'success': True,
                'message': f'已删除提示词模板: {template_name}'
            })
        else:
            return jsonify({'success': False, 'message': '模板不存在'}), 404
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

if __name__ == '__main__':
    # 确保data文件夹存在
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])
    
    # 关闭Flask的HTTP请求日志
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    
    # # 输出调试网址
    # print("\n" + "="*60)
    # print("Flask 应用已启动（调试模式）")
    # print("="*60)
    # print(f"本地访问地址: http://127.0.0.1:5001")
    # print(f"网络访问地址: http://0.0.0.0:5001")
    # print("="*60 + "\n")
    
    app.run(debug=True, host='0.0.0.0', port=5001)

