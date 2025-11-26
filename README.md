# Tail-f Web 日志查看工具

现代化的 Web 日志实时查看工具，支持本地和远程服务器日志监控。

## 项目简介

Tail-f Web 是一个轻量级的日志查看工具，提供实时日志流、语法高亮、关键字搜索等功能。通过 Web 界面集中管理和查看多个服务器的日志文件。

### 核心功能

- **实时日志流** - 基于 SSE 推送，无需刷新页面
- **多文件支持** - 同时管理多个日志文件，支持目录扫描
- **远程日志** - 通过 SSH 安全访问远程服务器日志
- **关键字搜索** - 实时过滤日志内容
- **语法高亮** - 自动识别 INFO/WARN/ERROR/DEBUG 等日志级别
- **高性能** - 虚拟滚动技术，支持 20000+ 行日志流畅查看

### 技术架构

**后端：** FastAPI + Paramiko + aiofiles + watchdog  
**前端：** Vue 3 + Element Plus  
**特性：** 异步 I/O、文件监控、GZip 压缩、虚拟滚动

---

## 环境要求

- Python 3.9+
- Linux/macOS 系统
- 对于远程日志访问：SSH 密钥认证

---

## 部署步骤

### 1. 安装依赖

```bash
# 安装 Python 和 pip
# CentOS/RHEL
sudo yum install python39 python39-pip -y

# Ubuntu/Debian
sudo apt install python3 python3-pip -y

# 安装 uv 包管理器（可选，推荐）
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. 部署代码

```bash
# 上传代码到服务器
cd /opt
git clone <repository-url> tail-f
# 或使用 scp 上传压缩包后解压

cd tail-f
```

### 3. 安装项目依赖

```bash
# 方式 1：使用 uv（推荐）
uv venv
source .venv/bin/activate
uv pip install -e .

# 方式 2：使用 pip
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 4. 配置服务

编辑 `config/settings.yaml`：

```yaml
server:
  host: "0.0.0.0"
  port: 8000

# 本地日志配置
log_files:
  - name: "应用日志"
    path: "/var/log/app.log"
    encoding: "utf-8"

# 扫描目录
log_directories:
  - name: "Nginx日志"
    scan_dir: "/var/log/nginx"
    pattern: "*.log"
    recursive: false
    encoding: "utf-8"

# 远程服务器（可选）
remote_servers: []
# - name: "生产服务器"
#   host: "192.168.1.100"
#   port: 22
#   user: "logviewer"
#   auth_method: "key"
#   key_path: "/root/.ssh/id_ed25519"
#   allowed_paths:
#     - "/var/log"
#   max_file_size: 104857600
#   logs:
#     - name: "应用日志"
#       path: "/var/log/app.log"
#       type: "file"
```

### 5. 测试启动

```bash
python backend/main.py
```

访问 http://服务器IP:8000 验证是否正常运行。

### 6. 生产环境部署

#### 方式 1：使用 systemd（推荐）

创建服务文件 `/etc/systemd/system/tail-f.service`：

```ini
[Unit]
Description=Tail-f Web Log Viewer
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/tail-f
Environment="PATH=/opt/tail-f/.venv/bin"
ExecStart=/opt/tail-f/.venv/bin/python backend/main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

启动服务：

```bash
sudo systemctl daemon-reload
sudo systemctl enable tail-f
sudo systemctl start tail-f
sudo systemctl status tail-f
```

查看日志：

```bash
sudo journalctl -u tail-f -f
```

#### 方式 2：使用 nohup

```bash
cd /opt/tail-f
source .venv/bin/activate
nohup python backend/main.py > logs/app.log 2>&1 &
```

查看日志：

```bash
tail -f /opt/tail-f/logs/app.log
```

停止服务：

```bash
pkill -f "python backend/main.py"
```

---

## 配置说明

### 本地日志配置

#### 单个文件

```yaml
log_files:
  - name: "应用日志"
    path: "/var/log/app.log"
    encoding: "utf-8"
```

#### 目录扫描

```yaml
log_directories:
  - name: "系统日志"
    scan_dir: "/var/log"
    pattern: "*.log"      # 文件匹配模式
    recursive: true       # 递归扫描子目录
    encoding: "utf-8"
```

### 远程日志配置

#### 准备工作

1. **在远程服务器创建只读账户**

```bash
# 在远程服务器执行
sudo useradd -m -s /bin/bash logviewer
sudo usermod -aG adm logviewer  # 加入 adm 组以读取日志
```

2. **配置 SSH 密钥**

```bash
# 在 Tail-f 服务器执行
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_logviewer
ssh-copy-id -i ~/.ssh/id_ed25519_logviewer.pub logviewer@远程服务器IP
```

3. **测试连接**

```bash
ssh -i ~/.ssh/id_ed25519_logviewer logviewer@远程服务器IP
```

#### 配置示例

```yaml
remote_servers:
  - name: "生产服务器"
    host: "192.168.1.100"
    port: 22
    user: "logviewer"
    auth_method: "key"
    key_path: "/root/.ssh/id_ed25519_logviewer"
    
    # 安全配置（必须）
    allowed_paths:
      - "/var/log"
      - "/opt/app/logs"
    max_file_size: 104857600  # 100MB
    
    # 日志文件
    logs:
      - name: "Nginx访问日志"
        path: "/var/log/nginx/access.log"
        type: "file"
      
      - name: "应用日志目录"
        path: "/opt/app/logs"
        type: "directory"
        pattern: "*.log"
        recursive: true
