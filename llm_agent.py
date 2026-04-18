"""
llm_agent.py
============
LLM Agent — RAG + Function Calling cho hệ thống giao dịch.

QUAN TRỌNG (LLM_ADVICE_ONLY = True):
  LLM CHỈ được THAM VẤN và ĐỀ XUẤT.
  LLM KHÔNG được TỰ ĐẶT LỆNH.
  Mọi hành động thực thi phải qua DecisionEngine với approval của user.

Chức năng:
  - analyze_trade(trade_record) → giải thích quyết định win/loss
  - suggest_improvements(stats) → đề xuất cải thiện chiến lược
  - rag_query(question) → trả lời câu hỏi dựa trên lịch sử giao dịch

Function Calling (chỉ READ/SIMULATE, không EXECUTE):
  - get_stats      → lấy thống kê giao dịch
  - get_balance    → xem số dư (read only)
  - run_simulation → chạy backtest (read only)
  - fetch_pattern  → tìm kiếm patterns trong VectorDB
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

import config


# ──────────────────────────────────────────────────────────────────
# LLM Client (OpenAI-compatible)
# ──────────────────────────────────────────────────────────────────

class LLMClient:
    """Thin wrapper around OpenAI-compatible chat completions API."""

    def __init__(self) -> None:
        self._client = None
        if config.LLM_ENABLED and config.LLM_API_KEY:
            try:
                from openai import OpenAI
                self._client = OpenAI(
                    api_key  = config.LLM_API_KEY,
                    base_url = config.LLM_BASE_URL,
                )
                print(f"[LLM] Connected: {config.LLM_BASE_URL} model={config.LLM_MODEL}")
            except ImportError:
                print("[LLM] openai package not installed — LLM disabled")
            except Exception as exc:
                print(f"[LLM] Connection failed: {exc}")

    def is_available(self) -> bool:
        return self._client is not None and config.LLM_ENABLED

    def chat(
        self,
        messages:  list[dict],
        tools:     Optional[list[dict]] = None,
        max_tokens:int = None,
    ) -> dict:
        """Send chat completion. Returns response dict or empty dict on failure."""
        if not self.is_available():
            return {}
        try:
            kwargs = {
                "model"      : config.LLM_MODEL,
                "messages"   : messages,
                "max_tokens" : max_tokens or config.LLM_MAX_TOKENS,
                "temperature": 0.3,
            }
            if tools:
                kwargs["tools"] = tools
            resp = self._client.chat.completions.create(**kwargs)
            msg  = resp.choices[0].message
            return {
                "content"     : msg.content or "",
                "tool_calls"  : [
                    {
                        "id"      : tc.id,
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in (msg.tool_calls or [])
                ],
            }
        except Exception as exc:
            print(f"[LLM] API error: {exc}")
            return {}


# ──────────────────────────────────────────────────────────────────
# Tool Definitions (function calling)
# ──────────────────────────────────────────────────────────────────

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_trading_stats",
            "description": "Lấy thống kê giao dịch hiện tại (read-only)",
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {"type": "string", "enum": ["today", "week", "all"], "description": "Khoảng thời gian"}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_patterns",
            "description": "Tìm kiếm patterns và insights từ lịch sử giao dịch",
            "parameters": {
                "type": "object",
                "properties": {
                    "query":    {"type": "string", "description": "Câu hỏi hoặc từ khóa tìm kiếm"},
                    "doc_type": {"type": "string", "enum": ["trade_log", "insight", "pattern", "error"], "description": "Loại document"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_system_status",
            "description": "Xem trạng thái hiện tại của hệ thống (mode, balance, control settings)",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


# ──────────────────────────────────────────────────────────────────
# LLM Agent
# ──────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Bạn là AI Analyst cho hệ thống giao dịch Binary Options tự động.

QUYỀN HẠN CỦA BẠN:
  ✅ Phân tích dữ liệu giao dịch
  ✅ Đề xuất cải thiện chiến lược
  ✅ Trả lời câu hỏi về hiệu suất
  ✅ Gọi tool để đọc dữ liệu (read-only)
  ❌ KHÔNG được đặt lệnh giao dịch
  ❌ KHÔNG được thay đổi cấu hình tự động
  ❌ KHÔNG được stop/start engine

Khi đề xuất, hãy:
  - Cụ thể và có số liệu
  - Chỉ ra nguyên nhân dựa trên dữ liệu
  - Đề xuất thay đổi nhỏ, có thể đo lường

Ngôn ngữ: tiếng Việt."""


