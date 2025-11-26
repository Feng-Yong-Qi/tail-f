# 安全机制说明

## 概述

Tail-f Web Viewer 在设计时将安全性作为首要考虑因素，特别是在远程日志访问功能中实施了多层安全防护。

## 安全架构

```
┌─────────────────────────────────────────────────────────────┐
│                        前端 (Vue.js)                         │
│                  - 用户界面                                   │
│                  - 文件选择                                   │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTPS (建议)
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                    后端 (FastAPI)                            │
│  ┌──────────────────────────────────────────────────────┐  │
│  │           SecurityValidator (安全验证器)              │  │
│  │  - 路径白名单验证                                     │  │
│  │  - 命令白名单验证                                     │  │
│  │  - 文件大小检查                                       │  │
│  │  - 危险模式检测                                       │  │
│  └──────────────────────────────────────────────────────┘  │
│                         │                                    │
│  ┌──────────────────────┴──────────────────────────────┐  │
│  │         SSHConnectionPool (连接池)                   │  │
│  │  - SSH 密钥认证                                       │  │
│  │  - 连接超时管理                                       │  │
│  │  - 连接复用                                           │  │
│  └──────────────────────┬──────────────────────────────┘  │
└─────────────────────────┼────────────────────────────────────┘
                          │ SSH (加密)
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                   远程服务器                                 │
│  ┌──────────────────────────────────────────────────────┐  │
│  │         专用只读账户 (logviewer)                      │  │
│  │  - 受限 Shell (可选)                                  │  │
│  │  - 最小权限原则                                       │  │
│  │  - 只读日志文件                                       │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

## 核心安全机制

### 1. 路径验证 (Path Validation)

**目的**：防止路径穿越攻击和未授权文件访问

**实现**：
- 所有文件路径都会经过 `SecurityValidator.validate_path()` 验证
- 使用 `os.path.normpath()` 和 `os.path.abspath()` 规范化路径
- 检查路径是否在 `allowed_paths` 白名单内
- 阻止包含危险模式的路径（`..`, `/etc/shadow`, `/root/.ssh` 等）

**示例**：
```python
# 攻击尝试
path = "/var/log/../../../etc/passwd"

# 验证结果
validate_path(path, ["/var/log"])  # ❌ 返回 False，访问被拒绝
```

### 2. 命令白名单 (Command Whitelist)

**目的**：防止远程命令注入和执行危险操作

**实现**：
- 只允许执行预定义的安全命令：`tail`, `cat`, `head`, `ls`, `find`
- 检测并阻止命令注入字符：`;`, `|`, `&`, `` ` ``, `$`, `>`, `<`
- 使用 `paramiko` 的 `exec_command` 而不是 shell

**示例**：
```python
# 攻击尝试
cmd = "tail /var/log/app.log; rm -rf /"

# 验证结果
validate_command(cmd)  # ❌ 返回 False，包含危险字符 `;`
```

### 3. 文件大小限制 (File Size Limit)

**目的**：防止内存溢出和 DoS 攻击

**实现**：
- 默认最大文件大小：100MB
- 在读取文件前检查大小
- 可在配置中自定义 `max_file_size`

**示例**：
```python
# 检查文件大小
file_size = 200 * 1024 * 1024  # 200MB
check_file_size(file_size, max_size=100*1024*1024)  # ❌ 返回 False
```

### 4. SSH 密钥认证 (SSH Key Authentication)

**目的**：避免密码泄露，提供更强的身份验证

**实现**：
- 推荐使用 Ed25519 或 RSA 密钥
- 密钥文件权限检查（应为 600）
- 支持密码认证作为备选（不推荐）

**最佳实践**：
```bash
# 生成专用密钥
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_logviewer

# 设置正确权限
chmod 600 ~/.ssh/id_ed25519_logviewer
```

### 5. 连接池管理 (Connection Pool)

