import asyncio
import os
import yaml
import glob
import re
import aiofiles
from typing import List, Dict, Optional, AsyncGenerator
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from backend.ssh_manager import SSHConnectionPool, RemoteFileReader

CONFIG_PATH = "config/settings.yaml"

# ANSI 颜色代码的正则表达式（支持带和不带 ESC 前缀的格式）
ANSI_ESCAPE_PATTERN = re.compile(r'(\x1B\[[0-?]*[ -/]*[@-~]|\[[0-9;]+m)')

def strip_ansi_codes(text: str) -> str:
    """移除 ANSI 颜色代码"""
    return ANSI_ESCAPE_PATTERN.sub('', text)


class FileWatcher(FileSystemEventHandler):
    """文件监控处理器（线程安全）"""
    
    def __init__(self, file_path: str, event_flag: asyncio.Event, loop):
        self.file_path = file_path
        self.event_flag = event_flag
        self.loop = loop
        
    def on_modified(self, event):
        if not event.is_directory and event.src_path == self.file_path:
            # 在主事件循环中设置事件（线程安全）
            self.loop.call_soon_threadsafe(self.event_flag.set)
    
    def on_created(self, event):
        if not event.is_directory and event.src_path == self.file_path:
            # 在主事件循环中设置事件（线程安全）
            self.loop.call_soon_threadsafe(self.event_flag.set)


