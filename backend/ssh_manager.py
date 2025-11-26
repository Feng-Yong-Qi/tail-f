"""
SSH 连接管理器
负责管理远程服务器的 SSH 连接，提供连接池和安全控制
"""
import asyncio
import os
import time
import re
from typing import Dict, Optional, AsyncGenerator
from pathlib import Path
import paramiko
from paramiko import SSHClient, AutoAddPolicy, RSAKey, Ed25519Key
from backend.security import SecurityValidator

# ANSI 颜色代码的正则表达式（支持带和不带 ESC 前缀的格式）
ANSI_ESCAPE_PATTERN = re.compile(r'(\x1B\[[0-?]*[ -/]*[@-~]|\[[0-9;]+m)')

def strip_ansi_codes(text: str) -> str:
    """移除 ANSI 颜色代码"""
    return ANSI_ESCAPE_PATTERN.sub('', text)


class SSHConnectionPool:
    """SSH 连接池"""
    
    def __init__(self, max_connections: int = 10, timeout: int = 300):
        """
        初始化连接池
        
        Args:
            max_connections: 最大连接数
            timeout: 连接超时时间（秒）
        """
        self.max_connections = max_connections
        self.timeout = timeout
        self.connections: Dict[str, Dict] = {}  # {server_id: {client, last_used, config}}
        self._lock = asyncio.Lock()
    
    async def get_connection(self, server_config: Dict) -> Optional[SSHClient]:
        """
        获取或创建 SSH 连接
        
        Args:
            server_config: 服务器配置
            
        Returns:
            SSHClient: SSH 客户端实例
        """
        server_id = f"{server_config['host']}:{server_config.get('port', 22)}"
        
        async with self._lock:
            # 检查是否有现有连接
            if server_id in self.connections:
                conn_info = self.connections[server_id]
                client = conn_info['client']
                
                # 检查连接是否仍然有效
                try:
                    transport = client.get_transport()
                    if transport and transport.is_active():
                        conn_info['last_used'] = time.time()
                        return client
                except Exception:
                    pass
                
                # 连接已失效，关闭并移除
                try:
                    client.close()
                except Exception:
                    pass
                del self.connections[server_id]
            
            # 创建新连接
            try:
                client = await self._create_connection(server_config)
                self.connections[server_id] = {
                    'client': client,
                    'last_used': time.time(),
                    'config': server_config
                }
                return client
            except Exception as e:
                print(f"[SSH] Failed to connect to {server_id}: {e}")
                return None
    
    async def _create_connection(self, config: Dict) -> SSHClient:
        """
        创建新的 SSH 连接
        
        Args:
            config: 服务器配置
            
        Returns:
            SSHClient: SSH 客户端实例
        """
        client = SSHClient()
        
        # 安全策略：只接受已知主机（生产环境应使用 known_hosts）
        # 这里为了演示使用 AutoAddPolicy，实际部署时应该更严格
        client.set_missing_host_key_policy(AutoAddPolicy())
        
        host = config['host']
        port = config.get('port', 22)
        user = config['user']
        auth_method = config.get('auth_method', 'key')
        
        connect_kwargs = {
            'hostname': host,
            'port': port,
            'username': user,
            'timeout': 10,
            'banner_timeout': 10,
            'auth_timeout': 10,
        }
        
        # 根据认证方式连接
        if auth_method == 'key':
            key_path = config.get('key_path')
            if not key_path or not os.path.exists(key_path):
                raise ValueError(f"SSH key not found: {key_path}")
            
            # 验证密钥文件权限（应该是 600）
            key_stat = os.stat(key_path)
            if key_stat.st_mode & 0o077:
                print(f"[SSH] Warning: Key file {key_path} has insecure permissions")
            
            connect_kwargs['key_filename'] = key_path
        elif auth_method == 'password':
            password = config.get('password')
            if not password:
                raise ValueError("Password not provided")
            connect_kwargs['password'] = password
        else:
            raise ValueError(f"Unsupported auth method: {auth_method}")
        
        # 在线程池中执行阻塞的 SSH 连接
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: client.connect(**connect_kwargs))
        
        return client
    
    async def cleanup_idle_connections(self):
        """清理空闲连接"""
        async with self._lock:
            current_time = time.time()
            to_remove = []
            
            for server_id, conn_info in self.connections.items():
                if current_time - conn_info['last_used'] > self.timeout:
                    to_remove.append(server_id)
            
            for server_id in to_remove:
                try:
                    self.connections[server_id]['client'].close()
                except Exception:
                    pass
                del self.connections[server_id]
                print(f"[SSH] Closed idle connection to {server_id}")
    
    async def close_all(self):
        """关闭所有连接"""
        async with self._lock:
            for conn_info in self.connections.values():
                try:
                    conn_info['client'].close()
                except Exception:
                    pass
            self.connections.clear()


