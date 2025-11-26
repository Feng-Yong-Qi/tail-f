# 远程服务器日志查看配置指南

## 安全要求

**⚠️ 重要：远程日志访问涉及服务器安全，请务必遵循以下安全实践**

### 1. 创建专用只读账户

在远程服务器上创建一个专用的只读账户，不要使用 root 或管理员账户：

```bash
# 在远程服务器上执行
sudo useradd -m -s /bin/bash logviewer
sudo passwd logviewer  # 设置密码（如果使用密码认证）
```

### 2. 配置 SSH 密钥认证（推荐）

**在 tail-f 服务器上生成密钥对：**

```bash
# 生成专用密钥对
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_logviewer -C "logviewer"

# 设置正确的权限
chmod 600 ~/.ssh/id_ed25519_logviewer
chmod 644 ~/.ssh/id_ed25519_logviewer.pub
```

**将公钥复制到远程服务器：**

```bash
# 方法 1：使用 ssh-copy-id
ssh-copy-id -i ~/.ssh/id_ed25519_logviewer.pub logviewer@192.168.1.100

# 方法 2：手动复制
cat ~/.ssh/id_ed25519_logviewer.pub | ssh logviewer@192.168.1.100 "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
```

### 3. 配置日志目录权限

确保 logviewer 账户可以读取日志文件：

```bash
# 在远程服务器上执行
# 方法 1：将用户添加到日志组
sudo usermod -aG adm logviewer  # Debian/Ubuntu
sudo usermod -aG systemd-journal logviewer  # 系统日志

# 方法 2：使用 ACL 授予特定目录权限
sudo setfacl -R -m u:logviewer:rx /var/log/nginx
sudo setfacl -R -m u:logviewer:rx /var/log/myapp

# 验证权限
sudo -u logviewer cat /var/log/nginx/access.log
```

### 4. 限制 SSH 访问（可选但推荐）

编辑远程服务器的 SSH 配置 `/etc/ssh/sshd_config`：

```bash
# 限制 logviewer 用户只能执行特定命令
Match User logviewer
    ForceCommand /usr/local/bin/log-viewer-shell.sh
    PermitTTY no
    X11Forwarding no
    AllowTcpForwarding no
```

创建受限 shell 脚本 `/usr/local/bin/log-viewer-shell.sh`：

```bash
#!/bin/bash
# 只允许执行 tail, cat, find 等安全命令
case "$SSH_ORIGINAL_COMMAND" in
    tail\ -f\ /var/log/*|tail\ -c\ *\ /var/log/*|find\ /var/log/*|stat\ -c\ *\ /var/log/*)
        eval "$SSH_ORIGINAL_COMMAND"
        ;;
    *)
        echo "Command not allowed"
        exit 1
        ;;
esac
```

设置权限：

```bash
sudo chmod +x /usr/local/bin/log-viewer-shell.sh
```

重启 SSH 服务：

```bash
sudo systemctl restart sshd
```

## 配置示例

### 基本配置（单个文件）

```yaml
remote_servers:
  - name: "生产服务器A"
    host: "192.168.1.100"
    port: 22
    user: "logviewer"
    auth_method: "key"
    key_path: "/root/.ssh/id_ed25519_logviewer"
    
    # 安全白名单（必须配置）
    allowed_paths:
      - "/var/log"
      - "/mnt/application/logs"
    max_file_size: 104857600  # 100MB
    
    logs:
      - name: "Nginx访问日志"
        path: "/var/log/nginx/access.log"
        type: "file"
```

### 扫描整个目录

```yaml
remote_servers:
  - name: "生产服务器B"
    host: "192.168.1.101"
    port: 22
    user: "logviewer"
    auth_method: "key"
    key_path: "/root/.ssh/id_ed25519_logviewer"
    
    allowed_paths:
      - "/var/log"
    max_file_size: 104857600
    
    logs:
      - name: "系统日志"
        path: "/var/log"
        type: "directory"
        pattern: "*.log"
        recursive: true
      
      - name: "应用日志"
        path: "/mnt/application/logs"
        type: "directory"
        pattern: "app-*.log"
        recursive: false
```

### 使用密码认证（不推荐）

```yaml
remote_servers:
  - name: "测试服务器"
    host: "192.168.1.102"
    port: 22
    user: "logviewer"
    auth_method: "password"
    password: "your_secure_password"  # 建议使用环境变量
    
    allowed_paths:
      - "/var/log"
    max_file_size: 52428800  # 50MB
    
    logs:
      - name: "测试日志"
        path: "/var/log/test.log"
        type: "file"
```

## 安全机制说明

### 1. 路径白名单验证

