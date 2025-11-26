"""
安全验证模块
提供路径验证、权限检查等安全机制
"""
import os
import re
from pathlib import Path
from typing import List, Optional


class SecurityValidator:
    """安全验证器"""
    
    # 危险路径模式（黑名单）
    DANGEROUS_PATTERNS = [
        r'\.\.',  # 路径穿越
        r'/etc/shadow',  # 敏感系统文件
        r'/etc/passwd',
        r'/root/\.ssh',  # SSH 密钥
        r'\.pem$',  # 私钥文件
        r'\.key$',
        r'/proc/',  # 系统进程信息
        r'/sys/',
    ]
    
    # 允许的命令白名单
    ALLOWED_COMMANDS = ['tail', 'cat', 'head', 'ls', 'find']
    
    @staticmethod
    def validate_path(path: str, allowed_paths: List[str]) -> bool:
        """
        验证路径是否安全
        
        Args:
            path: 要验证的路径
            allowed_paths: 允许的路径白名单
            
        Returns:
            bool: 路径是否安全
        """
        # 规范化路径，防止路径穿越
        try:
            normalized_path = os.path.normpath(path)
            resolved_path = os.path.abspath(normalized_path)
        except Exception:
            return False
        
        # 检查危险模式
        for pattern in SecurityValidator.DANGEROUS_PATTERNS:
            if re.search(pattern, resolved_path, re.IGNORECASE):
                return False
        
        # 检查是否在白名单内
        if not allowed_paths:
            return False
            
        for allowed in allowed_paths:
            allowed_resolved = os.path.abspath(os.path.normpath(allowed))
            if resolved_path.startswith(allowed_resolved):
                return True
        
        return False
    
    @staticmethod
    def validate_command(command: str) -> bool:
        """
        验证命令是否安全
        
        Args:
            command: 要执行的命令
            
        Returns:
            bool: 命令是否安全
        """
        # 提取命令名称（第一个单词）
        cmd_name = command.strip().split()[0] if command.strip() else ""
        
        # 检查是否在白名单内
        if cmd_name not in SecurityValidator.ALLOWED_COMMANDS:
            return False
        
        # 检查是否包含危险字符
        dangerous_chars = [';', '|', '&', '$', '`', '>', '<', '\n', '\r']
        for char in dangerous_chars:
            if char in command:
                return False
        
        return True
    
    @staticmethod
    def sanitize_filename(filename: str) -> str:
        """
        清理文件名，移除危险字符
        
        Args:
            filename: 原始文件名
            
        Returns:
            str: 清理后的文件名
        """
        # 只保留字母、数字、下划线、点、横线
        return re.sub(r'[^\w\.\-]', '_', filename)
    
    @staticmethod
    def check_file_size(size: int, max_size: int = 104857600) -> bool:
        """
        检查文件大小是否在允许范围内
        
        Args:
            size: 文件大小（字节）
            max_size: 最大允许大小（默认 100MB）
            
        Returns:
            bool: 是否在允许范围内
        """
        return 0 <= size <= max_size