class LLMAgent:
    """
    AI agent với function calling, gated và advice-only.

    Usage:
        agent = LLMAgent(logger=trade_logger, vector_store=vs, control=ctrl)
        answer = agent.ask("Tại sao hôm nay thua nhiều?")
        advice = agent.analyze_trade(trade_record)
        tips   = agent.suggest_improvements()
    """

    def __init__(self, logger=None, vector_store=None, control=None) -> None:
        self._llm     = LLMClient()
        self._vs      = vector_store   # VectorStore instance (optional)
        self._logger  = logger         # TradeLogger instance (optional)
        self._control = control        # ControlSystem instance (optional)
        self._audit: list[dict] = []   # In-memory audit log

    # ── Tool execution ────────────────────────────────────────────

    def _execute_tool(self, name: str, args: dict) -> Any:
        """Execute a tool call (all read-only)."""
        if name == "get_trading_stats":
            if self._logger:
                return self._logger.get_stats()
            return {"message": "Logger not available"}

        if name == "search_patterns":
            if self._vs:
                query    = args.get("query", "")
                doc_type = args.get("doc_type")
                results  = self._vs.search(query, top_k=5, doc_type=doc_type)
                return [{"content": doc.content, "score": round(score, 3)}
                        for doc, score in results]
            return []

        if name == "get_system_status":
            result: dict = {"timestamp": datetime.now().isoformat()}
            if self._control:
                result["control"] = self._control.status()
            return result

        return {"error": f"Unknown tool: {name}"}

    # ── RAG context builder ───────────────────────────────────────

    def _build_rag_context(self, query: str) -> str:
        if not self._vs:
            return ""
        results = self._vs.search(query, top_k=config.LLM_RAG_TOP_K)
        if not results:
            return ""
        lines = ["Relevant knowledge from trade history:"]
        for doc, score in results:
            lines.append(f"  [{score:.2f}] {doc.content[:200]}")
        return "\n".join(lines)

    # ── Audit logging ─────────────────────────────────────────────

    def _audit_log(self, action: str, query: str, response: str) -> None:
        self._audit.append({
            "timestamp": datetime.now().isoformat(),
            "action"   : action,
            "query"    : query[:200],
            "response" : response[:500],
        })
        # Keep last 200 entries
        if len(self._audit) > 200:
            self._audit = self._audit[-200:]

    # ── Public API ────────────────────────────────────────────────

    def ask(self, question: str) -> str:
        """
        Answer a question using RAG + function calling.
        Returns text response or empty string if LLM unavailable.
        """
        if not self._llm.is_available():
            return "[LLM] Không khả dụng — cần bật LLM_ENABLED và cung cấp LLM_API_KEY"

        rag = self._build_rag_context(question)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
        ]
        if rag:
            messages.append({"role": "system", "content": rag})
        messages.append({"role": "user", "content": question})

        # First LLM call — may request tools
        resp = self._llm.chat(messages, tools=_TOOLS)
        if not resp:
            return "[LLM] Không có phản hồi"

        # Handle tool calls (max 3 iterations)
        for _ in range(3):
            tool_calls = resp.get("tool_calls", [])
            if not tool_calls:
                break
            messages.append({"role": "assistant", "content": resp.get("content", ""), "tool_calls": tool_calls})
            for tc in tool_calls:
                try:
                    args   = json.loads(tc["function"]["arguments"])
                    result = self._execute_tool(tc["function"]["name"], args)
                except Exception as exc:
                    result = {"error": str(exc)}
                messages.append({
                    "role"        : "tool",
                    "tool_call_id": tc["id"],
                    "content"     : json.dumps(result, ensure_ascii=False),
                })
            resp = self._llm.chat(messages)
            if not resp:
                break

        answer = resp.get("content", "") if resp else ""
        self._audit_log("ask", question, answer)
        return answer

    def analyze_trade(self, trade_record: dict) -> str:
        """Analyze a specific trade win/loss."""
        won  = trade_record.get("won", False)
        sym  = trade_record.get("symbol", "")
        dir_ = trade_record.get("direction", "")
        pnl  = trade_record.get("pnl", 0)
        q = (
            f"Phân tích lệnh {'THẮNG' if won else 'THUA'}: "
            f"{sym} {dir_} PnL={pnl:+.2f} USD. "
            f"Điểm kỹ thuật: {json.dumps(trade_record, ensure_ascii=False)[:300]}. "
            f"Đây là gì? Tại sao? Rút ra bài học gì?"
        )
        # Add to vector store for future RAG
        if self._vs:
            self._vs.add_trade_log(trade_record)
        return self.ask(q)

    def suggest_improvements(self) -> str:
        """Generate strategy improvement suggestions based on current stats."""
        stats_str = ""
        if self._logger:
            stats = self._logger.get_stats()
            stats_str = json.dumps(stats, ensure_ascii=False)
        q = (
            f"Dựa trên dữ liệu giao dịch hiện tại: {stats_str}. "
            f"Hãy đề xuất 3 cải thiện cụ thể và có thể đo lường "
            f"để nâng cao tỉ lệ thắng và giảm rủi ro."
        )
        return self.ask(q)

    def get_audit_log(self) -> list[dict]:
        """Return LLM audit log."""
        return list(self._audit)


if __name__ == "__main__":
    from vector_store import VectorStore
    vs = VectorStore()
    vs.add("Strong win pattern at F618 with RSI oversold bounce", "pattern")

    agent = LLMAgent(vector_store=vs)
    print(agent.ask("Có pattern nào tốt cho lệnh CALL không?"))
