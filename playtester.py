"""Visible AI playtesting dashboard. Run with Python 3.10+ (Tkinter)."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from engine import DecisionError, GameProcess, ModelClient, ROOT, SafeError, Session, deepseek_preset, load_config


class Cancelled(Exception):
    pass


class Dashboard:
    def __init__(self, root: tk.Tk, config: dict):
        self.root = root
        self.config = config
        self.game: GameProcess | None = None
        self.session: Session | None = None
        self.model: ModelClient | None = None
        self.messages = queue.Queue()
        self.busy = False
        self.autoplay = False
        self.cancel = threading.Event()
        self.next_step = 0.0
        self.state: dict = {}
        self.closed = False
        root.title("Fincard · AI试玩控制台")
        root.geometry("820x900")
        root.minsize(700, 660)
        root.protocol("WM_DELETE_WINDOW", self.close)
        settings = ttk.LabelFrame(root, text="本地设置（密钥不保存）", padding=10)
        settings.pack(fill="x", padx=10, pady=8)
        self.fields: dict[str, tk.StringVar] = {}
        for row, (key, label) in enumerate([
            ("game_path", "游戏源码路径"), ("godot_path", "Godot路径"),
            ("base_url", "API Base URL"), ("model", "模型名"),
        ]):
            self.fields[key] = tk.StringVar(value=str(config.get(key, "")))
            ttk.Label(settings, text=label).grid(row=row, column=0, sticky="w", pady=3)
            ttk.Entry(settings, textvariable=self.fields[key]).grid(row=row, column=1, sticky="ew", padx=6)
        self.key_var = tk.StringVar()
        ttk.Label(settings, text="API Key（仅本次）").grid(row=4, column=0, sticky="w")
        ttk.Entry(settings, textvariable=self.key_var, show="*").grid(row=4, column=1, sticky="ew", padx=6)
        self.fields["scenario"] = tk.StringVar(value=config.get("scenario", "example_country"))
        ttk.Label(settings, text="国家").grid(row=5, column=0, sticky="w")
        ttk.Combobox(settings, textvariable=self.fields["scenario"], values=["example_country", "arcadia"], state="readonly").grid(row=5, column=1, sticky="ew", padx=6)
        self.fields["step_delay"] = tk.StringVar(value=str(config.get("step_delay", 2.0)))
        ttk.Label(settings, text="每步展示秒数").grid(row=6, column=0, sticky="w")
        ttk.Entry(settings, textvariable=self.fields["step_delay"]).grid(row=6, column=1, sticky="ew", padx=6)
        self.fields["max_decisions"] = tk.StringVar(value=str(config.get("max_decisions", 250)))
        ttk.Label(settings, text="本局AI调用上限").grid(row=7, column=0, sticky="w")
        ttk.Entry(settings, textvariable=self.fields["max_decisions"]).grid(row=7, column=1, sticky="ew", padx=6)
        self.vision = tk.BooleanVar(value=config.get("send_screenshots", False))
        ttk.Checkbutton(settings, text="发送游戏截图给模型（需视觉模型，会增加费用）", variable=self.vision).grid(row=8, column=0, columnspan=2, sticky="w", pady=5)
        compatibility = ttk.Frame(settings)
        compatibility.grid(row=9, column=0, columnspan=2, sticky="ew", pady=4)
        ttk.Button(compatibility, text="使用DeepSeek Flash预设", command=self.apply_deepseek).pack(side="left")
        self.fields["provider"] = tk.StringVar(value=config.get("provider", "auto"))
        ttk.Combobox(compatibility, textvariable=self.fields["provider"], values=["auto", "deepseek"], state="readonly", width=9).pack(side="left", padx=5)
        ttk.Label(compatibility, text="  DeepSeek思考模式：").pack(side="left")
        self.fields["thinking_mode"] = tk.StringVar(value=config.get("thinking_mode", "disabled"))
        ttk.Combobox(compatibility, textvariable=self.fields["thinking_mode"], values=["disabled", "low", "high", "max"], state="readonly", width=10).pack(side="left")
        self.fields["max_completion_tokens"] = tk.StringVar(value=str(config.get("max_completion_tokens", 1200)))
        ttk.Label(settings, text="输出token上限").grid(row=10, column=0, sticky="w")
        ttk.Entry(settings, textvariable=self.fields["max_completion_tokens"]).grid(row=10, column=1, sticky="ew", padx=6)
        save_bar = ttk.Frame(settings)
        save_bar.grid(row=11, column=0, columnspan=2, sticky="ew", pady=4)
        ttk.Button(save_bar, text="保存设置（不含密钥）", command=self.save).pack(side="left")
        ttk.Button(save_bar, text="测试模型接口（一次请求）", command=self.test_model).pack(side="left", padx=6)
        settings.columnconfigure(1, weight=1)
        bar = ttk.Frame(root)
        bar.pack(fill="x", padx=10)
        for label, command in [
            ("启动游戏", self.launch), ("开始 / 继续AI", self.start),
            ("暂停 / 人工接管", self.pause), ("AI单步", self.single),
            ("刷新局面", self.refresh), ("生成建议", self.report), ("打开记录", self.open_run),
        ]:
            ttk.Button(bar, text=label, command=command).pack(side="left", padx=2, pady=4)
        self.status = tk.StringVar(value="就绪：先启动游戏。默认不调用模型、不产生费用。")
        ttk.Label(root, textvariable=self.status, wraplength=780).pack(fill="x", padx=12, pady=6)
        self.summary = tk.StringVar(value="可以把游戏窗口和控制台并排放置，全程查看出牌、事件与奖励。")
        ttk.Label(root, textvariable=self.summary, wraplength=780).pack(fill="x", padx=12, pady=3)
        tabs = ttk.Notebook(root)
        tabs.pack(fill="both", expand=True, padx=10, pady=8)
        self.log = ScrolledText(tabs, wrap="word", font=("Microsoft YaHei UI", 10))
        self.inspector = ScrolledText(tabs, wrap="word", font=("Consolas", 10))
        tabs.add(self.log, text="AI选择理由 / 过程发现")
        tabs.add(self.inspector, text="玩家可见局面 / 合法动作")
        self._log("首次使用请填写模型和密钥。也可用配置指定的环境变量提供密钥。\n暂停后可直接操作游戏窗口；继续时会重新读取局面，不会执行旧决策。")
        root.after(100, self.tick)

    def read_settings(self) -> dict:
        config = dict(self.config)
        config.update({key: variable.get().strip() for key, variable in self.fields.items()})
        try:
            config["step_delay"] = float(config["step_delay"])
            config["max_decisions"] = int(config["max_decisions"])
            config["max_completion_tokens"] = int(config["max_completion_tokens"])
            if not 0.3 <= config["step_delay"] <= 60 or not 1 <= config["max_decisions"] <= 5000:
                raise ValueError()
            if not 1 <= config["max_completion_tokens"] <= 393216:
                raise ValueError()
        except ValueError:
            raise SafeError("展示时间应在0.3～60秒，调用上限应在1～5000之间，token上限应为1～393216的整数。") from None
        config["game_path"] = str(Path(config["game_path"]).expanduser().resolve())
        config["send_screenshots"] = self.vision.get()
        return config

    def apply_deepseek(self):
        if self.busy:
            messagebox.showinfo("兼容预设", "请先暂停，等待当前请求完成后再切换。")
            return
        self.autoplay = False
        self.config = deepseek_preset(self.config)
        for key in ("provider", "base_url", "model", "thinking_mode", "max_completion_tokens"):
            self.fields[key].set(str(self.config[key]))
        self.consented = False
        self.status.set("已设置DeepSeek Flash：请填写DeepSeek Key，先点击“测试模型接口”。")
        self._log("DeepSeek预设：官方地址、deepseek-flash、max_tokens；默认关闭思考以避免1200 token只够输出推理。若启用low/high/max，请把输出token上限提高至至少8192。密钥仍仅保存在本地内存。")

    def _configure_model(self):
        config = self.read_settings()
        model = ModelClient(config, self.key_var.get().strip())
        if self.model is not None:
            model.usage = self.model.usage
            model.request_count = self.model.request_count
        self.config = config
        self.model = model

    def test_model(self):
        if self.busy:
            return
        self.autoplay = False
        try:
            self._configure_model()
            if self.model.request_count >= int(self.config["max_decisions"]):
                raise SafeError("已达到调用上限，请提高上限后再测试。")
        except SafeError as error:
            messagebox.showerror("模型设置", str(error))
            return
        if not messagebox.askokcancel("测试模型接口", "将向当前模型发送一次极小的决策请求，可能产生少量费用。不会操作游戏，不会上传截图。是否继续？"):
            return
        self.status.set("正在测试模型的JSON决策接口……")

        def work():
            result = self.model.decide({"screen": "connection_test", "legal_actions": [{"kind": "end_turn"}]}, [])
            self.messages.put(("log", "模型接口测试通过：返回了合法JSON决策。此动作未发送给游戏。\n" + result["reason"]))
            self.messages.put(("status", "模型接口兼容检查通过，可以启动游戏并开始AI试玩。"))

        self._worker(work)

    def _log(self, text: str):
        self.log.insert("end", text + "\n\n")
        self.log.see("end")

    def _worker(self, operation):
        if self.busy or self.closed:
            return
        self.busy = True

        def run():
            try:
                operation()
            except Cancelled:
                self.messages.put(("status", "已暂停。未执行的AI决策已丢弃，可人工操作游戏。"))
            except SafeError as error:
                self.messages.put(("error", str(error)))
            except Exception as error:
                self.messages.put(("error", "操作失败（" + type(error).__name__ + "），已暂停。详见本地游戏日志。"))
            finally:
                self.messages.put(("done", None))

        threading.Thread(target=run, daemon=True).start()

    def save(self):
        try:
            config = self.read_settings()
            (ROOT / "config.local.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            self.config = config
            self._log("设置已保存到config.local.json（不含密钥，Git忽略此文件）。")
        except SafeError as error:
            messagebox.showerror("设置", str(error))

    def launch(self):
        if self.busy or self.game:
            return
        try:
            config = self.read_settings()
        except SafeError as error:
            messagebox.showerror("设置", str(error))
            return
        self.config = config
        self.status.set("正在启动真实游戏窗口……")

        def work():
            self.session = Session(config)
            self.game = GameProcess(config, self.session.directory)
            try:
                bridge = self.game.launch()
                observation = bridge.observe()
                action = {"kind": "start_run", "scenario": config["scenario"]}
                after = bridge.act(observation, action)
                decision = {"action": action, "reason": "用户选择国家，进入正常开局。", "feedback": ""}
                self.session.record(observation, decision, after, "launcher")
                if config.get("save_screenshots", True):
                    self.session.capture(bridge.screenshot(), 0)
                self.messages.put(("state", after))
                self.messages.put(("status", "游戏已启动，AI暂停。点击“开始 / 继续AI”才会调用模型。"))
                self.messages.put(("log", "对局目录：" + str(self.session.directory)))
            except Exception:
                self.game.close()
                self.game = None
                raise

        self._worker(work)

    def _prepare_model(self) -> bool:
        if not self.game or not self.game.bridge or not self.session:
            messagebox.showinfo("AI试玩", "请先启动游戏。")
            return False
        if self.busy:
            return False
        try:
            self._configure_model()
        except SafeError as error:
            messagebox.showerror("模型设置", str(error))
            return False
        signature = (self.model.endpoint, self.model.model, self.model.thinking_mode, self.config["send_screenshots"])
        if not getattr(self, "consented", False) or getattr(self, "consent_signature", signature) != signature:
            kind = "局面信息及游戏截图" if self.config["send_screenshots"] else "玩家可见局面信息（不含截图）"
            if not messagebox.askokcancel("确认模型调用", f"将发送{kind}到你配置的模型服务。\n每个决策一次请求，可能产生费用；不自动重试。\n截图和记录仅保存在本地，不自动上传GitHub。\n\n是否继续？"):
                return False
            self.consented = True
        self.consent_signature = signature
        return True

    def start(self):
        if self._prepare_model():
            self.cancel.clear()
            self.autoplay = True
            self.status.set("AI试玩中。可随时暂停；不要同时在游戏中点击操作。")

    def pause(self):
        self.autoplay = False
        self.cancel.set()
        self.status.set("已请求暂停；若模型请求仍在进行，将丢弃返回决策，不再执行。待当前已提交的动画结束后可人工接管。")

    def single(self):
        if self._prepare_model():
            self.autoplay = False
            self.cancel.clear()
            self._step()

    def _step(self):
        config = dict(self.config)

        def work():
            bridge = self.game.bridge
            observation = bridge.observe()
            self.session.sync_run(observation)
            self.messages.put(("state", observation))
            if observation.get("run_status") in (1, 2):
                self.messages.put(("pause", "本局已结束。可生成建议，或直接查看本地summary.md。"))
                return
            if not observation.get("legal_actions"):
                self.messages.put(("pause", "当前没有可执行动作；可能在动画中，请刷新后再继续。"))
                return
            if self.model.request_count >= int(config["max_decisions"]):
                self.messages.put(("pause", "已达到本局调用上限，AI暂停。可人工继续。"))
                return
            png = bridge.screenshot() if config.get("send_screenshots") else None
            self.messages.put(("status", "AI正在思考……暂停可阻止返回的决策被执行。"))
            try:
                decision = self.model.decide(observation, self.session.history, png)
            except DecisionError as error:
                path = self.session.save_diagnostic(error.diagnostic)
                raise SafeError(str(error) + "\n本地脱敏诊断：" + str(path)) from None
            finally:
                self.session.summary(self.model.usage)
                (self.session.directory / "request-count.json").write_text(json.dumps({"requests_sent": self.model.request_count}), encoding="utf-8")
            if self.cancel.is_set():
                raise Cancelled()
            self.messages.put(("log", "AI计划：" + json.dumps(decision["action"], ensure_ascii=False) + "\n理由：" + decision["reason"] + ("\n发现：" + decision["feedback"] if decision["feedback"] else "")))
            if self.cancel.wait(float(config["step_delay"])):
                raise Cancelled()
            current = bridge.observe()
            if current["revision"] != observation["revision"]:
                self.messages.put(("pause", "检测到人工操作或局面变化，旧决策已丢弃；请继续AI以重新思考。"))
                return
            if self.cancel.is_set():
                raise Cancelled()
            after = bridge.act(observation, decision["action"])
            self.session.record(observation, decision, after, "model")
            self.session.summary(self.model.usage)
            self.messages.put(("log", f"已执行成功：{decision['action']['kind']}；预算 {after.get('budget', '—')}，政治点 {after.get('political_capital', '—')}。"))
            if config.get("save_screenshots", True):
                self.session.capture(bridge.screenshot(), len(self.session.decisions))
            self.messages.put(("state", after))
            self.messages.put(("status", f"已执行{len(self.session.decisions)}步；模型调用{self.model.request_count}次。"))

        self._worker(work)

    def refresh(self):
        if self.game and not self.busy:
            def work():
                state = self.game.bridge.observe()
                self.session.last_state = state
                self.session.summary(self.model.usage if self.model else None)
                self.messages.put(("state", state))
            self._worker(work)

    def report(self):
        self.autoplay = False
        if not self._prepare_model():
            return
        if not messagebox.askokcancel("生成建议", "会额外调用一次模型生成报告，可能产生费用。是否继续？"):
            return
        if self.model.request_count >= int(self.config["max_decisions"]):
            messagebox.showinfo("调用上限", "已达到调用上限；可查看无需模型的summary.md，或手动提高上限。")
            return

        def work():
            state = self.game.bridge.observe()
            report = self.model.report(state, self.session.decisions)
            (self.session.directory / "ai-report.md").write_text(report, encoding="utf-8")
            self.session.summary(self.model.usage)
            self.messages.put(("log", report))
            self.messages.put(("status", "建议已保存到本次对局的ai-report.md。"))

        self._worker(work)

    def open_run(self):
        if self.session:
            if os.name == "nt":
                os.startfile(self.session.directory)
            else:
                messagebox.showinfo("记录目录", str(self.session.directory))

    def tick(self):
        if self.closed:
            return
        try:
            while True:
                kind, payload = self.messages.get_nowait()
                if kind == "done":
                    self.busy = False
                    self.next_step = time.monotonic() + 0.3
                elif kind == "log":
                    self._log(payload)
                elif kind == "state":
                    self.state = payload
                    self.summary.set(f"财年{payload.get('year', '—')} / 总回合{payload.get('turn', '—')} | 预算{payload.get('budget', '—')} | 政治点{payload.get('political_capital', '—')} | 当前：{payload.get('decision', '动画或终局')}")
                    self.inspector.delete("1.0", "end")
                    self.inspector.insert("end", json.dumps(payload, ensure_ascii=False, indent=2))
                elif kind in {"error", "pause"}:
                    self.autoplay = False
                    self.status.set(payload)
                    self._log(payload)
                elif kind == "status":
                    self.status.set(payload)
        except queue.Empty:
            pass
        if self.autoplay and not self.busy and time.monotonic() >= self.next_step:
            self._step()
        self.root.after(100, self.tick)

    def close(self):
        if not messagebox.askokcancel("退出试玩器", "关闭控制台及它启动的游戏窗口？当前游戏本身不支持存档，对局日志会保留。"):
            return
        self.closed = True
        self.autoplay = False
        self.cancel.set()
        # Only terminate the child we launched; never touch an unrelated game.
        if self.game and self.game.process and self.game.process.poll() is None:
            self.game.process.terminate()
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    try:
        config = load_config(args.config)
    except (OSError, ValueError):
        raise SystemExit("无法读取配置文件，请检查JSON格式。") from None
    root = tk.Tk()
    Dashboard(root, config)
    root.mainloop()


if __name__ == "__main__":
    main()