class RemoteFileReader:
    """远程文件读取器"""
    
    def __init__(self, ssh_pool: SSHConnectionPool):
        """
        初始化远程文件读取器
        
        Args:
            ssh_pool: SSH 连接池
        """
        self.ssh_pool = ssh_pool
        self.validator = SecurityValidator()
    
    async def list_files(self, server_config: Dict, directory: str, pattern: str = "*", 
                        recursive: bool = False) -> list:
        """
        列出远程目录中的文件
        
        Args:
            server_config: 服务器配置
            directory: 目录路径
            pattern: 文件匹配模式
            recursive: 是否递归扫描
            
        Returns:
            list: 文件列表
        """
        # 安全验证
        allowed_paths = server_config.get('allowed_paths', [])
        if not self.validator.validate_path(directory, allowed_paths):
            print(f"[Security] Path rejected: {directory}")
            return []
        
        client = await self.ssh_pool.get_connection(server_config)
        if not client:
            return []
        
        try:
            # 构建安全的 find 命令（不使用管道或重定向）
            # 使用绝对路径并加上引号，避免空格等问题
            if recursive:
                cmd = f"find '{directory}' -type f -name '{pattern}'"
            else:
                cmd = f"find '{directory}' -maxdepth 1 -type f -name '{pattern}'"

            # 验证命令安全性（整个命令字符串中不能包含危险字符）
            if not self.validator.validate_command(cmd):
                print(f"[Security] Command rejected: {cmd}")
                return []

            # 执行命令
            loop = asyncio.get_event_loop()
            stdin, stdout, stderr = await loop.run_in_executor(
                None, client.exec_command, cmd
            )

            files = []
            line_count = 0
            max_files = 1000  # 限制最多返回 1000 个文件

            for line in stdout:
                if line_count >= max_files:
                    break
                file_path = line.strip()
                if file_path:
                    files.append({
                        "path": file_path,
                        "name": os.path.basename(file_path)
                    })
                    line_count += 1

            return files
        except Exception as e:
            print(f"[SSH] Error listing files: {e}")
            return []
    
    async def tail_file(self, server_config: Dict, file_path: str, 
                       encoding: str = "utf-8") -> AsyncGenerator[Dict[str, str], None]:
        """
        实时读取远程文件（tail -f）
        
        Args:
            server_config: 服务器配置
            file_path: 文件路径
            encoding: 文件编码
            
        Yields:
            Dict: 日志行数据
        """
        # 安全验证
        allowed_paths = server_config.get('allowed_paths', [])
        if not self.validator.validate_path(file_path, allowed_paths):
            yield {"data": f"[SECURITY] Access denied: {file_path}"}
            return
        
        client = await self.ssh_pool.get_connection(server_config)
        if not client:
            yield {"data": "[ERROR] Failed to connect to remote server"}
            return
        
        try:
            # 先读取文件最后 10KB 的历史内容
            max_size = server_config.get('max_file_size', 104857600)
            cmd_check = f"stat -c %s {file_path} 2>/dev/null || echo 0"
            
            loop = asyncio.get_event_loop()
            stdin, stdout, stderr = await loop.run_in_executor(
                None, client.exec_command, cmd_check
            )
            file_size = int(stdout.read().decode().strip() or 0)
            
            if not self.validator.check_file_size(file_size, max_size):
                yield {"data": f"[ERROR] File too large: {file_size} bytes (max: {max_size})"}
                return
            
            # 读取历史日志（最后 10KB）
            if file_size > 0:
                read_size = min(file_size, 10240)
                cmd_history = f"tail -c {read_size} {file_path}"
                
                stdin, stdout, stderr = await loop.run_in_executor(
                    None, client.exec_command, cmd_history
                )
                
                for line in stdout:
                    decoded_line = line.strip()
                    if decoded_line:
                        # 移除 ANSI 颜色代码
                        clean_line = strip_ansi_codes(decoded_line)
                        yield {"data": clean_line}
            
            # 开始实时 tail
            cmd_tail = f"tail -f {file_path}"
            
            if not self.validator.validate_command(cmd_tail):
                yield {"data": f"[SECURITY] Command rejected: {cmd_tail}"}
                return
            
            stdin, stdout, stderr = await loop.run_in_executor(
                None, client.exec_command, cmd_tail
            )
            
            # 持续读取新行
            while True:
                try:
                    # 在线程池中读取一行
                    line = await loop.run_in_executor(None, stdout.readline)
                    if line:
                        decoded_line = line.strip()
                        if decoded_line:
                            # 移除 ANSI 颜色代码
                            clean_line = strip_ansi_codes(decoded_line)
                            yield {"data": clean_line}
                    else:
                        await asyncio.sleep(0.1)
                except Exception as e:
                    print(f"[SSH] Error reading line: {e}")
                    break
                    
        except Exception as e:
            yield {"data": f"[ERROR] Failed to read remote file: {str(e)}"}
    
    async def clear_file(self, server_config: Dict, file_path: str) -> bool:
        """
        清空远程文件
        
        Args:
            server_config: 服务器配置
            file_path: 文件路径
            
        Returns:
            bool: 是否成功
        """
        # 安全验证
        allowed_paths = server_config.get('allowed_paths', [])
        if not self.validator.validate_path(file_path, allowed_paths):
            print(f"[Security] Clear file rejected: {file_path}")
            return False
        
        client = await self.ssh_pool.get_connection(server_config)
        if not client:
            return False
        
        try:
            # 使用 truncate 命令清空文件（比 > 更安全）
            cmd = f"truncate -s 0 {file_path}"
            
            loop = asyncio.get_event_loop()
            stdin, stdout, stderr = await loop.run_in_executor(
                None, client.exec_command, cmd
            )
            
            # 检查是否有错误
            error_output = stderr.read().decode().strip()
            if error_output:
                print(f"[SSH] Error clearing file: {error_output}")
                return False
            
            return True
        except Exception as e:
            print(f"[SSH] Error clearing file: {e}")
            return False