class LogManager:
    def __init__(self):
        self.config = self._load_config()
        self.files_map = self._build_files_map()
        
        # 初始化 SSH 连接池和远程文件读取器
        self.ssh_pool = SSHConnectionPool(max_connections=10, timeout=300)
        self.remote_reader = RemoteFileReader(self.ssh_pool)

    def _load_config(self) -> dict:
        if not os.path.exists(CONFIG_PATH):
            return {"log_files": [], "log_directories": []}
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)

    def _scan_directory(self, dir_config: dict) -> List[Dict]:
        """扫描目录，返回所有日志文件"""
        scan_dir = dir_config.get("scan_dir")
        pattern = dir_config.get("pattern", "*.log")
        recursive = dir_config.get("recursive", True)
        encoding = dir_config.get("encoding", "utf-8")
        
        if not scan_dir or not os.path.exists(scan_dir):
            return []
        
        files = []
        search_pattern = os.path.join(scan_dir, "**", pattern) if recursive else os.path.join(scan_dir, pattern)
        
        for file_path in glob.glob(search_pattern, recursive=recursive):
            if os.path.isfile(file_path):
                # 生成相对于扫描目录的显示名称
                rel_path = os.path.relpath(file_path, scan_dir)
                files.append({
                    "name": rel_path,
                    "path": file_path,
                    "encoding": encoding,
                })
        
        return files

    def _build_tree_structure(self, files: List[Dict], base_name: str) -> List[Dict]:
        """将扁平的文件列表转换为树状结构"""
        tree = {}
        
        for file_info in files:
            rel_path = file_info["name"]
            path_parts = Path(rel_path).parts
            
            current_level = tree
            for i, part in enumerate(path_parts):
                if part not in current_level:
                    is_file = (i == len(path_parts) - 1)
                    if is_file:
                        # 叶子节点（文件）
                        unique_name = f"{base_name}/{rel_path}"
                        current_level[part] = {
                            "name": unique_name,
                            "label": part,  # 只显示文件名
                            "path": file_info["path"],
                            "encoding": file_info["encoding"],
                            "type": "file",
                            "is_leaf": True
                        }
                    else:
                        # 目录节点
                        current_level[part] = {
                            "name": part,
                            "label": part,  # 只显示目录名
                            "type": "directory",
                            "children": {}
                        }
                
                if not current_level[part].get("is_leaf"):
                    current_level = current_level[part]["children"]
        
        # 将字典转换为列表格式
        def dict_to_list(node_dict):
            result = []
            for key, value in sorted(node_dict.items()):
                if value.get("type") == "directory":
                    result.append({
                        "name": value["name"],
                        "label": value["label"],
                        "type": "directory",
                        "children": dict_to_list(value["children"])
                    })
                else:
                    result.append({
                        "name": value["name"],
                        "label": value["label"],
                        "path": value["path"],
                        "exists": os.path.exists(value["path"]),
                        "size": os.path.getsize(value["path"]) if os.path.exists(value["path"]) else 0,
                        "type": "file"
                    })
            return result
        
        return dict_to_list(tree)

    def _build_files_map(self) -> Dict[str, dict]:
        """构建文件名到配置的映射"""
        mapping = {}
        
        # 添加手动配置的文件
        for item in self.config.get("log_files", []):
            mapping[item["name"]] = {
                **item,
                "source": "local"
            }
        
        # 添加扫描目录中的文件
        for dir_config in self.config.get("log_directories", []):
            base_name = dir_config.get("name", "Scanned")
            scanned_files = self._scan_directory(dir_config)
            for file_info in scanned_files:
                # 使用完整路径作为唯一标识
                unique_name = f"{base_name}/{file_info['name']}"
                mapping[unique_name] = {
                    "path": file_info["path"],
                    "encoding": file_info["encoding"],
                    "source": "local"
                }
        
        # 添加远程服务器的文件
        for server_config in self.config.get("remote_servers", []):
            server_name = server_config.get("name", "Remote")
            for log_config in server_config.get("logs", []):
                unique_name = f"{server_name}/{log_config['name']}"
                mapping[unique_name] = {
                    **log_config,
                    "source": "remote",
                    "server_config": server_config
                }
        
        return mapping

    async def _build_remote_tree(self, server_config: Dict) -> List[Dict]:
        """构建远程服务器的文件树"""
        server_name = server_config.get("name", "Remote")
        tree_nodes = []
        
        for log_config in server_config.get("logs", []):
            log_name = log_config.get("name")
            log_type = log_config.get("type", "file")
            log_path = log_config.get("path")
            
            if log_type == "file":
                # 单个文件
                unique_name = f"{server_name}/{log_name}"
                tree_nodes.append({
                    "name": unique_name,
                    "label": log_name,
                    "path": log_path,
                    "type": "file",
                    "source": "remote",
                    "exists": True  # 远程文件假设存在，实际访问时再验证
                })
            elif log_type == "directory":
                # 扫描远程目录
                pattern = log_config.get("pattern", "*.log")
                recursive = log_config.get("recursive", False)
                
                remote_files = await self.remote_reader.list_files(
                    server_config, log_path, pattern, recursive
                )
                
                if remote_files:
                    # 构建子树
                    dir_tree = self._build_remote_dir_tree(
                        remote_files, log_path, server_name, log_name
                    )
                    
                    # 创建目录节点
                    tree_nodes.append({
                        "name": f"{server_name}/{log_name}",
                        "label": log_name,
                        "type": "directory",
                        "children": dir_tree
                    })
        
        return tree_nodes
    
    def _build_remote_dir_tree(self, files: List[Dict], base_path: str, 
                               server_name: str, dir_name: str) -> List[Dict]:
        """构建远程目录的树状结构"""
        tree = {}
        
        for file_info in files:
            file_path = file_info["path"]
            # 计算相对路径
            rel_path = os.path.relpath(file_path, base_path)
            path_parts = Path(rel_path).parts
            
            current_level = tree
            for i, part in enumerate(path_parts):
                if part not in current_level:
                    is_file = (i == len(path_parts) - 1)
                    if is_file:
                        # 文件节点
                        unique_name = f"{server_name}/{dir_name}/{rel_path}"
                        current_level[part] = {
                            "name": unique_name,
                            "label": part,
                            "path": file_path,
                            "type": "file",
                            "source": "remote",
                            "is_leaf": True
                        }
                    else:
                        # 目录节点
                        current_level[part] = {
                            "name": part,
                            "label": part,
                            "type": "directory",
                            "children": {}
                        }
                
                if not current_level[part].get("is_leaf"):
                    current_level = current_level[part]["children"]
        
        # 转换为列表
        def dict_to_list(node_dict):
            result = []
            for key, value in sorted(node_dict.items()):
                if value.get("type") == "directory":
                    result.append({
                        "name": value["name"],
                        "label": value["label"],
                        "type": "directory",
                        "children": dict_to_list(value["children"])
                    })
                else:
                    result.append({
                        "name": value["name"],
                        "label": value["label"],
                        "path": value["path"],
                        "type": "file",
                        "source": "remote",
                        "exists": True
                    })
            return result
        
        return dict_to_list(tree)

    def get_file_list(self) -> List[Dict]:
        """获取可用的文件列表，检查文件是否存在（同步版本，用于初始化）"""
        result = []
        
        # 手动配置的本地文件
        for file_conf in self.config.get("log_files", []):
            path = file_conf.get("path")
            exists = os.path.exists(path) if path else False
            result.append({
                "name": file_conf["name"],
                "label": file_conf["name"],
                "path": path,
                "exists": exists,
                "size": os.path.getsize(path) if exists else 0,
                "type": "file",
                "source": "local"
            })
        
        # 扫描本地目录
        for dir_config in self.config.get("log_directories", []):
            base_name = dir_config.get("name", "Scanned")
            scanned_files = self._scan_directory(dir_config)
            
            if scanned_files:
                tree_nodes = self._build_tree_structure(scanned_files, base_name)
                group_node = {
                    "name": base_name,
                    "label": base_name,
                    "type": "directory",
                    "source": "local",
                    "children": tree_nodes
                }
                result.append(group_node)
        
        return result
    
    async def get_file_list_async(self) -> List[Dict]:
        """获取完整文件列表（包括远程服务器），异步版本"""
        result = self.get_file_list()  # 先获取本地文件
        
        # 添加远程服务器
        for server_config in self.config.get("remote_servers", []):
            server_name = server_config.get("name", "Remote")
            
            try:
                # 构建远程服务器的文件树
                remote_tree = await self._build_remote_tree(server_config)
                
                if remote_tree:
                    # 创建服务器分组节点
                    server_node = {
                        "name": server_name,
                        "label": f"{server_name} 🌐",  # 添加图标标识远程服务器
                        "type": "directory",
                        "source": "remote",
                        "children": remote_tree
                    }
                    result.append(server_node)
            except Exception as e:
                print(f"[Remote] Failed to load server {server_name}: {e}")
                # 即使失败也添加节点，但标记为不可用
                result.append({
                    "name": server_name,
                    "label": f"{server_name} 🌐 (连接失败)",
                    "type": "directory",
                    "source": "remote",
                    "exists": False,
                    "children": []
                })
        
        return result

    def clear_log(self, file_name: str) -> bool:
        """清空日志文件（本地或远程）"""
        file_conf = self.files_map.get(file_name)
        if not file_conf:
            return False
        
        source = file_conf.get("source", "local")
        
        if source == "local":
            # 本地文件
            path = file_conf["path"]
            if os.path.exists(path):
                with open(path, 'w'):
                    pass
                return True
            return False
        else:
            # 远程文件 - 需要异步处理，这里返回 False，实际清空在 clear_log_async 中
            return False
    
    async def clear_log_async(self, file_name: str) -> bool:
        """清空日志文件（异步版本，支持远程）"""
        file_conf = self.files_map.get(file_name)
        
        # 如果在 files_map 中找不到，尝试解析远程目录文件
        if not file_conf:
            file_conf = await self._resolve_remote_file(file_name)
        
        if not file_conf:
            return False
        
        source = file_conf.get("source", "local")
        
        if source == "local":
            # 本地文件
            path = file_conf.get("path")
            if path and os.path.exists(path):
                with open(path, 'w'):
                    pass
                return True
            return False
        else:
            # 远程文件
            server_config = file_conf.get("server_config")
            file_path = file_conf.get("path")
            if server_config and file_path:
                return await self.remote_reader.clear_file(server_config, file_path)
            return False

    async def tail_file(self, file_name: str, request_args: dict) -> AsyncGenerator[Dict[str, str], None]:
        """
        生成器：实时读取日志文件（本地或远程）
        yields: 格式化的 SSE 数据块
        """
        file_conf = self.files_map.get(file_name)
        
        # 如果在 files_map 中找不到，尝试解析远程目录文件
        if not file_conf:
            file_conf = await self._resolve_remote_file(file_name)
        
        if not file_conf:
            yield {"data": "[SYSTEM] File not found or configured incorrectly."}
            return
        
        source = file_conf.get("source", "local")
        
        if source == "remote":
            # 远程文件
            server_config = file_conf.get("server_config")
            file_path = file_conf.get("path")
            encoding = file_conf.get("encoding", "utf-8")
            
            if not server_config or not file_path:
                yield {"data": "[SYSTEM] Remote file configuration error."}
                return
            
            # 使用远程读取器
            async for log_data in self.remote_reader.tail_file(server_config, file_path, encoding):
                yield log_data
            return
        
        # 本地文件处理（异步 + 文件监控）
        file_path = file_conf.get("path")
        if not os.path.exists(file_path):
            yield {"data": "[SYSTEM] File not found or configured incorrectly."}
            return

        encoding = file_conf.get("encoding", "utf-8")
        
        # 文件修改事件标志
        file_modified = asyncio.Event()
        
        try:
            # 初始读取历史日志（异步）
            async with aiofiles.open(file_path, 'r', encoding=encoding, errors='replace') as fp:
                # 获取文件大小
                file_size = os.path.getsize(file_path)
                if file_size > 0:
                    # 读取最后 10KB
                    read_size = min(file_size, 1024 * 10)
                    await fp.seek(file_size - read_size)
                    
                    # 丢弃第一行可能不完整的数据
                    if read_size < file_size:
                        await fp.readline()
                    
                    # 读取并发送现有内容
                    async for line in fp:
                        if line.strip():
                            clean_line = strip_ansi_codes(line.strip())
                            yield {"data": clean_line}
            
            # 获取当前文件位置（用于后续读取）
            current_position = os.path.getsize(file_path)
            
            # 启动文件监控（线程安全）
            observer = Observer()
            watch_dir = os.path.dirname(file_path)
            loop = asyncio.get_event_loop()
            
            event_handler = FileWatcher(file_path, file_modified, loop)
            observer.schedule(event_handler, watch_dir, recursive=False)
            observer.start()
            
            try:
                # 实时读取循环
                while True:
                    # 等待文件修改事件（带超时）
                    try:
                        await asyncio.wait_for(file_modified.wait(), timeout=2.0)
                        file_modified.clear()
                    except asyncio.TimeoutError:
                        # 超时，检查文件是否仍然存在
                        if not os.path.exists(file_path):
                            yield {"data": "[SYSTEM] File disappeared."}
                            break
                        continue
                    
                    # 文件被修改，读取新内容
                    try:
                        new_size = os.path.getsize(file_path)
                        
                        # 检查文件是否被截断
                        if new_size < current_position:
                            yield {"data": "[SYSTEM] File truncated. Reloading..."}
                            current_position = 0
                        
                        # 读取新行
                        async with aiofiles.open(file_path, 'r', encoding=encoding, errors='replace') as fp:
                            await fp.seek(current_position)
                            async for line in fp:
                                if line.strip():
                                    clean_line = strip_ansi_codes(line.strip())
                                    yield {"data": clean_line}
                            current_position = await fp.tell()
                    
                    except OSError as e:
                        # 文件可能正在被轮转
                        await asyncio.sleep(0.1)
                        continue
            
            finally:
                observer.stop()
                observer.join(timeout=1)
        
        except Exception as e:
            yield {"data": f"[SYSTEM] Error reading file: {str(e)}"}
    
    async def _resolve_remote_file(self, file_name: str) -> Optional[Dict]:
        """
        解析远程文件路径（用于动态扫描的目录文件）
        file_name 格式: "服务器名/目录名/相对路径"
        """
        # 尝试匹配远程服务器配置
        for server_config in self.config.get("remote_servers", []):
            server_name = server_config.get("name", "Remote")
            
            # 检查 file_name 是否以服务器名开头
            if not file_name.startswith(f"{server_name}/"):
                continue
            
            # 移除服务器名前缀
            remaining_path = file_name[len(server_name) + 1:]
            
            # 遍历该服务器的日志配置
            for log_config in server_config.get("logs", []):
                log_name = log_config.get("name")
                log_type = log_config.get("type", "file")
                
                # 如果是目录类型，尝试匹配
                if log_type == "directory" and remaining_path.startswith(f"{log_name}/"):
                    # 提取相对路径
                    rel_path = remaining_path[len(log_name) + 1:]
                    base_path = log_config.get("path")
                    
                    # 构建完整路径
                    full_path = os.path.join(base_path, rel_path)
                    
                    return {
                        "path": full_path,
                        "encoding": "utf-8",
                        "source": "remote",
                        "server_config": server_config
                    }
        
        return None
    
    async def cleanup(self):
        """清理资源"""
        await self.ssh_pool.close_all()
