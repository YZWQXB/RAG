# 知识库项目 · 内网穿透使用指南（cloudflared）

## 背景
把本机 Docker 跑的知识库（web 容器 :8080）临时暴露到公网，让外网用户无需进局域网即可访问。

## 2026-11-20 故障修复记录
启动报错 fork/exec ...cloudflared.exe: 文件或目录损坏且无法读取。
根因：C:\Users\Lenovo\.cftunnel\bin\cloudflared.exe 只有 6990 字节（正常约 53MB），是下载中断留下的截断文件。
修复：删除后从官方 releases 重新下载（52.9MB，版本 2026.11.1）。

修复命令：

    Remove-Item C:\Users\Lenovo\.cftunnel\bin\cloudflared.exe -Force
    Invoke-WebRequest -Uri 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' -OutFile 'C:\Users\Lenovo\.cftunnel\bin\cloudflared.exe'
    & C:\Users\Lenovo\.cftunnel\bin\cloudflared.exe --version   # 验证

## 方式一：快速临时隧道（免登录，推荐临时演示）

    & C:\Users\Lenovo\.cftunnel\bin\cloudflared.exe tunnel --url http://localhost:8080

- 启动后控制台输出一个 https://xxxx.trycloudflare.com 随机 URL
- 每次启动 URL 都会变；进程关闭即失效
- 免登录、免域名、免配置文件 —— 给朋友临时演示首选
- 本项目实测（2026-11-20）：生成 december-johnston-lincoln-waves.trycloudflare.com，页面 200、登录 API 正常

注意：输出被管道缓冲时看不到 URL，建议重定向到日志文件再从文件提取：

    & C:\Users\Lenovo\.cftunnel\bin\cloudflared.exe tunnel --url http://localhost:8080 *> .tmp-tunnel.log
    Select-String -Path .tmp-tunnel.log -Pattern 'trycloudflare'

## 方式二：命名隧道 + 自有域名（稳定，需 Cloudflare 账号）

    & C:\Users\Lenovo\.cftunnel\bin\cloudflared.exe tunnel login                 # 浏览器授权
    & C:\Users\Lenovo\.cftunnel\bin\cloudflared.exe tunnel create rag-kb        # 创建命名隧道
    & C:\Users\Lenovo\.cftunnel\bin\cloudflared.exe tunnel route dns rag-kb kb.你的域名.com
    & C:\Users\Lenovo\.cftunnel\bin\cloudflared.exe tunnel run rag-kb           # 运行（URL 固定不变）

配置文件示例（~/.cloudflared/config.yml）：

    tunnel: rag-kb
    credentials-file: C:\Users\Lenovo\.cloudflared\<tunnel-id>.json
    ingress:
      - hostname: kb.你的域名.com
        service: http://localhost:8080
      - service: http_status:404

可注册为 Windows 服务实现开机自启：

    & C:\Users\Lenovo\.cftunnel\bin\cloudflared.exe service install

## 安全提醒
- trycloudflare 随机 URL 知道的人都能访问，演示完及时关进程
- 快速隧道无额外鉴权层，认证完全依赖知识库自身的 JWT 登录
- 长期对外建议方式二 + Cloudflare Access 加一层身份验证
- 更大流量/更正式场景可换 frp / ngrok / 路由器端口映射
