"""
Agentic RAG — ReAct パターン実装 (PHASE 10)

【JSON 不要設計】
ローカルモデルは JSON を安定して出力できないため、
"THOUGHT: / ACTION: / INPUT:" の行形式を使用。
正規表現でパース可能で、出力の揺れに強い。
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List

MAX_STEPS = 5

SYSTEM_PROMPT = {
    "ja": """あなたはデータガバナンスの専門家AIエージェントです。
ユーザーの質問を段階的に解決してください。
各ステップで必ず以下の形式で出力してください:

THOUGHT: [次に何をすべきか]
ACTION: [search または final_answer]
INPUT: [検索クエリまたは最終回答]

例:
THOUGHT: replicationのRPO設定について検索する必要がある
ACTION: search
INPUT: replication RPO 設定""",
    "en": """You are an expert data governance AI agent.
Solve user questions step by step.
At each step, output EXACTLY this format:

THOUGHT: [what to do next]
ACTION: [search or final_answer]
INPUT: [search query or final answer]

Example:
THOUGHT: I need to search for replication RPO configuration
ACTION: search
INPUT: replication RPO configuration""",
}


def _parse_action(text: str) -> Dict[str, str]:
    """THOUGHT/ACTION/INPUT 形式をパース。失敗時は final_answer として扱う。"""
    if not text:
        return {"thought": "", "action": "final_answer", "input": ""}
    thought = re.search(r"THOUGHT:\s*(.+?)(?=\bACTION:|$)", text, re.S)
    action = re.search(r"ACTION:\s*(\S+)", text)
    inp = re.search(r"INPUT:\s*(.+?)$", text, re.S)
    return {
        "thought": thought.group(1).strip() if thought else "",
        "action": action.group(1).strip().lower().rstrip(",.;") if action else "final_answer",
        "input": inp.group(1).strip() if inp else text.strip(),
    }


class CynovelaAgent:
    """ReAct ループで RAG 検索を繰り返しながら回答するエージェント"""

    def __init__(
        self,
        llm_client,
        rag_search_fn: Callable,
        collection_ids: List[str],
        lang: str = "ja",
    ) -> None:
        self.llm = llm_client
        self.rag_search = rag_search_fn
        self.collection_ids = collection_ids
        self.lang = lang
        self.steps: List[Dict] = []

    def run(self, user_query: str) -> Dict[str, Any]:
        sys_prompt = SYSTEM_PROMPT.get(self.lang, SYSTEM_PROMPT["en"])
        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_query},
        ]

        for step_num in range(MAX_STEPS):
            try:
                response = self.llm.chat(
                    messages=messages,
                    max_tokens=400,
                    temperature=0.1,
                )
                content = (response or {}).get("content", "").strip()
            except Exception as e:
                return {
                    "answer": f"LLM error: {e}",
                    "steps": self.steps,
                    "sources": [],
                }

            parsed = _parse_action(content)
            self.steps.append({"step": step_num, **parsed})

            if parsed["action"] == "final_answer":
                return {
                    "answer": parsed["input"],
                    "steps": self.steps,
                    "sources": [s["input"] for s in self.steps if s["action"] == "search"],
                }

            if parsed["action"] == "search":
                results: List[Dict] = []
                for col_id in self.collection_ids:
                    try:
                        r = self.rag_search(
                            collection_id=col_id,
                            query=parsed["input"],
                            n_results=3,
                        )
                        if isinstance(r, list):
                            results.extend(r)
                    except Exception:
                        pass
                observation = "\n\n".join(f"[{i + 1}] {(r.get('text') or '')[:400]}" for i, r in enumerate(results[:5]))
                if self.lang == "ja":
                    label = "検索結果:"
                    next_q = "次のステップを実行してください。"
                else:
                    label = "Search results:"
                    next_q = "Proceed to next step."
                messages.append({"role": "assistant", "content": content})
                messages.append(
                    {
                        "role": "user",
                        "content": f"{label}\n{observation}\n\n{next_q}",
                    }
                )
                continue

            # 未知のアクション → そのまま回答として返す
            return {
                "answer": parsed["input"],
                "steps": self.steps,
                "sources": [],
            }

        # MAX_STEPS 到達
        last = self.steps[-1] if self.steps else {}
        return {
            "answer": last.get("thought", "Reached max steps without final answer"),
            "steps": self.steps,
            "sources": [],
        }