```

### 安全配置说明

- **allowed_paths** - 路径白名单，只能访问这些目录
- **max_file_size** - 最大文件大小限制（字节）
- **auth_method** - 建议使用 `key`（SSH密钥），避免使用 `password`
- **用户权限** - 远程服务器建议使用专用只读账户

---

## 使用说明

### Web 界面

1. 访问 http://服务器IP:8000
2. 左侧文件树选择日志文件
3. 右侧实时显示日志内容

### 功能操作

- **搜索** - 在搜索框输入关键字过滤日志
- **自动滚动** - 点击工具栏自动滚动按钮，新日志自动跳转到底部
- **清空日志** - 点击清空按钮清空当前日志文件
- **字体调节** - 点击 A+ / A- 调整字体大小

---

## 维护管理

### 查看服务状态

```bash
# systemd 方式
sudo systemctl status tail-f

# 或查看进程
ps aux | grep "python backend/main.py"
```

### 重启服务

```bash
# systemd 方式
sudo systemctl restart tail-f

# nohup 方式
pkill -f "python backend/main.py"
cd /opt/tail-f && source .venv/bin/activate
nohup python backend/main.py > logs/app.log 2>&1 &
```

### 更新配置

修改 `config/settings.yaml` 后重启服务：

```bash
sudo systemctl restart tail-f
```

### 查看日志

```bash
# systemd 方式
sudo journalctl -u tail-f -f

# nohup 方式
tail -f /opt/tail-f/logs/app.log
```

---

## 故障排查

### 服务无法启动

1. **检查端口占用**

```bash
ss -tlnp | grep :8000
```

2. **检查配置文件**

```bash
cd /opt/tail-f
source .venv/bin/activate
python -c "import yaml; yaml.safe_load(open('config/settings.yaml'))"
```

3. **查看错误日志**

```bash
sudo journalctl -u tail-f -n 50
```

### 无法访问日志文件

1. **检查文件权限**

```bash
ls -la /var/log/app.log
sudo chmod 644 /var/log/app.log
```

2. **检查路径是否正确**

确认 `config/settings.yaml` 中配置的路径存在。

### 远程服务器连接失败

1. **测试 SSH 连接**

```bash
ssh -i /root/.ssh/id_ed25519_logviewer logviewer@远程IP
```

2. **检查密钥权限**

```bash
chmod 600 /root/.ssh/id_ed25519_logviewer
```

3. **检查路径白名单**

确认访问的路径在 `allowed_paths` 列表中。

### 日志不更新

1. **检查文件编码**

确认配置的 `encoding` 与实际文件编码一致。

2. **检查浏览器控制台**

按 F12 查看是否有 JavaScript 错误或网络错误。

---

## 性能优化

### 已实现的优化

- ✅ **虚拟滚动** - 支持 20000+ 行日志流畅查看
- ✅ **语法高亮缓存** - 减少重复计算
- ✅ **防抖搜索** - 减少无效过滤
- ✅ **异步文件读取** - 使用 aiofiles
- ✅ **文件监控** - watchdog 替代轮询
- ✅ **GZip 压缩** - 减少 70% 网络带宽

### 性能指标

- 日志容量：20000 行流畅运行
- 内存占用：~30MB（20000行）
- CPU 使用：<1%（空闲时）
- 响应延迟：<10ms

---

## 目录结构

```
tail-f/
├── backend/              # 后端代码
│   ├── main.py          # FastAPI 入口
│   ├── log_core.py      # 日志管理核心
│   ├── ssh_manager.py   # SSH 连接管理
│   └── security.py      # 安全验证
├── config/              # 配置文件
│   └── settings.yaml
├── static/              # 前端资源
│   ├── index.html
│   ├── lib/            # 前端库
│   └── log.svg
├── docs/               # 文档
├── logs/               # 运行日志
├── .gitignore
├── pyproject.toml
└── README.md
```

---

## 反向代理配置（可选）

### Nginx 配置

```nginx
server {
    listen 80;
    server_name logs.yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        
        # SSE 配置
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 86400;
    }
}
```

---

## 安全建议

1. **使用 SSH 密钥** - 避免使用密码认证
2. **配置路径白名单** - 严格限制可访问的目录
3. **最小权限原则** - 远程服务器使用只读账户
4. **启用防火墙** - 限制 8000 端口的访问来源
5. **定期更新** - 保持依赖包更新

---

## 技术支持

遇到问题请查看：
- [远程配置指南](docs/REMOTE_SETUP.md)
- [安全说明](docs/SECURITY.md)

---

**版本：** 1.0.0  
**更新时间：** 2024-11
