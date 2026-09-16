from typing import Any
import json
import logging
import os
import re
import httpx
import time
from contextvars import ContextVar
from datetime import datetime
from decimal import Decimal
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import get_settings
from app.db.models.restaurant import User
import dotenv
from langchain_core.chat_history import InMemoryChatMessageHistory
from app.db.models.restaurant import Customer, Order
from langchain_core.stores import InMemoryStore as LangChainStore
from app.db.models.restaurant import Customer
dotenv.load_dotenv()

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, before_model
# from app.prompt import (
#     ADDRESS_EXTRACTION_SYSTEM,
#     address_extraction_prompt,
#     address_normalization_prompt,
#     add_item_extraction_prompt,
#     add_item_sql_prompt,
#     attach_contact_prompt,
#     ADDRESS_NORMALIZATION_SYSTEM,
#     CONTACT_EXTRACTION_SYSTEM,
#     DATABASE_ASSISTANT_SYSTEM,
#     DATABASE_CONFIRMATION_SYSTEM,
#     DATABASE_SUMMARY_SYSTEM,
#     DB_SCHEMA_PROMPT,
#     confirm_order_sql_prompt,
#     complete_order_prompt,
#     contact_details_prompt,
#     contact_follow_up_prompt,
#     create_order_sql_prompt,
#     ETA_SYSTEM,
#     eta_prompt,
#     INTENT_CLASSIFIER_SYSTEM,
#     intent_classification_prompt,
#     ITEM_EXTRACTION_SYSTEM,
#     latest_active_order_prompt,
#     ORDER_EXTRACTION_SYSTEM,
#     order_items_query_prompt,
#     order_status_prompt,
#     order_total_prompt,
#     PRECISE_INTENT_SYSTEM,
#     result_summary_prompt,
#     save_destination_sql_prompt,
#     sql_fix_prompt,
#     error_synthesis_prompt,
#     authenticated_order_extraction_prompt,
#     authenticated_user_context,
#     database_chat_messages,
#     guest_order_extraction_prompt,
#     order_confirmation_prompt,
# )

from langfuse import Langfuse
from langfuse.langchain import CallbackHandler

logger = logging.getLogger(__name__)


class RequestTiming:
    def __init__(self) -> None:
        self.started_at = time.perf_counter()
        self.llm_seconds = 0.0
        self.database_seconds = 0.0
        self.llm_calls = 0
        self.database_calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0

    def record(self, category: str, started_at: float, detail: str, usage: dict | None = None) -> None:
        elapsed = time.perf_counter() - started_at
        if category == "LLM":
            self.llm_seconds += elapsed
            self.llm_calls += 1
            if usage:
                self.prompt_tokens += usage.get("prompt_tokens", 0)
                self.completion_tokens += usage.get("completion_tokens", 0)
                self.total_tokens += usage.get("total_tokens", 0)
        elif category == "DATABASE":
            self.database_seconds += elapsed
            self.database_calls += 1
        logger.info("[TIMING] %s | %.2fs | %s", category, elapsed, detail)

    def finish(self, session_id: str) -> None:
        total_seconds = time.perf_counter() - self.started_at
        python_seconds = max(0.0, total_seconds - self.llm_seconds - self.database_seconds)
        llm_summary = f"{self.llm_seconds * 1000:.1f}ms ({self.llm_calls} calls)" if self.llm_calls else "NOT CALLED"
        database_summary = f"{self.database_seconds * 1000:.1f}ms ({self.database_calls} calls)" if self.database_calls else "NOT CALLED"
        logger.info(
            "[TIMING] SUMMARY | LLM: %s | DATABASE: %s | PYTHON: %.1fms | TOTAL: %.1fms | session=%s",
            llm_summary,
            database_summary,
            python_seconds * 1000,
            total_seconds * 1000,
            session_id,
        )


request_timing: ContextVar[RequestTiming | None] = ContextVar("request_timing", default=None)
_langfuse_clients: dict[str, Any] = {}


def _langfuse_client() -> Langfuse | None:
    """Return the configured Langfuse client used for tracing and prompts."""
    settings = get_settings()
    if not settings.langfuse_enabled or not settings.langfuse_public_key or not settings.langfuse_secret_key:
        return None
    try:
        if settings.langfuse_public_key not in _langfuse_clients:
            _langfuse_clients[settings.langfuse_public_key] = Langfuse(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                base_url=settings.langfuse_base_url,
            )
        return _langfuse_clients[settings.langfuse_public_key]
    except Exception:
        logger.exception("Langfuse client could not be initialized")
        return None


def get_langfuse_callback() -> Any | None:
    settings = get_settings()
    if not settings.langfuse_enabled or not settings.langfuse_public_key or not settings.langfuse_secret_key:
        return None

    try:

        _langfuse_client()
        return CallbackHandler(public_key=settings.langfuse_public_key)
    except Exception:
        logger.exception("Langfuse tracing could not be initialized")
        return None

def _clean_content(content: str | None) -> str:
    print(f"==>> _clean_content: called")
    if not content:
        return ""
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL | re.IGNORECASE)
    content = re.sub(r"<think>.*", "", content, flags=re.DOTALL | re.IGNORECASE)
    return content.strip()

_session_store = LangChainStore()
_session_orders: dict[str, int] = {}

def get_session_history(session_id: str) -> InMemoryChatMessageHistory:
    print(f"==>> get_session_history: {session_id}")
    existing = _session_store.mget([session_id])[0]
    if existing is None:
        history = InMemoryChatMessageHistory()
        _session_store.mset([(session_id, history)])
        return history
    return existing

def _parse_json_from_llm(raw: str) -> dict:
    if not raw:
        return {}
    clean = raw.strip()
    if "```" in clean:
        parts = clean.split("```")
        print(f"==>> parts: {parts}")
        clean = " ".join(p for i, p in enumerate(parts) if i % 2 == 1 or len(parts) == 1).strip()
    if clean.startswith("json"):
        clean = clean[4:].strip()
    s_idx = clean.find("{")
    e_idx = clean.rfind("}")
    if s_idx != -1 and e_idx != -1:
        clean = clean[s_idx:e_idx + 1]
    try:
        return json.loads(clean)
    except Exception:
        print(f"==>> Failed to parse JSON: {clean}")
        return {}

def _session_order_id(history: InMemoryChatMessageHistory) -> int | None:
    print(f"==>> _session_order_id: called")
    """Return the most recently created order number acknowledged in this session."""
    for message in reversed(history.messages):
        if message.type != "ai":
            continue
        content = str(message.content)
        idx = content.find("#")
        while idx != -1:
            num = ""
            i = idx + 1
            while i < len(content) and content[i].isdigit():
                num += content[i]
                i += 1
            if num:
                return int(num)
            idx = content.find("#", idx + 1)
    return None


class TopicGuardrailMiddleware(AgentMiddleware):
    """Custom Topic Guardrail Middleware for checking forbidden topics before processing chat requests."""

    def __init__(self, forbidden_topics: list[str] | None = None):
        super().__init__()
        self.forbidden_topics = forbidden_topics or [
            # Drugs
            "drugs",
            "illegal drugs",
            "controlled substances",
            "drug trafficking",
            "drug manufacturing",

            # Weapons
            "weapons",
            "firearms",
            "guns",
            "bombs",
            "explosives",
            "chemical weapons",
            "biological weapons",

            # Violence
            "violent attack",
            "mass shooting",
            "murder",
            "assassination",
            "terrorist attack",

            # Self-harm
            "suicide",
            "self harm",
            "self-harm",

            # Sexual content
            "pornography",
            "explicit sexual content",
            "sexual exploitation",
            "sexual abuse",

            # Child exploitation
            "child exploitation",
            "child sexual abuse",

            # Cyber abuse
            "malware",
            "ransomware",
            "phishing",
            "credential theft",
            "password cracking",
            "unauthorized access",
            "ddos attack",

            # Crime
            "fraud",
            "scam",
            "money laundering",
            "identity theft",
            "extortion",
            "kidnapping",
            "burglary",
            "theft",

            # Privacy abuse
            "doxxing",
            "stalking",
            "personal data theft",

            # Extremism
            "terrorism",
            "terrorist recruitment",
            "violent extremism",

            # Financial crimes
            "tax evasion",
            "financial fraud",
            "market manipulation",
            "counterfeiting",
        ]

    def before_model(self, user_message: str) -> bool:
        """Executes before model call. Returns True if ALLOWED, False if BLOCKED."""
        message_lower = user_message.lower()
        for topic in self.forbidden_topics:
            if topic in message_lower:
                logger.info("[MIDDLEWARE: GUARDRAIL] Blocked due to forbidden topic %r in message: %r", topic, user_message)
                return False
        return True

