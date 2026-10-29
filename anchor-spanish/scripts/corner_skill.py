"""skill 主体：intake 状态机 + 简报导出（不调用任何 LLM）。

语言身份（技能包名 / 语言键 / 默认配置名）全部由 `corner_config` 里的常量决定，
本模块不做任何语言分派，也不会去探测别的语言配置。

退出码（`python scripts/corner_skill.py [selftest]`）：
`0` 干净（演示跑完 / 自检全过）；`1` 自检报出不一致；`2` 用法错（只认 `selftest`）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from corner_config import SKILL_NAME, ConfigError, SkillConfig, load_config

__all__ = ["SkillSession", "init_skill"]

# ─────────────────────────── Skill 主体 ───────────────────────────


@dataclass
class SkillSession:
    """skill 主体运行时实例：持有配置 + 交互状态。"""

    config: SkillConfig
    answers: dict = field(default_factory=dict)

    # ---------- intake 流程 ----------

    def ask_next(self) -> list[dict]:
        """返回下一题的 AskUserQuestion 兼容负载；空列表表示 intake 完成。

        对 ask=false 的节点（auto_recommend），自动填值并继续取下一题。
        """
        nxt = self.config.next_question(self.answers)
        if nxt is None:
            return []
        # 自动产出节点：以 `ask is False` 触发（与 next_question 同一判据，
        # 不依赖 mode），由 auto_recommend_choices 依据 source 填值，不向用户提问。
        if nxt.ask is False:
            self.answers[nxt.key] = self.config.auto_recommend_choices(
                nxt, self.answers
            )
            # 自动填值完成后，重新取下一个（真正的待问问题）
            return self.ask_next()
        return self.config.build_ask_payload(self.answers)

    def submit(self, question_id: str, choices: list[str]) -> None:
        """回填用户选择。question_id 形如 'Q1' 或 'Q1#2'（分组），统一归并到父 key。

        跨分组累计校验：超长题目（如 Q4 拆 3 组）若仅校验单组，单组选满 + 后续组
        再选会累计越过 max_choices。本方法按「existing + choices」合并去重后校验
        投影长度，任一组合越界立刻拒绝。
        """
        parent = question_id.split("#", 1)[0] if "#" in question_id else question_id
        spec = next((q for q in self.config.question_plan if q.id == parent), None)
        if spec is None:
            raise ConfigError(f"未知的 question_id: {question_id}")
        existing = self.answers.get(spec.key, [])
        projected = list(dict.fromkeys(list(existing) + list(choices)))
        if spec.max_choices and len(projected) > spec.max_choices:
            raise ConfigError(
                f"{spec.title} 累计上限 {spec.max_choices} 个"
                f"（含已有 {len(existing)} 个 + 本次 {len(choices)} 个"
                f"去重后 {len(projected)} 个）；请减少本次选择。"
            )
        # 支持「随机选一个」的题目：哨兵 -> 题库随机话题，id -> 可读标签
        if spec.allow_random:
            projected = self.config.expand_choices(projected)
        self.answers[spec.key] = projected

    def is_intake_done(self) -> bool:
        return self.config.next_question(self.answers) is None

    # ---------- 导出简报（交给 skill 主体读取） ----------

    def preview_brief(self) -> str:
        """返回将写入简报的 Markdown 字符串（不落盘），供宿主检查。"""
        return self.config.build_markdown_brief(self.answers)

    def export_brief(self, path: str | None = None) -> str:
        """把已收集参数整理为 Markdown 简报并落盘，返回文件路径。

        该文件交给 skill 主体读取并产出主持脚本，不依赖任何 LLM / openai ——
        「参数收集」与「内容生成」经此文件彻底解耦。

        `path=None` 时落盘到**当前工作目录**的 `meta.brief_filename`：技能包目录
        保持只读（它会被整体同步到 `~/.workbuddy/skills/`），运行产物不写回包内。
        """
        if not self.is_intake_done():
            raise ConfigError(
                "intake 未完成，不能导出；先 ask_next()/submit() 收集全部答案。"
            )
        md = self.preview_brief()
        if path is None:
            brief_name = self.config.meta.get(
                "brief_filename",
                f"{self.config.meta.get('skill_name', 'corner')}-brief.md",
            )
            out_path = Path.cwd() / brief_name
        else:
            out_path = Path(path)
        out_path.write_text(md, encoding="utf-8")
        return str(out_path)


# ─────────────────────────── 便捷入口 ───────────────────────────


def init_skill(*, config_path: str | None = None) -> SkillSession:
    """初始化 skill 主体：加载本包配置 -> 构造会话。

    - `config_path=None`（默认）：读取本包资产目录下的 `DEFAULT_CONFIG_NAME`
      ——本包只服务一种语言，不接受语言参数，也不会去探测别的语言配置；
    - `config_path`：可选，显式指定配置文件路径（如测试或多套配置场景）。

    单一入口；不依赖任何 LLM，intake 完成后由 export_brief() 导出 Markdown 简报，
    交给 skill 主体读取并产出主持脚本。
    """
    return SkillSession(config=load_config(config_path))


# ─────────────────────── 自检工具（供 __main__ 使用） ───────────────────────


def _check(cond: object, message: str) -> None:
    """自检断言。不用 `assert` —— `python -O` 会把 `assert` 整条删掉，
    自检会照旧打印「全部通过 ✓」并退出 0，实际一条都没验。"""
    if not cond:
        print(f"  ✗ {message}")
        raise SystemExit(1)


if __name__ == "__main__":
    # 用法：
    #   python scripts/corner_skill.py            # 演示：初始化 -> intake -> 简报
    #   python scripts/corner_skill.py selftest   # 自检：auto_recommend 配对 + 公共 API
    import sys

    argv = sys.argv[1:]
    if argv and argv[0] != "selftest":
        print(f"用法: python scripts/corner_skill.py [selftest]（收到: {argv[0]!r}）")
        raise SystemExit(2)
    session = init_skill()

    def drive(s: SkillSession, *, verbose: bool = False) -> None:
        """模拟用户走完 intake：按 max_choices 限流，使累计不超限。

        自检与演示共用同一份循环 —— 两份写法会各自漂移（改了一处忘了另一处）。
        `verbose=True` 逐题打印取到的分组与选择（演示用）；自检只要最终答案。
        """
        while True:
            payload = s.ask_next()  # 一次返回当前问题的所有分组(chunk)
            if not payload:
                break
            first = payload[0]
            if verbose:
                print(f"· 提问 {first['id']} ({first['header']}): {first['question']}")
            spec = next(
                q
                for q in s.config.question_plan
                if q.id == first["id"].split("#", 1)[0]
            )
            budget = spec.max_choices or 1
            already = len(s.answers.get(spec.key, []))
            for chunk in payload:
                chosen = [chunk["options"][0]["value"]]
                if already >= budget:
                    if verbose:
                        print(
                            f"    · 分组 {chunk['id']} → 跳过（已达 "
                            f"{spec.key} 上限 {budget}）"
                        )
                    continue
                if verbose:
                    print(f"    · 分组 {chunk['id']} → 模拟选择: {chosen}")
                s.submit(chunk["id"], chosen)
                already += 1

    # 自检模式：验证 auto_recommend + ask=false 配对 与 topic_options 公共 API
    if argv:
        import dataclasses as _dc

        print("=== auto_recommend 配对自检 ===")

        base_plan = session.config.question_plan

        # 1) topic 源（Q4 topics）→ auto_recommend
        plan_a = tuple(
            _dc.replace(q, ask=False, mode="auto_recommend") if q.key == "topics" else q
            for q in base_plan
        )
        s_a = SkillSession(_dc.replace(session.config, question_plan=plan_a))
        drive(s_a)
        _check(s_a.is_intake_done(), "intake 未完成")
        _check(s_a.answers.get("topics"), "auto_recommend 未自动填值 topics")
        print("  ✓ topic 源 auto_recommend：topics =", s_a.answers["topics"])

        # 2) 非 topic 源（level）→ auto_recommend，验证走 _resolve_options 前 N 分支
        plan_b = tuple(
            _dc.replace(q, ask=False, mode="auto_recommend") if q.key == "level" else q
            for q in base_plan
        )
        s_b = SkillSession(_dc.replace(session.config, question_plan=plan_b))
        drive(s_b)
        _check(s_b.answers.get("level"), "auto_recommend 未自动填值 level")
        print("  ✓ 非 topic 源 auto_recommend：level =", s_b.answers["level"])

        # 3) topic_options() 公共 API 可用（assets 保留了 topic_dimensions 数据）
        _check(session.config.topic_options(), "topic_options() 返回空")
        print(
            "  ✓ topic_options() 公共 API：",
            [o.label for o in session.config.topic_options()],
        )

        print("auto_recommend 配对自检全部通过 ✓")
        raise SystemExit(0)

    print(f"=== intake 流程（{SKILL_NAME}） ===")
    drive(session, verbose=True)

    print("\n=== 是否收集完成 ===", session.is_intake_done())
    print("\n=== 导出 Markdown 简报 ===")
    brief_path = session.export_brief()
    print("已写入:", brief_path)