**目的**：优化性能并防止连接泄露

**实现**：
- 最大连接数限制（默认 10）
- 空闲连接自动超时（默认 5 分钟）
- 连接健康检查
- 异常连接自动清理

### 6. 最小权限原则 (Principle of Least Privilege)

**目的**：限制潜在的攻击面

**实现**：
- 远程服务器使用专用只读账户
- 不使用 root 或管理员账户
- 只授予日志目录的读取权限
- 可选：使用 SSH ForceCommand 限制可执行命令

**示例**：
```bash
# 在远程服务器上
sudo useradd -m -s /bin/bash logviewer
sudo usermod -aG adm logviewer  # 只读日志组
```

### 7. 错误隔离 (Error Isolation)

**目的**：防止单点故障影响整个系统

**实现**：
- 单个远程服务器连接失败不影响其他服务器
- 异常捕获和日志记录
- 前端显示连接状态

## 安全配置检查清单

### 部署前检查

- [ ] **SSH 密钥认证**
  - 使用 Ed25519 或 RSA 4096 密钥
  - 密钥文件权限为 600
  - 公钥已添加到远程服务器

- [ ] **远程账户配置**
  - 创建了专用的 logviewer 账户
  - 账户只有日志目录的读取权限
  - 未使用 root 或管理员账户

- [ ] **路径白名单**
  - 配置了 `allowed_paths`
  - 只包含必要的日志目录
  - 不包含敏感系统目录

- [ ] **文件大小限制**
  - 设置了合理的 `max_file_size`
  - 根据服务器内存调整

- [ ] **网络安全**
  - SSH 端口未暴露到公网（或使用防火墙限制）
  - 考虑使用 VPN 或跳板机
  - 启用 SSH 连接日志审计

### 运行时监控

- [ ] 定期检查 SSH 连接日志
- [ ] 监控异常的文件访问尝试
- [ ] 审计 `[Security]` 标记的日志
- [ ] 检查连接池状态

## 已知限制

### 1. 密码认证

如果使用密码认证，密码会存储在配置文件中（明文或环境变量）。

**缓解措施**：
- 强烈推荐使用 SSH 密钥认证
- 如必须使用密码，存储在环境变量中
- 限制配置文件的读取权限（chmod 600）

### 2. SSH Known Hosts

当前使用 `AutoAddPolicy`，会自动接受未知主机密钥。

**缓解措施**：
- 生产环境应使用 `RejectPolicy` 并手动添加主机密钥
- 修改 `backend/ssh_manager.py` 中的策略：
  ```python
  client.load_system_host_keys()
  client.set_missing_host_key_policy(paramiko.RejectPolicy())
  ```

### 3. 日志内容过滤

当前不过滤日志内容中的敏感信息（如密码、Token）。

**缓解措施**：
- 在应用层面避免记录敏感信息
- 考虑在前端或后端添加敏感信息脱敏功能

## 安全更新

### 依赖项

定期更新安全相关的依赖：

```bash
uv pip install --upgrade paramiko cryptography
```

### 漏洞扫描

建议定期运行安全扫描：

```bash
# 使用 safety 检查已知漏洞
uv pip install safety
safety check

# 使用 bandit 检查代码安全问题
uv pip install bandit
bandit -r backend/
```

## 报告安全问题

如果发现安全漏洞，请通过以下方式报告：

1. **不要**在公开 Issue 中披露
2. 发送邮件到安全团队（如果有）
3. 提供详细的复现步骤和影响范围

## 参考资料

- [OWASP Top 10](https://owasp.org/www-project-top-ten/)
- [SSH Security Best Practices](https://www.ssh.com/academy/ssh/security)
- [Python Security Best Practices](https://python.readthedocs.io/en/stable/library/security_warnings.html)
- [Paramiko Documentation](https://docs.paramiko.org/)

---

**安全是一个持续的过程，请定期审查和更新安全配置。**

