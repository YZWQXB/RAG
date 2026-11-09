"""浏览器端到端自动校验脚本（基于 bsk CLI，驱动用户已登录的真实浏览器）。

完整链路：注册 → 建库 → 上传 → 等待入库就绪 → 提问 → 校验回答与引用。

为什么需要重试引擎：真实浏览器里可能有第三方扩展注入 DOM 并持续重渲染
（本项目实测被「大学搜题酱」扩展干扰），导致"观察 → 动作"之间页面发生变化，
bsk 共享控制守卫会拒绝动作并要求重新同步；同时 ref 编号会整体漂移。
本脚本的核心是 guarded_act()：每次动作前重新同步拿新鲜 context，
失败后按错误类型分类重试，动作前用 --depends-on 限定决策区域。

用法：
    python scripts/browser_verify.py                     # 默认 http://localhost:5173
    python scripts/browser_verify.py --base http://localhost:8080
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import time
import uuid
from pathlib import Path

BSK = os.environ.get("BSK_PATH", r"C:\Users\Lenovo\.local\bin\bsk.exe")
FILE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "sample.md"
MAX_ATTEMPTS = 8

# 可容忍的失败：重新同步后重试即可
RETRYABLE = (
    "shared page control or observation changed",  # 扩展重渲染竞态
    "cooperation_sync_required",  # 协调结果，等待后重试
    "missing target",  # ref 漂移，重新解析即可
)


def bsk(*args: str, timeout: int = 120) -> str:
    """执行 bsk 命令，合并 stdout/stderr 返回。"""
    env = dict(os.environ)
    env["BSK_AUTO_START"] = "0"
    result = subprocess.run(
        [BSK, *args], capture_output=True, text=True, encoding="utf-8", env=env, timeout=timeout
    )
    return (result.stdout or "") + (result.stderr or "")


def jdrop_noise(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if "new bsk version" not in line)


class BrowserVerify:
    def __init__(self, base: str) -> None:
        self.base = base
        self.session = ""
        self.tab_id = ""

    # ---------- 基础设施 ----------

    def start(self) -> None:
        if "daemon_version" not in bsk("status", "--json"):
            raise SystemExit(
                "bsk 守护进程未运行：请先在托管后台任务里运行 bsk daemon start --foreground"
            )
        out = json.loads(jdrop_noise(bsk("session", "start", "--json")))
        self.session = out["session_id"]
        print(f"[session] {self.session}")

    def stop(self) -> None:
        bsk("session", "stop", self.session)
        print("[session] stopped")

    def sync_ctx(self) -> str:
        out = bsk("cooperate", "sync", "--session", self.session)
        m = re.search(r'"context_id": "([a-f0-9-]+)"', out)
        if not m:
            raise RuntimeError("cooperate sync 未返回 context")
        return m.group(1)

    def snapshot_text(self) -> str:
        return jdrop_noise(bsk("snapshot", "--session", self.session))

    def find_ref(self, pattern: str) -> str | None:
        """从最新快照按正则找元素 ref。返回 None 表示元素不存在（与"找到了但漂移"区分）。"""
        m = re.search(rf"(@e\d+) [^\n]*{pattern}", self.snapshot_text())
        return m.group(1) if m else None

    # ---------- 重试引擎 ----------

    def guarded_act(self, verb: str, *args: str, depends_on: str | None = None) -> str:
        """带失败分类的守卫动作：重同步 → 动作 → 校验结果，必要时退避重试。

        失败分类与处置：
        1. observation changed / sync_required → 扩展竞态或协调等待：重新同步后重试
        2. missing target → ref 漂移：重新快照解析 ref 后重试
        3. dialog_pending → 原生对话框阻塞：读取对话框内容，只放过预期的确认框，
           其余一律 dismiss（防止 ref 漂移误触危险按钮）
        4. session_busy → 上一命令仍在执行：等待空闲
        5. 重试耗尽 → 如实报错退出，绝不盲目重放可能已生效的动作
        """
        for attempt in range(1, MAX_ATTEMPTS + 1):
            ctx = self.sync_ctx()
            cmd = [verb, *args, "--session", self.session, "--context", ctx]
            if depends_on:
                cmd += ["--depends-on", depends_on]
            out = jdrop_noise(bsk(*cmd))

            if f"{verb} ok" in out or "reached=load" in out:
                print(f"[ok] {verb} (attempt {attempt})")
                return out

            if "dialog" in out and "pending" in out:
                self._handle_dialog()
            elif "session_busy" in out:
                bsk("cooperate", "wait", "--session", self.session)
            elif any(marker in out for marker in RETRYABLE) or "missing target" in out:
                pass  # 下一轮循环重新解析
            else:
                print(out.strip()[-500:])
                raise RuntimeError(f"{verb} 出现不可重试的失败（见上方输出）")

            time.sleep(0.5 + random.random())  # 抖动退避，避免与扩展重渲染节奏共振
        raise RuntimeError(f"{verb} 重试 {MAX_ATTEMPTS} 次仍失败")

    def _handle_dialog(self) -> None:
        """原生对话框保护：只处理本流程预期的确认框，其余 dismiss。"""
        info = json.loads(jdrop_noise(bsk("dialog", "status", "--session", self.session, "--json")))
        for d in info.get("dialogs", []):
            if d["type"] == "confirm" and "创建" in d["message"]:
                bsk("dialog", "accept", d["id"], "--session", self.session)
                print(f"[dialog] accepted: {d['message']}")
            else:
                bsk("dialog", "dismiss", d["id"], "--session", self.session)
                print(f"[dialog] dismissed: {d['message']}")

    # ---------- 业务步骤 ----------

    def register(self) -> None:
        self.guarded_act("navigate", f"{self.base}/register")
        email = f"verify-{uuid.uuid4().hex[:8]}@example.com"
        for label, value in (("邮箱", email), ("密码", "Passw0rd!"), ("空间名称", "自动验证空间")):
            ref = self.find_ref(rf'textbox "{label}"')
            if not ref:
                raise RuntimeError(f"注册页缺少 {label} 输入框")
            self.guarded_act("fill", ref, "--value", value, depends_on=ref)
        ref = self.find_ref(r'button "创建空间"')
        self.guarded_act("click", ref, depends_on=ref)

    def create_kb(self) -> None:
        self.guarded_act("navigate", f"{self.base}/kbs")
        ref = self.find_ref(r'button "新建知识库"')
        self.guarded_act("click", ref, depends_on=ref)
        ref = self.find_ref(r'textbox "名称"')
        self.guarded_act("fill", ref, "--value", "自动验证知识库", depends_on=ref)
        ref = self.find_ref(r'button "创建"')
        self.guarded_act("click", ref, depends_on=ref)

    def upload(self) -> None:
        """上传：优先原生 upload 通道；被扩展竞态拦截则降级 evaluate 设置文件输入。"""
        ref = self.find_ref(r'button "选择文件上传')
        if ref:
            try:
                self.guarded_act("upload", ref, "--file", str(FILE), depends_on=ref)
                return
            except RuntimeError:
                print("[upload] 原生通道失败，降级 evaluate")
        self._upload_via_evaluate()

    def _upload_via_evaluate(self) -> None:
        content = json.dumps(FILE.read_text(encoding="utf-8"), ensure_ascii=False)
        js = (
            "(() => {"
            " const input = document.querySelector('input[type=file]');"
            " if (!input) return JSON.stringify({ok:false});"
            " const dt = new DataTransfer();"
            f" dt.items.add(new File([{content}], 'sample.md', {{type:'text/markdown'}}));"
            " input.files = dt.files;"
            " input.dispatchEvent(new Event('change', {bubbles:true}));"
            " return JSON.stringify({ok:true, size:input.files[0].size});"
            " })()"
        )
        ctx = self.sync_ctx()
        out = bsk(
            "evaluate",
            js,
            "--session",
            self.session,
            "--context",
            ctx,
            "--depends-on",
            "input[type=file]",
        )
        if '"ok":true' not in out:
            raise RuntimeError("evaluate 上传失败")

    def wait_doc_ready(self, timeout: float = 90) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            text = self.snapshot_text()
            if "可检索" in text:
                print("[doc] 可检索")
                return
            if "失败" in text and "sample" in text:
                match = re.search(r"sample[^\n]*失败", text)
                raise RuntimeError(f"文档入库失败：{match.group(0) if match else text[:200]}")
            time.sleep(3)
        raise RuntimeError("等待文档就绪超时")

    def ask(self, question: str) -> str:
        self.guarded_act("navigate", f"{self.base}/chat")
        ref = self.find_ref(r'textbox "输入问题')
        if not ref:
            raise RuntimeError("问答页缺少输入框")
        self.guarded_act("fill", ref, "--value", question, depends_on=ref)
        ref = self.find_ref(r'button "发送"')
        self.guarded_act("click", ref, depends_on=ref)

        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            text = self.snapshot_text()
            if "生成中" in text:
                time.sleep(2)
                continue
            m = re.search(r"根据资料[^\n]*", text)
            if m:
                print(f"[chat] 回答: {m.group(0)}")
                has_citation = bool(re.search(r"查看第 1 条引用|引用来源", text))
                print(f"[chat] 引用: {'有' if has_citation else '无'}")
                return m.group(0)
            time.sleep(2)
        raise RuntimeError("等待回答超时")

    def run(self, question: str) -> None:
        self.start()
        try:
            self.register()
            self.create_kb()
            self.upload()
            self.wait_doc_ready()
            self.ask(question)
            print("VERIFY OK")
        finally:
            self.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:5173")
    parser.add_argument("--question", default="年假有多少天？")
    args = parser.parse_args()
    BrowserVerify(args.base).run(args.question)