guard = TopicGuardrailMiddleware()

@before_model
def topic_guardrail(state, runtime=None):
    """LangChain before_model topic guardrail function."""
    messages = state.get("messages", []) if isinstance(state, dict) else getattr(state, "messages", [])
    if not messages:
        return None
    last_msg = messages[-1]
    if isinstance(last_msg, dict):
        content = last_msg.get("content", "")
    else:
        content = getattr(last_msg, "content", "")

    if not guard.before_model(content):
        raise ValueError("Invalid request: forbidden topic detected")
    return None

class ChatService:
    def __init__(self, db: AsyncSession | None = None, current_user: User | None = None, middleware: list | None = None, tools: list | None = None, agent: Any | None = None):
        self.db = db
        self.current_user = current_user
        self.tools = tools or []
        self.agent = agent
        self._session_id = "default_session"
        # A Langfuse prompt must be passed to the callback handler as the
        # original PromptClient, not merely as its compiled string. Keep that
        # association until the corresponding LLM call is made.
        self._compiled_langfuse_prompts: dict[str, Any] = {}
        self._last_langfuse_prompt: Any | None = None
        settings = get_settings()
        self.groq_api_key = (os.getenv("GROQ_API_KEY")
            or settings.groq_api_key
        )
        self.groq_model = os.getenv("GROQ_MODEL") or settings.groq_model
        if self.groq_model == "llama-3.3-70b-versatile":
            self.groq_model = "openai/gpt-oss-20b"
        
        # Configure middleware pipeline
        self.middleware = middleware if middleware is not None else [
            topic_guardrail
        ]
        
        print(f"==>> ChatService init: api_key_set={bool(self.groq_api_key)} model={self.groq_model!r} user={getattr(current_user, 'email', None)!r} middleware_count={len(self.middleware)} tools_count={len(self.tools)} agent_set={bool(self.agent)}")

    def _langfuse_callbacks(self) -> list:
        callback = get_langfuse_callback()
        return [callback] if callback else []

    def _prompt(self, name: str, **variables: Any) -> str:
        """Fetch and compile the production prompt from Langfuse."""
        client = _langfuse_client()
        if not client:
            raise RuntimeError("Langfuse prompts are enabled but the Langfuse client is not configured.")
        try:
            prompt = client.get_prompt(
                name,
                label="production",
                type="text",
                cache_ttl_seconds=60,
            )
            print("prompt-------------------------------------------", prompt.__dict__)
            compiled = prompt.compile(**variables)
            self._compiled_langfuse_prompts[compiled] = prompt
            self._last_langfuse_prompt = prompt
            return compiled
        except Exception as error:
            raise RuntimeError(f"Langfuse prompt {name!r} could not be fetched") from error

    def _langfuse_prompt_for_messages(self, messages: list[dict]) -> Any | None:
        """Return the remote prompt that forms the system instruction for this call."""
        for message in messages:
            if message.get("role") == "system":
                prompt = self._compiled_langfuse_prompts.get(str(message.get("content", "")))
                if prompt:
                    return prompt
        return None

    def _get_user_info(self) -> dict:
        if not self.current_user:
            return {}
        # Access customer only via __dict__ to avoid triggering a lazy load
        # if the ORM object has been detached or expired from its session.
        customer = self.current_user.__dict__.get("customer")
        phone = customer.phone if (customer and customer.phone) else "N/A"
        return {
            "full_name": self.current_user.full_name,
            "email": self.current_user.email,
            "phone": phone,
            "user_id": str(self.current_user.id),
        }

    def _extract_clean_address_with_llm(self, text: str) -> str:
        """Use LLM to extract the exact delivery address / location from user input."""
        if not text or not text.strip():
            return text or ""
        prompt = self._prompt("restaurant/address-extraction", text=text)
        try:
            cleaned = _clean_content(self._call_llm([
                {"role": "system", "content": self._prompt("restaurant/system/address-extraction")},
                {"role": "user", "content": prompt}
            ], max_tokens=60)).strip().strip('"\'')
            return cleaned if cleaned else text.strip()
        except Exception as err:
            logger.warning("[_extract_clean_address_with_llm] LLM call failed: %s", err)
            return text.strip()

    def _clean_and_deduplicate_addresses_with_llm(self, addresses: list[str]) -> list[str]:
        """Use LLM to normalize, format, and deduplicate a list of raw address strings. No hardcoding."""
        if not addresses:
            return []

        addresses_json = json.dumps(addresses)
        prompt = self._prompt("restaurant/address-normalization", addresses_json=addresses_json)
        try:
            raw = _clean_content(self._call_llm([
                {"role": "system", "content": self._prompt("restaurant/system/address-normalization")},
                {"role": "user", "content": prompt}
            ], max_tokens=150))
            parsed = _parse_json_from_llm(raw)
            result = parsed.get("addresses")
            if isinstance(result, list) and len(result) > 0:
                clean_list = [str(a).strip() for a in result if a and str(a).strip()]
                return clean_list[:5]
        except Exception as err:
            logger.warning("[_clean_and_deduplicate_addresses_with_llm] failed: %s", err)

        unique: list[str] = []
        for a in addresses:
            if a and a.strip() and not any(u.lower() == a.strip().lower() for u in unique):
                unique.append(a.strip())
        return unique[:5]

    async def _get_past_destinations(self) -> list[str]:
        """Return the list of past delivery addresses saved for the logged-in
        user's customer row and past orders. Returns [] when unauthenticated or no history."""
        if not self.current_user or not self.db:
            return []
        try:
            customer_result = await self.db.execute(
                select(Customer).where(
                    (Customer.user_id == self.current_user.id) |
                    (Customer.email == self.current_user.email)
                )
            )
            customers = customer_result.scalars().all()
            destinations: list[str] = []
            cust_ids = []
            for customer in customers:
                cust_ids.append(customer.id)
                if not customer.user_id:
                    customer.user_id = self.current_user.id
                    await self.db.commit()

                raw = customer.destination
                if isinstance(raw, list):
                    for a in raw:
                        if isinstance(a, str) and a.strip():
                            val = a.strip()
                            if not any(d.lower() == val.lower() for d in destinations):
                                destinations.append(val)
                elif isinstance(raw, str) and raw.strip():
                    val = raw.strip()
                    if not any(d.lower() == val.lower() for d in destinations):
                        destinations.append(val)

            # Query past orders for ALL matching customer records
            if cust_ids:
                past_order_result = await self.db.execute(
                    select(Order.delivery_destination).where(
                        Order.customer_id.in_(cust_ids),
                        Order.delivery_destination.isnot(None),
                        Order.delivery_destination != ""
                    )
                    .order_by(Order.id.desc())
                )
                for (dest,) in past_order_result.all():
                    if dest and dest.strip():
                        val = dest.strip()
                        if not any(d.lower() == val.lower() for d in destinations):
                            destinations.append(val)

            if destinations:
                destinations = self._clean_and_deduplicate_addresses_with_llm(destinations)

            logger.info("[_get_past_destinations] Cleaned user %s destinations: %r", self.current_user.id, destinations)
            return destinations[:5]
        except Exception as err:
            logger.warning("[_get_past_destinations] failed: %s", err)
            return []

    async def _save_past_destination(self, destination: str) -> None:
        """Append a newly used address to the customer's JSON address list
        (deduplicates, most-recent-first, capped at 5 entries)."""
        if not self.current_user or not self.db or not destination:
            return
        try:
            customer_result = await self.db.execute(
                select(Customer).where(
                    (Customer.user_id == self.current_user.id) |
                    (Customer.email == self.current_user.email)
                )
            )
            customer = customer_result.scalars().first()
            if not customer:
                return
            if not customer.user_id:
                customer.user_id = self.current_user.id

            existing: list[str] = []
            if isinstance(customer.destination, list):
                existing = [a for a in customer.destination if isinstance(a, str) and a.strip()]
            elif isinstance(customer.destination, str) and customer.destination.strip():
                existing = [customer.destination.strip()]

            # Remove duplicate (case-insensitive) then prepend new address
            existing = [a for a in existing if a.strip().lower() != destination.strip().lower()]
            existing.insert(0, destination.strip())
            customer.destination = existing[:5]  # keep last 5
            await self.db.commit()
            logger.info("[_save_past_destination] Saved address for user %s: %s", self.current_user.id, destination)
        except Exception as err:
            logger.warning("[_save_past_destination] failed: %s", err)


    @staticmethod
    def _sql_escape(value: str | None) -> str:
        """Double up single quotes before a value is embedded in a SQL-writing
        task prompt. This is a best-effort mitigation so the LLM's literal
        comes out valid — it is not a substitute for parameterized queries,
        which this architecture intentionally does not use (see
        CRITICAL_RULES in DB_SCHEMA_PROMPT)."""
        if not value:
            return value or ""
        return value.replace("'", "''")

    def _generate_sql(self, task: str) -> tuple[str | None, str]:
        """Ask the LLM to write the SQL for a specific internal task."""
        started_at = time.perf_counter()
        raw = _clean_content(self._call_llm([
            {"role": "system", "content": self._prompt("restaurant/db-schema")},
            {"role": "user", "content": task},
        ], _span_name="sql-generation"))
        parsed = _parse_json_from_llm(raw)
        logger.info(
            "[TIMING] sql_generation duration_ms=%.2f task=%s",
            (time.perf_counter() - started_at) * 1000,
            " ".join(task.split())[:160],
        )
        return parsed.get("sql"), parsed.get("explanation", "")

    async def _run_sql(self, sql: str) -> list[dict]:
        """Execute a single LLM-authored SQL statement and return its rows
        (empty list for statements with no result set). Mutating statements
        are committed; raises on failure so the caller can retry/handle it."""
        upper_sql = sql.strip().upper()
        is_select_type = upper_sql.startswith("SELECT") or (
            upper_sql.startswith("WITH")
            and "RETURNING" not in upper_sql
            and not any(f" {kw} " in f" {upper_sql} " for kw in ("INSERT", "UPDATE", "DELETE"))
        )
        started_at = time.perf_counter()
        try:
            result = await self.db.execute(text(sql))
        except Exception:
            timing = request_timing.get()
            if timing:
                timing.record("DATABASE", started_at, "SQL failed: " + " ".join(sql.split())[:180])
            logger.info(
                "[TIMING] DATABASE failed | %.2fs | %s",
                time.perf_counter() - started_at,
                " ".join(sql.split())[:180],
            )
            raise
        try:
            rows = [dict(zip(result.keys(), row)) for row in result.fetchall()]
        except Exception:
            rows = []
        if not is_select_type:
            await self.db.commit()
        timing = request_timing.get()
        if timing:
            timing.record("DATABASE", started_at, f"{len(rows)} rows: {' '.join(sql.split())[:180]}")
        else:
            logger.info("[TIMING] DATABASE | %.2fs | %d rows | %s", time.perf_counter() - started_at, len(rows), " ".join(sql.split())[:180])
        return rows

    async def _generate_and_run(self, task: str) -> list[dict] | None:
        """Generate SQL for `task` and execute it. On failure, feeds the
        error back to the LLM once for a fix before giving up. Returns None
        only when no SQL could be produced/executed at all."""
        sql, _ = self._generate_sql(task)
        if not sql:
            logger.warning("[_generate_and_run] LLM returned no SQL for task: %s", task)
            return None
        try:
            return await self._run_sql(sql)
        except Exception as err:
            await self.db.rollback()
            logger.warning("[_generate_and_run] SQL failed (%s), asking LLM to fix: %s", err, sql)
            fix_task = (
                f"{task}\n\nThe SQL you previously wrote:\n{sql}\n\n"
                f"failed with database error:\n{err}\n\n"
                "Fix it. Remember: no placeholders like $1/?/:param — literal values only."
            )
            sql2, _ = self._generate_sql(fix_task)
            if not sql2:
                return None
            try:
                return await self._run_sql(sql2)
            except Exception as err2:
                await self.db.rollback()
                logger.error("[_generate_and_run] Retry also failed: %s", err2)
                return None

    async def _order_items(self, order_id: int) -> list[dict]:
        task = self._prompt("restaurant/order-items-query", order_id=order_id)
        rows = await self._generate_and_run(task) or []
        logger.info("[_order_items] order_id=%s returned %d rows", order_id, len(rows))
        return rows

    def _is_order_details_follow_up(self, history: InMemoryChatMessageHistory, message: str) -> bool:
        print(f"==>> _is_order_details_follow_up: called (LLM)")
        """Use the LLM to decide whether the current message is providing
        contact details that complete an in-progress order from earlier in
        the conversation."""
        if not history.messages:
            return False

        recent_turns = "\n".join(
            f"{'User' if m.type == 'human' else 'Assistant'}: {m.content}"
            for m in history.messages[-6:]
        )
        prompt = self._prompt("restaurant/contact-follow-up", recent_turns=recent_turns, message=message)
        try:
            raw = _clean_content(self._call_llm([
                {"role": "system", "content": self._prompt("restaurant/system/intent-classifier")},
                {"role": "user", "content": prompt},
            ], max_tokens=60))
            result = _parse_json_from_llm(raw)
            return bool(result.get("is_contact_follow_up", False))
        except Exception:
            return False

    def _classify_intent(
        self, history: InMemoryChatMessageHistory, message: str, session_order_id: int | None
    ) -> str:
        """Ask the LLM what the user is trying to do.

        Returns one of:
          give_destination  – user is providing a delivery address
          confirm_order     – user is confirming a pending order
          delivery_status   – user wants delivery ETA / time remaining
          add_item          – user wants to add more items to an existing order
          contact_followup  – user is giving name/email/phone to complete an order
          general           – anything else
        """
        print(f"==>> _classify_intent: called")
        recent_turns = "\n".join(
            f"{'User' if m.type == 'human' else 'Assistant'}: {m.content}"
            for m in history.messages[-8:]
        )
        prompt = self._prompt("restaurant/intent-classification", recent_turns=recent_turns, message=message, session_order_id=session_order_id)
        try:
            raw = _clean_content(self._call_llm([
                {"role": "system", "content": self._prompt("restaurant/system/precise-intent")},
                {"role": "user", "content": prompt},
            ], max_tokens=50, _span_name="intent-classification"))
            intent = _parse_json_from_llm(raw).get("intent", "general")
            valid = {"give_destination", "confirm_order", "delivery_status", "add_item", "contact_followup", "general"}
            if intent in valid and intent != "general":
                return intent
        except Exception:
            pass

        # Fallback if LLM returned 'general' or failed: check if strong intent signals exist
        import re
        msg_lower = message.strip().lower()
        if re.search(r"\b(confirm|proceed|yes confirm|confirm order)\b", msg_lower):
            return "confirm_order"
        if re.search(r"\b(eta|timing|time left|delivery time|how long|time remaining)\b", msg_lower):
            return "delivery_status"
        if "@" in msg_lower or re.search(r"\b(name is|my name|email is|phone|contact no)\b", msg_lower):
            return "contact_followup"
        if re.search(r"\b(delivery address|destination|deliver to|location)\b", msg_lower):
            return "give_destination"

        return "general"

    def _call_llm(
        self,
        messages: list[dict],
        max_tokens: int = 1200,
        _span_name: str = "llm-call",
    ) -> str:
        print(f"==>> _call_llm: called")
        started_at = time.perf_counter()
        user_prompt = next(
            (str(message.get("content", "")) for message in reversed(messages) if message.get("role") == "user"),
            "",
        )
        if self.agent:
            try:
                metadata = {
                    # These names are understood by Langfuse and populate the
                    # trace's Sessions and Users views.
                    "langfuse_session_id": self._session_id,
                    "langfuse_user_id": str(getattr(self.current_user, "id", "")),
                    "ls_model_name": get_settings().langfuse_model_name or self.groq_model,
                }
                res = self.agent.invoke(
                    {"messages": messages},
                    config={
                        # Model calls may include classifiers and SQL helpers.
                        # The single customer-facing generation is recorded in
                        # process_query, after the final response is known.
                        "callbacks": [],
                        "run_name": "restaurant-chat",
                        "tags": ["restros", "chat"],
                        "metadata": metadata,
                    },
                )
                response_messages = res["messages"]
                last_msg = response_messages[-1]
                content = getattr(last_msg, "content", str(last_msg))
                # Extract token usage from agent response metadata
                agent_usage = {}
                rm = getattr(last_msg, "response_metadata", {}) or {}
                token_usage = rm.get("token_usage") or rm.get("usage") or {}
                if token_usage:
                    agent_usage = {
                        "prompt_tokens": token_usage.get("prompt_tokens") or token_usage.get("input_tokens", 0),
                        "completion_tokens": token_usage.get("completion_tokens") or token_usage.get("output_tokens", 0),
                        "total_tokens": token_usage.get("total_tokens", 0),
                    }
                timing = request_timing.get()
                if timing:
                    timing.record("LLM", started_at, f"agent: {' '.join(user_prompt.split())[:160]}", agent_usage)
                else:
                    logger.info("[TIMING] LLM | %.2fs | agent: %s", time.perf_counter() - started_at, " ".join(user_prompt.split())[:160])
                return _clean_content(content)
            except ValueError as ve:
                if "forbidden topic" in str(ve).lower() or "invalid request" in str(ve).lower():
                    raise ve
                logger.warning("[AGENT_INVOKE] agent.invoke failed (%s), falling back to Groq HTTP client", ve)
            except Exception as e:
                logger.warning("[AGENT_INVOKE] agent.invoke failed (%s), falling back to Groq HTTP client", e)

        if not self.groq_api_key:
            raise RuntimeError("GROQ_API_KEY is missing from environment variables.")

        lf_client = _langfuse_client()
        model_name = get_settings().langfuse_model_name or self.groq_model

        def _do_groq_call() -> str:
            with httpx.Client(timeout=20.0) as client:
                response = client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self.groq_api_key}", "Content-Type": "application/json"},
                    json={
                        "model": self.groq_model,
                        "messages": messages,
                        "temperature": 0.1,
                        "max_tokens": max_tokens,
                    },
                )
            if response.status_code == 200:
                resp_json = response.json()
                msg = resp_json["choices"][0]["message"]
                content = msg.get("content") or msg.get("reasoning") or ""
                usage = resp_json.get("usage", {})
                timing = request_timing.get()
                if timing:
                    timing.record("LLM", started_at, f"groq: {' '.join(user_prompt.split())[:160]}", usage)
                else:
                    logger.info("[TIMING] LLM | %.2fs | groq: %s", time.perf_counter() - started_at, " ".join(user_prompt.split())[:160])
                # Update the current child generation span with tokens + model
                # so Langfuse's pricing engine computes cost for this call.
                if lf_client:
                    try:
                        lf_client.update_current_generation(
                            model=model_name,
                            output=content,
                            usage_details={
                                "input": usage.get("prompt_tokens", 0),
                                "output": usage.get("completion_tokens", 0),
                                "total": usage.get("total_tokens", 0),
                            },
                        )
                    except Exception:
                        pass
                return _clean_content(content)
            error = f"Groq API Error {response.status_code}: {response.text}"
            logger.warning("Groq model %s failed: %s", self.groq_model, error)
            raise RuntimeError(error)

        try:
            if lf_client:
                # Each _call_llm gets its own child generation span so Langfuse
                # records cost per-call rather than only on the parent span.
                with lf_client.start_as_current_observation(
                    name=_span_name,
                    as_type="generation",
                    model=model_name,
                    input=messages,
                ):
                    return _do_groq_call()
            return _do_groq_call()
        except httpx.HTTPError as error:
            logger.warning("Groq request failed: %s", error)
            raise RuntimeError(f"Groq request failed: {error}") from error

    async def _execute_sql(self, sql_query: str, user_message: str, retried: bool = False) -> dict:
        print(f"==>> _execute_sql: called")
        upper_sql = sql_query.strip().upper()
        allowed_verbs = ("SELECT", "INSERT", "UPDATE", "DELETE", "WITH")

        if not any(upper_sql.startswith(verb) for verb in allowed_verbs):
            return {
                "response": "I can only execute SELECT, INSERT, UPDATE, or DELETE operations.",
                "sql_query": None
            }

        try:
            is_select_type = upper_sql.startswith("SELECT") or (upper_sql.startswith("WITH") and "RETURNING" not in upper_sql and "INSERT" not in upper_sql and "UPDATE" not in upper_sql and "DELETE" not in upper_sql)

            if is_select_type:
                rows = await self._run_sql(sql_query)

                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                synthesis_prompt = self._prompt(
                    "restaurant/response-synthesis",
                    user_message=user_message, rows=json.dumps(rows, default=str), row_count=len(rows), current_time=current_time,
                )
                synth_messages = [
                    {"role": "system", "content": self._prompt("restaurant/system/database-summary")},
                    {"role": "user", "content": synthesis_prompt}
                ]
                final_response = _clean_content(self._call_llm(synth_messages, _span_name="response-synthesis"))
                final_response = re.sub(r"```.*?```", "", final_response, flags=re.DOTALL).strip()

                return {
                    "response": final_response,
                    "sql_query": sql_query
                }
            else:
                returned_rows = await self._run_sql(sql_query)
                rowcount = len(returned_rows)

                returned_info = ""
                if returned_rows:
                    returned_info = f"\nReturned data from database: {json.dumps(returned_rows, default=str)}"

                synthesis_prompt = self._prompt("restaurant/order-confirmation", user_message=user_message, rowcount=rowcount, returned_info=returned_info)
                synth_messages = [
                    {"role": "system", "content": self._prompt("restaurant/system/database-confirmation")},
                    {"role": "user", "content": synthesis_prompt}
                ]
                final_response = _clean_content(self._call_llm(synth_messages, _span_name="order-confirmation"))
                final_response = re.sub(r"```.*?```", "", final_response, flags=re.DOTALL).strip()

                # If an order was just created, register it in the session and
                # attach past addresses so the frontend shows the address picker.
                past_addresses: list[str] = []
                order_id_from_rows = None
                for row in returned_rows:
                    oid = row.get("order_id")
                    if oid:
                        order_id_from_rows = int(oid)
                        break
                if order_id_from_rows:
                    past_addresses = await self._get_past_destinations()

                return {
                    "response": final_response,
                    "sql_query": sql_query,
                    "past_addresses": past_addresses,
                    "ask_destination": bool(order_id_from_rows),
                    "_new_order_id": order_id_from_rows,
                }

        except Exception as err:
            await self.db.rollback()
            if not retried:
                fix_task = "Please fix the SQL query. Remember: DO NOT use placeholders like $1 or $2 or ?. Write literal values directly in the SQL."
                fix_prompt = self._prompt("restaurant/sql-fix", sql=sql_query, error=str(err), task=fix_task)
                try:
                    retry_output = self._call_llm([
                        {"role": "system", "content": self._prompt("restaurant/db-schema")},
                        {"role": "user", "content": fix_prompt}
                    ])
                    clean_retry = re.sub(r"^```(json)?|```$", "", retry_output.strip(), flags=re.MULTILINE).strip()
                    parsed_retry = json.loads(clean_retry)
                    new_sql = parsed_retry.get("sql")
                    if new_sql:
                        return await self._execute_sql(new_sql, user_message, retried=True)
                except Exception:
                    pass

            error_synth_prompt = self._prompt("restaurant/error-synthesis", user_message=user_message, error=str(err))
            try:
                friendly_error = self._call_llm(
                    [
                        {"role": "system", "content": self._prompt("restaurant/system/database-assistant")},
                        {"role": "user", "content": error_synth_prompt},
                    ],
                )
            except Exception:
                friendly_error = "I couldn't complete the request because the requested restaurant or customer details could not be found."

            return {
                "response": friendly_error,
                "sql_query": sql_query
            }

    async def _create_order_from_llm(self, history: InMemoryChatMessageHistory, message: str, session_id: str = "default_session") -> dict | None:
        logger.info("[_create_order_from_llm] Called for session_id=%s, message=%r", session_id, message)
        print(f"==>> _create_order_from_llm: called")
        """Let the LLM resolve customer + order details from the conversation,
        then let the LLM write the SQL that actually creates them."""
        user_info = self._get_user_info()

        conversation = "\n".join(
            f"{'User' if item.type == 'human' else 'Assistant'}: {item.content}"
            for item in history.messages[-12:]
        )

        if user_info:
            extraction_prompt = self._prompt("restaurant/authenticated-order-extraction", conversation=conversation, user_full_name=user_info["full_name"], user_email=user_info["email"], message=message)
        else:
            extraction_prompt = self._prompt("restaurant/guest-order-extraction", conversation=conversation, message=message)
        try:
            raw = _clean_content(self._call_llm([
                {"role": "system", "content": self._prompt("restaurant/system/order-extraction")},
                {"role": "user", "content": extraction_prompt},
            ], max_tokens=250))
            logger.info("[_create_order_from_llm] Raw LLM output: %r", raw)
            extracted = _parse_json_from_llm(raw)
            logger.info("[_create_order_from_llm] Parsed JSON: %s", extracted)

            order_data = extracted.get("order") or {}
            raw_items = order_data.get("items") or []
            cafe = order_data.get("cafe")

            if not raw_items and order_data.get("menu_item"):
                raw_items = [{"menu_item": order_data.get("menu_item"), "quantity": order_data.get("quantity") or 1}]

            if user_info:
                full_name = user_info["full_name"]
                email = user_info["email"]
                phone = user_info["phone"]
                user_id = user_info["user_id"]
            else:
                customer_data = extracted.get("customer") or {}
                full_name = customer_data.get("full_name")
                email = customer_data.get("email")
                phone = customer_data.get("phone")
                user_id = None

                if not (isinstance(full_name, str) and full_name.strip() and isinstance(email, str) and email.strip() and isinstance(phone, str) and phone.strip()):
                    logger.warning("[_create_order_from_llm] Missing customer contact details.")
                    return None

            if not raw_items:
                name_str = f", {full_name.strip()}" if full_name and isinstance(full_name, str) else ""
                return {
                    "response": f"Nice to meet you{name_str}! Which café would you like to order from, and what menu items would you like?",
                    "sql_query": None,
                }
        except Exception as err:
            logger.error("[_create_order_from_llm] Exception during extraction: %s", err, exc_info=True)
            return None

        items_desc = "; ".join(
            f"{int(it.get('quantity') or 1)} x {self._sql_escape(str(it.get('menu_item')))}"
            for it in raw_items
            if isinstance(it, dict) and it.get("menu_item")
        )
        if not items_desc:
            name_str = f", {full_name.strip()}" if full_name and isinstance(full_name, str) else ""
            return {
                "response": f"Thanks{name_str}! Which café would you like to order from, and what menu items would you like?",
                "sql_query": None,
            }

        task = self._prompt("restaurant/create-order-sql", full_name=self._sql_escape(full_name), email=self._sql_escape(email), phone=self._sql_escape(phone), user_id=self._sql_escape(user_id) if user_id else None, cafe=self._sql_escape(cafe) if cafe and isinstance(cafe, str) else None, items_desc=items_desc)
        rows = await self._generate_and_run(task)
        if not rows or not rows[0].get("order_id"):
            logger.warning("[_create_order_from_llm] LLM SQL did not resolve items / create an order.")
            name_str = f", {full_name.strip()}" if full_name else ""
            return {
                "response": f"Thanks{name_str}! Which café would you like to order from, and what menu items would you like?",
                "sql_query": None,
            }

        order_id = rows[0]["order_id"]
        customer_name = rows[0].get("customer_name") or full_name
        if not rows[0].get("item_count"):
            logger.error("[_create_order_from_llm] Order #%s was created without order items", order_id)
            return {
                "response": "I couldn't match the requested menu item to the café menu. Please try the item name again.",
                "sql_query": None,
            }

        _session_orders[session_id] = order_id
        logger.info("[_create_order_from_llm] Successfully created Order #%s for customer %s (session_id=%s)", order_id, customer_name, session_id)

        past_addresses = await self._get_past_destinations()
        dest_prompt = (
            "Choose your **delivery destination** below, or type a new address."
            if past_addresses
            else "Please share your **delivery destination** (area/address in Ahmedabad) so I can prepare your order summary and calculate a delivery estimate for you."
        )
        return {
            "response": f"Thanks, {customer_name}! 🛵 {dest_prompt}",
            "sql_query": None,
            "past_addresses": past_addresses,
            "ask_destination": True,
        }

    async def _add_item_from_llm(self, history: InMemoryChatMessageHistory, message: str, session_order_id: int | None) -> dict | None:
        print(f"==>> _add_item_from_llm: called")
        """Use the LLM to identify the item change, then let the LLM write the
        SQL that inserts the item and updates the order's total."""
        prompt = self._prompt("restaurant/add-item-extraction", session_order_id=session_order_id, message=message)
        try:
            raw = _clean_content(self._call_llm([
                {"role": "system", "content": self._prompt("restaurant/system/item-extraction")},
                {"role": "user", "content": prompt},
            ], max_tokens=150))
            data = _parse_json_from_llm(raw)
            order_id = int(data.get("order_id") or session_order_id)
            menu_item = data.get("menu_item")
            quantity = int(data.get("quantity") or 1)
            if not isinstance(menu_item, str) or not menu_item.strip() or quantity < 1:
                return None
        except (KeyError, TypeError, ValueError, RuntimeError):
            return None

        task = self._prompt("restaurant/add-item-sql", order_id=order_id, menu_item=self._sql_escape(menu_item.strip()), quantity=quantity)
        rows = await self._generate_and_run(task)
        if not rows:
            return None
        item_name = rows[0].get("item_name")
        unit_price = rows[0].get("unit_price")
        if not item_name or unit_price is None:
            return None

        total_rows = await self._generate_and_run(self._prompt("restaurant/order-total", order_id=order_id))
        new_total = total_rows[0]["total_amount"] if total_rows else None

        return {
            "response": (
                f"Added {quantity} {item_name} to your order."
                + (f" Your updated total is ₹{new_total}." if new_total is not None else "")
            ),
            "sql_query": None,
        }

    async def _latest_active_order(self, order_id: int | None = None) -> dict | None:
        print(f"==>> _latest_active_order: called")
        """Return the latest order (joined with its restaurant and, if any,
        its customer) that can still be delivered in this demo app, via
        LLM-authored SQL."""
        scope = (
            f"Restrict to the order with id = {order_id}."
            if order_id
            else "Do not filter by id; return only the single most recently created such order."
        )
        task = self._prompt("restaurant/latest-active-order", scope=scope)
        rows = await self._generate_and_run(task)
        return rows[0] if rows else None

    def _extract_contact_details(self, history: InMemoryChatMessageHistory, message: str) -> dict | None:
        print(f"==>> _extract_contact_details: called")
        """Ask the LLM to pull a full name, email, and phone number out of
        the recent conversation + latest message. Returns None unless all
        three are present."""
        recent_turns = "\n".join(
            f"{'User' if m.type == 'human' else 'Assistant'}: {m.content}"
            for m in history.messages[-6:]
        )
        prompt = self._prompt("restaurant/contact-details", recent_turns=recent_turns, message=message)
        try:
            raw = _clean_content(self._call_llm([
                {"role": "system", "content": self._prompt("restaurant/system/contact-extraction")},
                {"role": "user", "content": prompt},
            ], max_tokens=150))
            data = _parse_json_from_llm(raw)
            full_name, email, phone = data.get("full_name"), data.get("email"), data.get("phone")
            if not (
                isinstance(full_name, str) and full_name.strip()
                and isinstance(email, str) and email.strip()
                and isinstance(phone, str) and phone.strip()
            ):
                return None
            return {"full_name": full_name.strip(), "email": email.strip(), "phone": phone.strip()}
        except Exception as err:
            logger.warning("[_extract_contact_details] extraction failed: %s", err)
            return None

    async def _attach_contact_to_order(self, order_id: int, full_name: str, email: str, phone: str) -> str | None:
        print(f"==>> _attach_contact_to_order: called")
        """Ensure a customer with these details exists and link them to an
        already-created order, via LLM-authored SQL. Returns the customer's
        name on success."""
        task = self._prompt("restaurant/attach-contact", order_id=order_id, full_name=self._sql_escape(full_name), email=self._sql_escape(email), phone=self._sql_escape(phone))
        rows = await self._generate_and_run(task)
        if not rows:
            return None
        return rows[0].get("customer_name") or full_name

    async def _order_summary_response(self, active_order: dict, prefix: str = "") -> str:
        print(f"==>> _order_summary_response: called")
        items = await self._order_items(active_order["order_id"])
        bill_lines = [
            f"  • {i['item_name']} × {i['quantity']} — ₹{float(i['unit_price']) * i['quantity']:.2f}"
            for i in items
        ]
        bill_text = "\n".join(bill_lines) if bill_lines else "  (no items found)"
        total = sum(
            (Decimal(str(item["unit_price"])) * int(item["quantity"])
             for item in items if item.get("unit_price") is not None),
            Decimal("0"),
        )
        eta = active_order.get("estimated_delivery_minutes")
        return (
            f"{prefix}"
            f"🧾 **Order Summary** (from {active_order['cafe']}):"
            f"\n{bill_text}"
            f"\n\n💰 **Total: ₹{total:.2f}**"
            + (f"\n⏱ Estimated delivery time: **~{eta} minutes**" if eta else "")
            + "\n\nPlease type **CONFIRM** to place your order, or let me know if you would like to modify any items."
        )

    def _estimate_delivery_minutes(self, cafe_location: str, destination: str) -> int:
        """Estimate delivery time with one short, numeric-only model call."""
        print("==>> _estimate_delivery_minutes: called")
        prompt = self._prompt("restaurant/eta-estimate", cafe_location=cafe_location, destination=destination)
        try:
            raw = _clean_content(self._call_llm([
                {"role": "system", "content": self._prompt("restaurant/system/eta")},
                {"role": "user", "content": prompt},
            ], max_tokens=8))
            match = re.search(r"\d+", raw)
            if match:
                return max(15, min(90, int(match.group())))
        except Exception as err:
            logger.warning("[_estimate_delivery_minutes] failed: %s", err)
        return 45

    async def _save_destination(self, destination: str, order_id: int | None = None) -> dict:
        print(f"==>> _save_destination: called")
        active_order = await self._latest_active_order(order_id)
        if not active_order:
            return {
                "response": "I couldn't find an active order to deliver. Please select your café and menu items first.",
                "sql_query": None,
            }

        destination = self._extract_clean_address_with_llm(destination)
        eta_minutes = self._estimate_delivery_minutes(active_order["location"], destination)
        items = await self._order_items(active_order["order_id"])
        if not items:
            logger.error("[_save_destination] No order_items found for order_id=%s", active_order["order_id"])
            return {
                "response": "I couldn't load the items in this order. Please start the order again so I can prepare the correct summary.",
                "sql_query": None,
            }
        total = sum(
            (Decimal(str(item["unit_price"])) * int(item["quantity"])
             for item in items if item.get("unit_price") is not None),
            Decimal("0"),
        )

        update_task = self._prompt("restaurant/save-destination-sql", destination=self._sql_escape(destination), eta_minutes=eta_minutes, order_id=active_order["order_id"])
        if await self._generate_and_run(update_task) is None:
            return {
                "response": "I had trouble saving your delivery destination. Could you try again?",
                "sql_query": None,
            }

        # Persist this address in the user's address history
        await self._save_past_destination(destination)

        # Build itemized bill to show before asking for confirmation
        bill_lines = [
            f"  • {i['item_name']} × {i['quantity']} — ₹{float(i['unit_price']) * i['quantity']:.2f}"
            for i in items
        ]
        bill_text = "\n".join(bill_lines) if bill_lines else "  (no items found)"

        response = (
            f"📍 Delivery destination set to **{destination}**."
            f"\n\n🧾 **Order Summary** (from {active_order['cafe']}):"
            f"\n{bill_text}"
            f"\n\n💰 **Total: ₹{total:.2f}**"
            f"\n⏱ Estimated delivery time: **~{eta_minutes} minutes**"
            f"\n\nPlease type **CONFIRM** to place your order, or let me know if you would like to modify any items."
        )
        return {
            "response": response,
            "sql_query": None,
        }

    async def _confirm_latest_order(self, order_id: int | None = None) -> dict:
        print(f"==>> _confirm_latest_order: called")
        active_order = await self._latest_active_order(order_id)
        if not active_order:
            return {"response": "I couldn't find an active order to confirm.", "sql_query": None}

        if not active_order.get("delivery_destination"):
            past_addresses = await self._get_past_destinations()
            return {
                "response": "Please select your delivery destination below before we can place your order.",
                "sql_query": None,
                "ask_destination": True,
                "past_addresses": past_addresses,
            }

        if not (active_order.get("customer_name") and active_order.get("customer_email") and active_order.get("customer_phone")):
            user_info = self._get_user_info()
            if user_info:
                attached_name = await self._attach_contact_to_order(
                    active_order["order_id"],
                    user_info["full_name"],
                    user_info["email"],
                    user_info["phone"],
                )
                if not attached_name:
                    return {
                        "response": "Before I can confirm your order, could you share your full name, email, and phone number?",
                        "sql_query": None,
                    }
                active_order["customer_name"] = attached_name
            else:
                return {
                    "response": "Before I can confirm your order, could you share your full name, email, and phone number?",
                    "sql_query": None,
                }

        eta_minutes = active_order.get("estimated_delivery_minutes")
        if not eta_minutes:
            eta_minutes = self._estimate_delivery_minutes(active_order["location"], active_order["delivery_destination"])

        update_task = self._prompt("restaurant/confirm-order-sql", eta_minutes=eta_minutes, order_id=active_order["order_id"])
        if await self._generate_and_run(update_task) is None:
            return {"response": "I had trouble confirming your order. Could you try again?", "sql_query": None}

        items = await self._order_items(active_order["order_id"])
        bill_lines = [
            f"  • {i['item_name']} × {i['quantity']} — ₹{float(i['unit_price']) * i['quantity']:.2f}"
            for i in items
        ]
        bill_text = "\n".join(bill_lines) if bill_lines else "  (no items)"

        return {
            "response": (
                f"🎉 Order **#{active_order['order_id']}** placed successfully!"
                f"\n\n🧾 **Bill Summary:**"
                f"\n{bill_text}"
                f"\n💰 **Total: ₹{float(active_order['total_amount']):.2f}**"
                f"\n\n🛵 Delivering to **{active_order['delivery_destination']}**."
                f"\n⏱ Estimated delivery time: **~{eta_minutes} minutes**."
                f"\n\nYou can ask *'how much time is left?'* anytime to check your remaining delivery time."
            ),
            "sql_query": None,
        }

    async def _delivery_status(self, order_id: int | None = None, message: str = "") -> dict:
        print(f"==>> _delivery_status: called")
        logger.info("[_delivery_status] Called with order_id=%s, message=%r", order_id, message)

        active_order = await self._latest_active_order(order_id)
        if not active_order:
            if order_id:
                rows = await self._generate_and_run(self._prompt("restaurant/order-status", order_id=order_id))
                row = rows[0] if rows else None
                if row and row.get("order_status") == "completed":
                    return {
                        "response": f"🎉 Your order placed successfully! Order **#{order_id}** has been delivered to **{row.get('delivery_destination') or 'your destination'}**! Enjoy your meal! 🍽️",
                        "sql_query": None,
                    }
            return {"response": "I couldn't find an active order.", "sql_query": None}

        order_id = active_order["order_id"]

        if active_order["order_status"] == "completed":
            return {
                "response": f"🎉 Your order placed successfully! Order **#{order_id}** has been delivered to **{active_order.get('delivery_destination') or 'your destination'}**! Enjoy your meal! 🍽️",
                "sql_query": None,
            }

        if active_order["order_status"] == "pending" or not active_order.get("confirmed_at"):
            if not active_order.get("delivery_destination"):
                return {
                    "response": "Please provide your delivery destination first so we can prepare your order summary.",
                    "sql_query": None,
                }
            return {
                "response": "Your order is pending confirmation. Please type **CONFIRM** to confirm your order and activate delivery tracking.",
                "sql_query": None,
            }

        confirmed_at = active_order["confirmed_at"]
        if isinstance(confirmed_at, str):
            confirmed_at = datetime.fromisoformat(confirmed_at)
        if confirmed_at.tzinfo is not None:
            confirmed_at = confirmed_at.replace(tzinfo=None)

        now = datetime.now()
        elapsed_seconds = max(0.0, (now - confirmed_at).total_seconds())
        elapsed_minutes = int(elapsed_seconds // 60)
        estimated_total = active_order["estimated_delivery_minutes"]
        remaining_minutes = max(0, estimated_total - elapsed_minutes)

        logger.info("[_delivery_status] order_id=%s, confirmed_at=%s, now=%s, elapsed_sec=%.1f, elapsed_min=%d, remaining_min=%d",
                    order_id, confirmed_at, now, elapsed_seconds, elapsed_minutes, remaining_minutes)

        if remaining_minutes <= 0:
            await self._generate_and_run(self._prompt("restaurant/complete-order", order_id=order_id))
            return {
                "response": (
                    f"🎉 Your order placed successfully! Order **#{order_id}** has been delivered to **{active_order['delivery_destination']}**! "
                    "Enjoy your meal! 🍽️"
                ),
                "sql_query": None,
            }

        elapsed_display = f"{elapsed_minutes} minute{'s' if elapsed_minutes != 1 else ''}" if elapsed_minutes > 0 else "less than a minute"
        return {
            "response": (
                f"🛵 Your order **#{order_id}** is on the way to **{active_order['delivery_destination']}**!"
                f"\n⏱ Time elapsed: {elapsed_display}"
                f"\n⏳ Estimated time remaining: **~{remaining_minutes} minute{'s' if remaining_minutes != 1 else ''}**."
            ),
            "sql_query": None,
        }

    async def process_query(self, user_message: str, session_id: str = "default_session") -> dict:
        self._session_id = session_id
        timing = RequestTiming()
        token = request_timing.set(timing)
        client = _langfuse_client()
        try:
            if not client:
                result = await self._process_query(user_message, session_id)
                result["tokens_used"] = timing.total_tokens
                result["total_cost"] = round(timing.total_tokens * 0.00000059, 6)
                return result

            # The parent observation establishes one trace for the entire chat
            # request. LangChain callback observations created in _call_llm are
            # automatically attached to this active context.
            with client.start_as_current_observation(
                name="restaurant-chat",
                as_type="generation",
                input={"message": user_message},
                model=get_settings().langfuse_model_name or self.groq_model,
                metadata={
                    "session_id": session_id,
                    "user_id": str(getattr(self.current_user, "id", "")),
                },
            ) as trace:
                # Set session_id and user_id as OTEL trace attributes so
                # Langfuse evaluators can read them.
                from langfuse import LangfuseOtelSpanAttributes as _A
                from opentelemetry import trace as _otel_trace
                _span = _otel_trace.get_current_span()
                _span.set_attribute(_A.TRACE_SESSION_ID, session_id)
                _span.set_attribute(_A.TRACE_USER_ID, str(getattr(self.current_user, "id", "")))
                try:
                    result = await self._process_query(user_message, session_id)
                except Exception as error:
                    trace.update(level="ERROR", status_message=str(error))
                    raise

                evaluation_input: dict[str, Any] = {"user_message": user_message}
                user_info = self._get_user_info()
                if user_info:
                    evaluation_input["authenticated_user"] = user_info

                # Update the generation with token usage, input/output so
                # Langfuse evaluators and cost tracking work correctly.
                trace.update(
                    input=evaluation_input,
                    output=result.get("response", ""),
                    usage_details={
                        "input": timing.prompt_tokens,
                        "output": timing.completion_tokens,
                        "total": timing.total_tokens,
                    },
                )
                result["tokens_used"] = timing.total_tokens
                result["total_cost"] = round(timing.total_tokens * 0.00000059, 6)
                return result
        finally:
            timing.finish(session_id)
            request_timing.reset(token)
            # Events are buffered by the SDK. Flushing here makes a completed
            # request visible promptly and is especially important in dev mode.
            if client:
                client.flush()

    async def _process_query(self, user_message: str, session_id: str = "default_session") -> dict:
        print(f"==>> process_query: called, session_id={session_id}")
        logger.info("[PROCESS_QUERY] Called with session_id=%s, user_message=%r", session_id, user_message)
        user_message = user_message.replace(r"\@", "@")
        history = get_session_history(session_id)

        # ── Middleware pipeline is handled natively by agent.invoke ──
        # for mw in self.middleware:
        #     try:
        #         if isinstance(mw, TopicGuardrailMiddleware):
        #             if not mw.before_model(user_message):
        #                 raise ValueError("Invalid request: forbidden topic detected")
        #         elif hasattr(mw, "before_model"):
        #             state = {"messages": [{"role": "user", "content": user_message}]}
        #             mw.before_model(state, None)
        #     except ValueError:
        #         blocked_msg = "Your request was blocked because it contains discussion or requests involving drugs, weapons, or illegal activities."
        #         history.add_user_message(user_message)
        #         history.add_ai_message(blocked_msg)
        #         return {"response": blocked_msg, "sql_query": None}

        try:
            session_order_id = _session_orders.get(session_id) or _session_order_id(history)
            logger.info("[PROCESS_QUERY] Resolved session_order_id=%s for session_id=%s", session_order_id, session_id)

            # ── Single LLM intent classifier — no more string-matching on AI messages ──
            intent = self._classify_intent(history, user_message, session_order_id)
            logger.info("[PROCESS_QUERY] Intent classified as '%s' (session_order_id=%s)", intent, session_order_id)
            print(f"==>> intent: {intent}  order_id: {session_order_id}")
        except ValueError as ve:
            if "forbidden topic" in str(ve).lower() or "invalid request" in str(ve).lower():
                blocked_msg = "Your request was blocked because it contains discussion or requests involving drugs, weapons, or illegal activities."
                history.add_user_message(user_message)
                history.add_ai_message(blocked_msg)
                return {"response": blocked_msg, "sql_query": None}
            raise

        result: dict | None = None

        # 1. User is giving a delivery address for an existing order
        if intent == "give_destination":
            result = await self._save_destination(user_message.strip(), session_order_id)

        # 2. User is confirming the order
        elif intent == "confirm_order":
            result = await self._confirm_latest_order(session_order_id)

        # 3. User wants delivery ETA / time left
        elif intent == "delivery_status":
            result = await self._delivery_status(session_order_id, user_message)

        # 4. User wants to add more items to an existing order
        elif intent == "add_item" and session_order_id:
            result = await self._add_item_from_llm(history, user_message, session_order_id)

        # 5. User is providing contact details to complete an order
        elif intent == "contact_followup":
            # If user is already logged in, their details are known — don't
            # treat casual mentions of "my name" as contact input.
            # Only handle this intent when the user is NOT authenticated.
            if self.current_user:
                # Fall through to the general LLM path which has user context.
                pass
            elif session_order_id:
                # An order already exists (e.g. created before contact details
                # were collected) — attach the customer to it rather than
                # trying to spin up a duplicate order.
                contact = self._extract_contact_details(history, user_message)
                if not contact:
                    result = {
                        "response": "Could you share your full name, email, and phone number so I can attach them to your order?",
                        "sql_query": None,
                    }
                else:
                    customer_name = await self._attach_contact_to_order(session_order_id, **contact)
                    if not customer_name:
                        result = {
                            "response": "I had trouble saving your contact details. Could you share your name, email, and phone number again?",
                            "sql_query": None,
                        }
                    else:
                        order_pair = await self._latest_active_order(session_order_id)
                        if order_pair and order_pair.get("delivery_destination"):
                            result = {
                                "response": await self._order_summary_response(order_pair, prefix=f"Thanks, {customer_name}! "),
                                "sql_query": None,
                            }
                        else:
                            result = {
                                "response": (
                                    f"Thanks, {customer_name}! 🛵 Please share your **delivery destination** "
                                    "(area/address in Ahmedabad) so I can prepare your order summary and calculate a delivery estimate for you."
                                ),
                                "sql_query": None,
                            }
            else:
                result = await self._create_order_from_llm(history, user_message, session_id)
                print("result:", result)
                if not result:
                    result = {
                        "response": "Thank you for sharing your contact details! Which café and menu items would you like to order?",
                        "sql_query": None,
                    }

        # 6. Active pending order missing destination: process address unless it's a menu inquiry
        elif session_order_id:
            order_pair = await self._latest_active_order(session_order_id)
            if order_pair and order_pair.get("order_status") == "pending" and not order_pair.get("delivery_destination"):
                if not any(w in user_message.lower() for w in ["menu", "top cafe", "show cafe", "what is", "help me", "recommend"]):
                    result = await self._save_destination(user_message.strip(), session_order_id)

        # ── If a structured handler produced a result, return it ──
        if result:
            logger.info("[PROCESS_QUERY] Handler produced response: %r", result["response"])
            history.add_user_message(user_message)
            history.add_ai_message(result["response"])
            return result

        # ── General path: ask the DB-schema LLM ──
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        user_info = self._get_user_info()
        user_context = None
        if user_info:
            past_addresses = await self._get_past_destinations()
            user_context = self._prompt(
                "restaurant/authenticated-user-context",
                full_name=user_info["full_name"], email=user_info["email"], phone=user_info["phone"],
                past_addresses=", ".join(f"'{address}'" for address in past_addresses) if past_addresses else "No saved addresses found",
            )
        history_messages = [
            ("user" if msg.type == "human" else "assistant", msg.content)
            for msg in history.messages[-8:]
        ]
        messages = [{
            "role": "system",
            "content": self._prompt(
                "restaurant/db-schema",
                current_time=current_time,
            ),
        }]
        if user_context:
            messages.append({"role": "system", "content": user_context})
        messages.extend({"role": role, "content": content} for role, content in history_messages)
        messages.append({"role": "user", "content": user_message})

        try:
            llm_output = self._call_llm(messages)
            logger.info("[GENERAL_PATH] Raw LLM output: %r", llm_output)
        except ValueError as ve:
            if "forbidden topic" in str(ve).lower() or "invalid request" in str(ve).lower():
                blocked_msg = "Your request was blocked because it contains discussion or requests involving drugs, weapons, or illegal activities."
                history.add_user_message(user_message)
                history.add_ai_message(blocked_msg)
                return {"response": blocked_msg, "sql_query": None}
            logger.error("[GENERAL_PATH] LLM request failed: %s", ve, exc_info=True)
            resp = "The ordering assistant is temporarily busy. Please try again in a few minutes."
            history.add_user_message(user_message)
            history.add_ai_message(resp)
            return {"response": resp, "sql_query": None}
        except Exception as error:
            logger.error("[GENERAL_PATH] LLM request failed: %s", error, exc_info=True)
            resp = "The ordering assistant is temporarily busy. Please try again in a few minutes."
            history.add_user_message(user_message)
            history.add_ai_message(resp)
            return {"response": resp, "sql_query": None}

        cleaned_output = _clean_content(llm_output)
        parsed = _parse_json_from_llm(cleaned_output)
        logger.info("[GENERAL_PATH] Cleaned output: %r | Parsed: %s", cleaned_output, parsed)

        if not parsed:
            is_incomplete = '"sql"' in cleaned_output or "'sql'" in cleaned_output
            parsed = {"sql": None, "explanation": "" if is_incomplete else cleaned_output}

        sql_query = parsed.get("sql")
        explanation = parsed.get("explanation", "")

        if not sql_query:
            final_response = (
                explanation.strip()
                if isinstance(explanation, str) and explanation.strip()
                else "Could you please specify the café name, menu item, or question you would like assistance with?"
            )
            logger.info("[GENERAL_PATH] No SQL query found. Returning final_response: %r", final_response)
            history.add_user_message(user_message)
            history.add_ai_message(final_response)
            return {"response": final_response, "sql_query": None}

        final_result = await self._execute_sql(sql_query, user_message)
        logger.info("[GENERAL_PATH] Executed SQL result: %r", final_result)

        # Register a newly created order in the session so the destination
        # handler (_save_destination) can find it without a stale lookup.
        new_oid = final_result.pop("_new_order_id", None)
        if new_oid:
            _session_orders[session_id] = new_oid
            logger.info("[GENERAL_PATH] Registered new order_id=%s in session %s", new_oid, session_id)

        history.add_user_message(user_message)
        history.add_ai_message(final_result["response"])
        return final_result