所有文件访问都会经过严格的路径验证：

- ✅ 只能访问 `allowed_paths` 中配置的目录
- ✅ 自动防止路径穿越攻击（`../../../etc/passwd`）
- ✅ 阻止访问敏感系统文件（`/etc/shadow`, `/root/.ssh` 等）

### 2. 命令白名单

只允许执行安全的只读命令：

- ✅ `tail` - 查看文件末尾
- ✅ `cat` - 读取文件内容
- ✅ `head` - 查看文件开头
- ✅ `ls` - 列出文件
- ✅ `find` - 查找文件
- ❌ `rm`, `mv`, `chmod` 等危险命令被禁止

### 3. 文件大小限制

- 默认最大文件大小：100MB
- 防止读取超大文件导致内存溢出
- 可在配置中自定义 `max_file_size`

### 4. SSH 连接池管理

- 自动管理 SSH 连接，避免频繁建立连接
- 空闲连接自动超时关闭（默认 5 分钟）
- 连接失败自动重试

### 5. 错误隔离

- 单个远程服务器连接失败不影响其他服务器
- 前端会显示连接状态（🌐 图标 + 连接失败提示）

## 测试连接

配置完成后，可以手动测试 SSH 连接：

```bash
# 测试密钥认证
ssh -i /root/.ssh/id_ed25519_logviewer logviewer@192.168.1.100 "tail -n 10 /var/log/nginx/access.log"

# 测试文件访问权限
ssh -i /root/.ssh/id_ed25519_logviewer logviewer@192.168.1.100 "ls -la /var/log"
```

如果命令能正常执行，说明配置正确。

## 故障排查

### 连接失败

1. 检查 SSH 密钥权限：`ls -la ~/.ssh/id_ed25519_logviewer`（应该是 600）
2. 检查远程服务器是否可达：`ping 192.168.1.100`
3. 检查 SSH 端口是否开放：`telnet 192.168.1.100 22`
4. 查看 tail-f 后端日志：`python backend/main.py`

### 权限拒绝

1. 确认 logviewer 用户有读取权限：`sudo -u logviewer cat /var/log/xxx.log`
2. 检查 SELinux 状态：`getenforce`（如果是 Enforcing，可能需要配置策略）
3. 检查文件 ACL：`getfacl /var/log/xxx.log`

### 路径被拒绝

1. 检查 `allowed_paths` 配置是否包含该路径
2. 确认路径是绝对路径（以 `/` 开头）
3. 查看后端日志中的 `[Security]` 提示

## 性能优化

### 1. 减少扫描目录的文件数量

```yaml
logs:
  - name: "最近日志"
    path: "/var/log"
    type: "directory"
    pattern: "*.log"
    recursive: false  # 不递归，只扫描顶层
```

### 2. 使用更精确的文件模式

```yaml
logs:
  - name: "Nginx日志"
    path: "/var/log/nginx"
    type: "directory"
    pattern: "access-*.log"  # 只匹配特定格式
    recursive: false
```

### 3. 调整连接池大小

如果有很多远程服务器，可以在代码中调整连接池大小：

```python
# backend/log_core.py
self.ssh_pool = SSHConnectionPool(max_connections=20, timeout=600)
```

## 安全检查清单

部署前请确认：

- [ ] 已创建专用的只读账户（不是 root）
- [ ] 使用 SSH 密钥认证（不是密码）
- [ ] 密钥文件权限正确（600）
- [ ] 配置了 `allowed_paths` 白名单
- [ ] 远程用户只有日志目录的读取权限
- [ ] 测试过 SSH 连接和文件读取
- [ ] 考虑使用 SSH ForceCommand 限制命令执行
- [ ] 定期审计日志访问记录

## 高级配置

### 使用跳板机

如果需要通过跳板机访问远程服务器，可以配置 SSH ProxyJump：

在 `~/.ssh/config` 中添加：

```
Host production-server
    HostName 10.0.1.100
    User logviewer
    IdentityFile ~/.ssh/id_ed25519_logviewer
    ProxyJump jumphost@bastion.example.com
```

然后在配置中使用别名：

```yaml
remote_servers:
  - name: "生产服务器"
    host: "production-server"  # 使用 SSH config 中的别名
    port: 22
    user: "logviewer"
    auth_method: "key"
    key_path: "/root/.ssh/id_ed25519_logviewer"
```

### 环境变量存储密码

不要在配置文件中明文存储密码，使用环境变量：

```bash
export REMOTE_SERVER_PASSWORD="your_password"
```

然后在代码中读取（需要修改 `backend/log_core.py`）。

---

**如有问题，请查看后端日志或提交 Issue。**

