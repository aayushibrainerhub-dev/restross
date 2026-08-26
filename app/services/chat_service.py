import json
import logging
import os
import re
import httpx
from datetime import datetime
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.core.config import get_settings
import dotenv
from langchain_core.chat_history import InMemoryChatMessageHistory
from langchain_core.stores import InMemoryStore as LangChainStore
dotenv.load_dotenv()

logger = logging.getLogger(__name__)

DB_SCHEMA_PROMPT = """
You are an expert PostgreSQL database assistant for a Restaurant Application.

PostgreSQL Schema:
- users (id UUID PRIMARY KEY, email VARCHAR(255), full_name VARCHAR(150), role VARCHAR(30), is_active BOOLEAN, created_at TIMESTAMP)
- restaurants (id INT PRIMARY KEY, cafe VARCHAR(150), location VARCHAR(100), rating_out_of_5 NUMERIC(3,2), feedback VARCHAR(500), menu_item VARCHAR(200), price_inr NUMERIC(10,2), approx_cost_for_two_inr NUMERIC(10,2))
- customers (id UUID PRIMARY KEY, full_name VARCHAR(150), phone VARCHAR(40), email VARCHAR(255), created_at TIMESTAMP)
- orders (id INT PRIMARY KEY, restaurant_id INT REFERENCES restaurants(id), customer_id UUID REFERENCES customers(id), status VARCHAR(30), total_amount NUMERIC(10,2), notes TEXT, delivery_destination VARCHAR(255), estimated_delivery_minutes INT, confirmed_at TIMESTAMP, created_at TIMESTAMP)
- order_items (id INT PRIMARY KEY, order_id INT REFERENCES orders(id), item_name VARCHAR(150), quantity INT, unit_price NUMERIC(10,2))
- reservations (id INT PRIMARY KEY, restaurant_id INT REFERENCES restaurants(id), customer_id UUID REFERENCES customers(id), reserved_for TIMESTAMP, party_size INT, status VARCHAR(30), notes TEXT)

Enums:
- OrderStatus: 'pending', 'confirmed', 'preparing', 'served', 'completed', 'cancelled'
- ReservationStatus: 'booked', 'seated', 'completed', 'cancelled'

Task:
Analyze the user's message.
- When a user asks to see a café's menu (for example, "show me the menu from Komo By Kaffa"), ALWAYS return a non-null SQL SELECT query. Use `SELECT cafe, menu_item, price_inr FROM restaurants WHERE cafe ILIKE '%<cafe name>%' ORDER BY menu_item LIMIT 50`.
- When a user asks for the top cafés, ALWAYS return a non-null SQL SELECT query such as `SELECT cafe, location, rating_out_of_5, approx_cost_for_two_inr FROM restaurants ORDER BY rating_out_of_5 DESC NULLS LAST LIMIT 5`.
- When a user asks what to eat, use their chat preferences. If they have not provided useful preferences, ask concise follow-up questions about dietary requirements, preferred cuisine, and budget. If preferences are available, generate a SELECT query for matching menu items and recommend only from its results.
- Update ALL relevant tables based on the user query.
- When ordering food:
  1. Insert/Ensure customer exists in `customers` table if customer name/details are provided.
  2. Insert into `orders` table.
  3. Insert into `order_items` table with item details.
  4. Treat the full conversation as one request. For example, if a user first
     specifies an item, quantity, and café, then provides their name, email, or
     phone number in a later message, use both messages to create the order.
  5. Create the order with status `pending` as soon as the item and customer
     details are available, even when no delivery destination has been given.
     Ask for the destination only after creating that pending order.
- When booking a table / reservation:
  1. Insert/Ensure customer exists in `customers` table if customer details are provided.
  2. Insert into `reservations` table setting `status = 'booked'`.
- When finalizing/booking an order (changing status to placed/confirmed):
  This applies only to an already-created pending order. It does NOT apply when
  the user is still supplying details needed to create a new order.
  1. FIRST, check if the user has provided a delivery destination in the conversation.
  2. If NO destination is provided, do NOT generate SQL. Return `{"sql": null, "explanation": "Please provide your delivery destination before we can book your order."}`
  3. If a destination IS provided, return `{"sql": null, "explanation": "I have the destination. I will calculate a delivery estimate using the café location."}`. The application calculates and stores the ETA after retrieving the café location.

Return JSON ONLY:
{"sql": "<PostgreSQL query>", "explanation": "<brief rationale>"}

- If the user is just saying hello or asking a general non-database question, return JSON ONLY:
{"sql": null, "explanation": "<friendly greeting or conversation>"}

When asked to write SQL for a specific internal application task (rather than
directly answering a user's chat message), the same rules above still apply:
use CTEs for multi-table writes, never use placeholders, resolve foreign keys
via subqueries, and follow any column-aliasing instructions in the task
exactly, since the application code reads the result by those alias names.

CRITICAL RULES:
- ALWAYS use CTEs (WITH clause) when inserting across multiple tables (e.g. customers, orders, order_items, reservations) so ALL affected tables are updated together.
- When creating a new order, the CTE's INSERT INTO orders statement MUST end with `RETURNING id`. Then add a final `SELECT id AS order_id FROM new_order` at the end of the CTE so the order ID is returned.
- Example CTE tail for orders:
  ```
  new_order AS (INSERT INTO orders (...) VALUES (...) RETURNING id)
  SELECT id AS order_id FROM new_order;
  -- then separately insert order_items in the same CTE
  ```
  Actually, structure it so the final statement of the CTE is `SELECT id AS order_id FROM new_order`.
- NEVER use parameter placeholders such as $1, $2, ?, or :param. Write literal values or SQL subqueries directly.
- Do NOT provide explicit 'id' PRIMARY KEY values if they are auto-incrementing / defaulted with uuid/sequence.
- For foreign keys:
  * customer_id: use subqueries or CTE references (e.g., `(SELECT id FROM customers WHERE full_name ILIKE '%<name>%' LIMIT 1)` or from CTE).
  * restaurant_id: use subqueries (e.g., `(SELECT id FROM restaurants WHERE cafe ILIKE '%<cafe_name>%' LIMIT 1)`).
- NEVER use `ON CONFLICT (full_name)` for the customers table since `full_name` is not unique. To ensure a customer exists, either use a normal `INSERT` or a CTE with `WHERE NOT EXISTS (SELECT 1 FROM customers WHERE full_name ILIKE ...)`.
- When booking a table, reservation status MUST be set to 'booked'.
- Generate valid SELECT, INSERT, UPDATE, or DELETE statements only. Never generate DROP or ALTER statements.
- For list SELECT queries without a specific limit, use LIMIT 10, EXCEPT when querying for a cafe's menu.
- Note on menus: The `restaurants` table is denormalized and stores ONE ROW PER MENU ITEM. To get the full menu for a cafe, query all rows for that cafe (e.g. `SELECT menu_item, price_inr FROM restaurants WHERE cafe ILIKE '%<name>%' LIMIT 50`).
- Return ONLY JSON string, no markdown wrapping, no extra text, and NO <think> tags or reasoning steps.
"""

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


class ChatService:
    def __init__(self, db: Session):
        self.db = db
        settings = get_settings()
        self.openrouter_api_key = (os.getenv("OPENROUTER_AI_API_KEY")
            or settings.openrouter_api_key
        )
        self.openrouter_model = os.getenv("OPENROUTER_MODEL") or settings.openrouter_model
        print(f"==>> ChatService init: api_key_set={bool(self.openrouter_api_key)} model={self.openrouter_model!r}")

    # ------------------------------------------------------------------
    # LLM-authored SQL helpers
    #
    # Nothing in this service builds INSERT/UPDATE/SELECT statements (or
    # SQLAlchemy ORM objects) by hand anymore. Every database read or write
    # is produced by asking the LLM to write the SQL for a specific task
    # (using the same schema + rules as the general chat path), which is
    # then executed with a plain text() call. Python only supplies the
    # values that go into the task description and reads back rows using
    # column aliases it explicitly told the LLM to use.
    # ------------------------------------------------------------------

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
        raw = _clean_content(self._call_llm([
            {"role": "system", "content": DB_SCHEMA_PROMPT},
            {"role": "user", "content": task},
        ]))
        parsed = _parse_json_from_llm(raw)
        return parsed.get("sql"), parsed.get("explanation", "")

    def _run_sql(self, sql: str) -> list[dict]:
        """Execute a single LLM-authored SQL statement and return its rows
        (empty list for statements with no result set). Mutating statements
        are committed; raises on failure so the caller can retry/handle it."""
        upper_sql = sql.strip().upper()
        is_select_type = upper_sql.startswith("SELECT") or (
            upper_sql.startswith("WITH")
            and "RETURNING" not in upper_sql
            and not any(f" {kw} " in f" {upper_sql} " for kw in ("INSERT", "UPDATE", "DELETE"))
        )
        result = self.db.execute(text(sql))
        try:
            rows = [dict(zip(result.keys(), row)) for row in result.fetchall()]
        except Exception:
            rows = []
        if not is_select_type:
            self.db.commit()
        return rows

    def _generate_and_run(self, task: str) -> list[dict] | None:
        """Generate SQL for `task` and execute it. On failure, feeds the
        error back to the LLM once for a fix before giving up. Returns None
        only when no SQL could be produced/executed at all."""
        sql, _ = self._generate_sql(task)
        if not sql:
            logger.warning("[_generate_and_run] LLM returned no SQL for task: %s", task)
            return None
        try:
            return self._run_sql(sql)
        except Exception as err:
            self.db.rollback()
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
                return self._run_sql(sql2)
            except Exception as err2:
                self.db.rollback()
                logger.error("[_generate_and_run] Retry also failed: %s", err2)
                return None

    def _order_items(self, order_id: int) -> list[dict]:
        task = f"""
Write a SELECT that returns every row in order_items belonging to the order
with id = {order_id}.
Alias the returned columns EXACTLY as: item_name, quantity, unit_price.
"""
        return self._generate_and_run(task) or []

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
        prompt = f"""\
You are analysing a restaurant-ordering conversation.

Previous turns:
{recent_turns}

New user message:
{message}

Answer with JSON only: {{"is_contact_follow_up": true}} if the new message
supplies name, email, or phone number to complete a food order the user
started in a prior turn, otherwise {{"is_contact_follow_up": false}}.
Do NOT explain. Return JSON only."""
        try:
            raw = _clean_content(self._call_llm([
                {"role": "system", "content": "You classify restaurant conversation intent. Return JSON only, no markdown."},
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
        prompt = (
            f"Conversation History:\n{recent_turns}\n\n"
            f"Latest User Message: \"{message}\"\n"
            f"Active Order ID in Session: {session_order_id}\n\n"
            "Analyze the latest user message and classify its intent as EXACTLY one of the following labels:\n"
            "- contact_followup : User provides personal contact details (such as name, email, phone number) to create or complete an order.\n"
            "- confirm_order    : User says confirm, approve, proceed, or wants to finalize a pending order.\n"
            "- delivery_status  : User asks about delivery ETA, timing, time left, time remaining, arrival time, or order status.\n"
            "- give_destination : User provides a delivery address, location, area, or destination.\n"
            "- add_item         : User requests to add more food items or drinks to an existing order.\n"
            "- general          : Any general inquiry, menu browse, recommendation request, or uncategorized message.\n\n"
            "Respond ONLY with a JSON object: {\"intent\": \"<label>\"}"
        )
        try:
            raw = _clean_content(self._call_llm([
                {"role": "system", "content": "You are a precise intent classifier for a restaurant ordering system. Output pure JSON only."},
                {"role": "user", "content": prompt},
            ], max_tokens=50))
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

    def _call_llm(self, messages: list[dict], max_tokens: int = 1200) -> str:
        print(f"==>> _call_llm: called")
        if not self.openrouter_api_key:
            raise RuntimeError("OPENROUTER_AI_API_KEY is missing from environment variables.")

        try:
            with httpx.Client(timeout=20.0) as client:
                response = client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self.openrouter_api_key}", "Content-Type": "application/json", "X-OpenRouter-Title": "Restros"},
                    json={
                        "model": self.openrouter_model,
                        "messages": messages,
                        "temperature": 0.1,
                        "max_tokens": max_tokens,
                        # Reasoning models (e.g. gpt-oss) spend tokens on hidden
                        # "thinking" before writing the visible answer. Without
                        # capping/excluding that, a modest max_tokens gets fully
                        # consumed by reasoning and `content` comes back empty
                        # or None - this keeps tokens available for the actual
                        # answer. Harmless no-op for non-reasoning models.
                        "reasoning": {"effort": "low", "exclude": True},
                    },
                )
            if response.status_code == 200:
                message = response.json()["choices"][0]["message"]
                content = message.get("content") or message.get("reasoning") or ""
                return _clean_content(content)
            error = f"OpenRouter API Error {response.status_code}: {response.text}"
            print(f"==>> _call_llm: {error}")
            logger.warning("OpenRouter model %s failed: %s", self.openrouter_model, error)
            raise RuntimeError(error)
        except httpx.HTTPError as error:
            print(f"==>> _call_llm: HTTP error: {error}")
            logger.warning("OpenRouter request failed: %s", error)
            raise RuntimeError(f"OpenRouter request failed: {error}") from error

    def _execute_sql(self, sql_query: str, user_message: str, retried: bool = False) -> dict:
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
                rows = self._run_sql(sql_query)

                synthesis_prompt = f"""
The user asked: "{user_message}"
Query Result Data ({len(rows)} rows):
{json.dumps(rows, default=str)}
CURRENT SYSTEM TIME: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

Synthesize a clear, friendly, and well-formatted response for the user based strictly on this data.
CRITICAL INSTRUCTION: Do NOT include SQL queries, reasoning steps, code blocks, or mentions of database tables in your final answer. Provide only the direct answer to the user's request. Never invent a fixed delivery window or ETA; delivery timing is calculated separately from the saved destination and the order's stored ETA.
"""
                synth_messages = [
                    {"role": "system", "content": "You are a helpful restaurant database assistant. Provide clear summaries of data results."},
                    {"role": "user", "content": synthesis_prompt}
                ]
                final_response = _clean_content(self._call_llm(synth_messages))
                final_response = re.sub(r"```.*?```", "", final_response, flags=re.DOTALL).strip()

                return {
                    "response": final_response,
                    "sql_query": sql_query
                }
            else:
                returned_rows = self._run_sql(sql_query)
                rowcount = len(returned_rows)

                returned_info = ""
                if returned_rows:
                    returned_info = f"\nReturned data from database: {json.dumps(returned_rows, default=str)}"

                synthesis_prompt = f"""
The user requested: "{user_message}"
Rows affected: {rowcount}{returned_info}

Write a SHORT, FRIENDLY confirmation message (2-4 sentences max). 
If the user requested food, ask them to provide their delivery destination so the order can be confirmed and a destination-specific ETA can be calculated. Do NOT reveal or display any order number yet (order numbers are displayed only after confirmation).
STRICTLY FORBIDDEN in your response:
- Any SQL code or queries
- Table names (customers, orders, order_items, etc.)
- Column names or technical field names
- Phrases like "behind the scenes", "database", "record was inserted", "rows affected"
- Any internal implementation details
Just confirm the action, show the order number if available, and ask if they need anything else.
"""
                synth_messages = [
                    {"role": "system", "content": "You are a helpful restaurant database assistant confirming data operations."},
                    {"role": "user", "content": synthesis_prompt}
                ]
                final_response = _clean_content(self._call_llm(synth_messages))
                final_response = re.sub(r"```.*?```", "", final_response, flags=re.DOTALL).strip()

                return {
                    "response": final_response,
                    "sql_query": sql_query
                }

        except Exception as err:
            self.db.rollback()
            if not retried:
                fix_prompt = f"""
The SQL query you generated:
{sql_query}

Failed with database error:
{str(err)}

Please fix the SQL query. Remember: DO NOT use placeholders like $1 or $2 or ?. Write literal values directly in the SQL.
Return JSON ONLY:
{{"sql": "<corrected SQL>", "explanation": "<reason>"}}
"""
                try:
                    retry_output = self._call_llm([
                        {"role": "system", "content": DB_SCHEMA_PROMPT},
                        {"role": "user", "content": fix_prompt}
                    ])
                    clean_retry = re.sub(r"^```(json)?|```$", "", retry_output.strip(), flags=re.MULTILINE).strip()
                    parsed_retry = json.loads(clean_retry)
                    new_sql = parsed_retry.get("sql")
                    if new_sql:
                        return self._execute_sql(new_sql, user_message, retried=True)
                except Exception:
                    pass

            error_synth_prompt = f"""
The user requested: "{user_message}"
The database operation failed with error: {str(err)}

Briefly explain to the user why the operation failed in friendly terms.
CRITICAL INSTRUCTION: Do NOT include raw SQL, code blocks, or technical tracebacks.
"""
            try:
                friendly_error = self._call_llm([
                    {"role": "system", "content": "You are a helpful restaurant database assistant."},
                    {"role": "user", "content": error_synth_prompt}
                ])
            except Exception:
                friendly_error = "I couldn't complete the request because the requested restaurant or customer details could not be found."

            return {
                "response": friendly_error,
                "sql_query": sql_query
            }

    def _create_order_from_llm(self, history: InMemoryChatMessageHistory, message: str, session_id: str = "default_session") -> dict | None:
        logger.info("[_create_order_from_llm] Called for session_id=%s, message=%r", session_id, message)
        print(f"==>> _create_order_from_llm: called")
        """Let the LLM resolve customer + order details from the conversation,
        then let the LLM write the SQL that actually creates them."""
        conversation = "\n".join(
            f"{'User' if item.type == 'human' else 'Assistant'}: {item.content}"
            for item in history.messages[-12:]
        )
        extraction_prompt = f"""
Extract customer details and ALL ordered food items from this conversation.
Return JSON only with this exact shape:
{{
  "customer": {{"full_name": "...", "email": "...", "phone": "..."}},
  "order": {{
    "cafe": "...",
    "items": [
      {{"menu_item": "...", "quantity": 1}}
    ]
  }}
}}

Use null for unknown customer/cafe fields. If no food items are specified, set items to []. Do not return SQL or prose.

Conversation:
{conversation}
User: {message}
"""
        try:
            raw = _clean_content(self._call_llm([
                {"role": "system", "content": "You extract restaurant order data accurately. Output JSON only, no markdown."},
                {"role": "user", "content": extraction_prompt},
            ], max_tokens=250))
            logger.info("[_create_order_from_llm] Raw LLM output: %r", raw)
            extracted = _parse_json_from_llm(raw)
            logger.info("[_create_order_from_llm] Parsed JSON: %s", extracted)

            customer_data = extracted.get("customer") or {}
            order_data = extracted.get("order") or {}

            full_name = customer_data.get("full_name")
            email = customer_data.get("email")
            phone = customer_data.get("phone")
            cafe = order_data.get("cafe")
            raw_items = order_data.get("items") or []

            if not raw_items and order_data.get("menu_item"):
                raw_items = [{"menu_item": order_data.get("menu_item"), "quantity": order_data.get("quantity") or 1}]

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

        task = f"""
Create a new pending order for this customer and these items.

Customer full name: {self._sql_escape(full_name)}
Customer email: {self._sql_escape(email)}
Customer phone: {self._sql_escape(phone)}
Café: {self._sql_escape(cafe) if cafe and isinstance(cafe, str) and cafe.strip() else "not specified — infer the single café whose menu best matches the items requested"}
Items requested (quantity x menu item, as the user described it): {items_desc}

Write a CTE that:
1. Ensures the customer exists (do not use ON CONFLICT on full_name).
2. For each requested item, resolves the exact menu_item name and price_inr
   from restaurants by matching the café and using ILIKE against the
   requested item text.
3. Inserts the order into orders with status 'pending', the resolved
   restaurant_id, the customer's id, and total_amount equal to the sum of
   (unit price x quantity) across all resolved items.
4. Inserts one row per resolved item into order_items with the resolved
   item_name, quantity, and unit_price.
If an item cannot be confidently matched to any café menu, skip that item
rather than failing the whole order.
The final statement of the CTE must be a SELECT returning the new order's id
and the customer's full name, aliased EXACTLY as: order_id, customer_name.
"""
        rows = self._generate_and_run(task)
        if not rows or not rows[0].get("order_id"):
            logger.warning("[_create_order_from_llm] LLM SQL did not resolve items / create an order.")
            name_str = f", {full_name.strip()}" if full_name else ""
            return {
                "response": f"Thanks{name_str}! Which café would you like to order from, and what menu items would you like?",
                "sql_query": None,
            }

        order_id = rows[0]["order_id"]
        customer_name = rows[0].get("customer_name") or full_name

        _session_orders[session_id] = order_id
        logger.info("[_create_order_from_llm] Successfully created Order #%s for customer %s (session_id=%s)", order_id, customer_name, session_id)
        return {
            "response": (
                f"Thanks, {customer_name}! 🛵 Please share your **delivery destination** (area/address in Ahmedabad) "
                "so I can prepare your order summary and calculate a delivery estimate for you."
            ),
            "sql_query": None,
        }

    def _add_item_from_llm(self, history: InMemoryChatMessageHistory, message: str, session_order_id: int | None) -> dict | None:
        print(f"==>> _add_item_from_llm: called")
        """Use the LLM to identify the item change, then let the LLM write the
        SQL that inserts the item and updates the order's total."""
        prompt = f"""
Extract an item addition from the user's restaurant-order conversation. Return JSON only:
{{"order_id": <integer or null>, "menu_item": "...", "quantity": <integer>}}
Use the session order number {session_order_id!r} when the user does not specify one.
User request: {message}
"""
        try:
            raw = _clean_content(self._call_llm([
                {"role": "system", "content": "You extract order modifications accurately. Return JSON only, no markdown."},
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

        task = f"""
The user wants to add an item to an existing order.
order id: {order_id}
menu item requested: {self._sql_escape(menu_item.strip())}
quantity: {quantity}

Write a CTE that:
1. Looks up the restaurant this order belongs to via orders.restaurant_id.
2. Finds the matching row in restaurants for that same café where
   menu_item ILIKE a pattern built from the requested item text, to get its
   exact menu_item name and price_inr.
3. Inserts one row into order_items for this order with that item_name,
   quantity {quantity}, and the resolved unit_price.
4. Updates orders.total_amount for this order id by adding
   (resolved unit_price x {quantity}) to the existing total_amount.
The final statement must be a SELECT returning the resolved item name and
unit price, aliased EXACTLY as: item_name, unit_price.
"""
        rows = self._generate_and_run(task)
        if not rows:
            return None
        item_name = rows[0].get("item_name")
        unit_price = rows[0].get("unit_price")
        if not item_name or unit_price is None:
            return None

        total_rows = self._generate_and_run(f"""
Write a SELECT that returns total_amount for the orders row with id = {order_id}.
Alias the column EXACTLY as: total_amount.
""")
        new_total = total_rows[0]["total_amount"] if total_rows else None

        return {
            "response": (
                f"Added {quantity} {item_name} to your order."
                + (f" Your updated total is ₹{new_total}." if new_total is not None else "")
            ),
            "sql_query": None,
        }

    def _latest_active_order(self, order_id: int | None = None) -> dict | None:
        print(f"==>> _latest_active_order: called")
        """Return the latest order (joined with its restaurant and, if any,
        its customer) that can still be delivered in this demo app, via
        LLM-authored SQL."""
        scope = (
            f"Restrict to the order with id = {order_id}."
            if order_id
            else "Do not filter by id; return only the single most recently created such order."
        )
        task = f"""
Write a SELECT joining orders to restaurants (and LEFT JOIN customers on
orders.customer_id, since customer_id may be null) that returns the latest
order whose status is 'pending', 'confirmed', or 'preparing'.
{scope}
Alias the returned columns EXACTLY as: order_id, order_status, total_amount,
delivery_destination, estimated_delivery_minutes, confirmed_at,
restaurant_id, cafe, location, customer_id, customer_name, customer_email,
customer_phone.
Limit the result to 1 row.
"""
        rows = self._generate_and_run(task)
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
        prompt = f"""
Extract the customer's contact details from this restaurant-ordering
conversation. These are sometimes split across messages, so consider the
recent turns together with the new message.

Recent turns:
{recent_turns}

New user message:
{message}

Return JSON only: {{"full_name": "...", "email": "...", "phone": "..."}}
Use null for any field that truly isn't present. Do not invent values.
"""
        try:
            raw = _clean_content(self._call_llm([
                {"role": "system", "content": "You extract contact details accurately. Return JSON only, no markdown."},
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

    def _attach_contact_to_order(self, order_id: int, full_name: str, email: str, phone: str) -> str | None:
        print(f"==>> _attach_contact_to_order: called")
        """Ensure a customer with these details exists and link them to an
        already-created order, via LLM-authored SQL. Returns the customer's
        name on success."""
        task = f"""
Attach a customer to an existing order.
order id: {order_id}
Customer full name: {self._sql_escape(full_name)}
Customer email: {self._sql_escape(email)}
Customer phone: {self._sql_escape(phone)}

Write a CTE that:
1. Ensures a customer with this email or phone exists — if one already
   matches by email or phone, update their full_name/email/phone to these
   values; otherwise insert a new customer row. Do NOT use ON CONFLICT on
   full_name, since it is not unique.
2. Updates the orders row with id = {order_id} to set its customer_id to
   that customer's id.
The final statement must be a SELECT returning the customer's full name,
aliased EXACTLY as: customer_name.
"""
        rows = self._generate_and_run(task)
        if not rows:
            return None
        return rows[0].get("customer_name") or full_name

    def _order_summary_response(self, active_order: dict, prefix: str = "") -> str:
        print(f"==>> _order_summary_response: called")
        items = self._order_items(active_order["order_id"])
        bill_lines = [
            f"  • {i['item_name']} × {i['quantity']} — ₹{float(i['unit_price']) * i['quantity']:.2f}"
            for i in items
        ]
        bill_text = "\n".join(bill_lines) if bill_lines else "  (no items found)"
        eta = active_order.get("estimated_delivery_minutes")
        return (
            f"{prefix}"
            f"🧾 **Order Summary** (from {active_order['cafe']}):"
            f"\n{bill_text}"
            f"\n\n💰 **Total: ₹{float(active_order['total_amount']):.2f}**"
            + (f"\n⏱ Estimated delivery time: **~{eta} minutes**" if eta else "")
            + "\n\nPlease type **CONFIRM** to place your order, or let me know if you would like to modify any items."
        )

    def _estimate_delivery_minutes(self, cafe_location: str, destination: str) -> int:
        """Ask the LLM to estimate delivery time in minutes from the café
        location to the customer's destination.

        This is done as two separate LLM calls to avoid the fragility of
        parsing a single strict-JSON response 

          1. Ask the model, in plain text, to reason about where the two
             places are relative to each other in Ahmedabad and give a
             rough road distance in km. This has no output-format
             constraints, so it can't fail to parse.
          2. Feed that reasoning back and ask for a single integer
             (15-90) as the final ETA. We pull the first integer out of
             the response with a regex, so extra words, punctuation, or
             formatting around the number don't break it.

        Only if the model genuinely returns no usable number after a retry
        does this fall back to a fixed default — and that fallback, plus
        every raw response along the way, is logged so failures are
        visible instead of silent.
        """
        print("==>> _estimate_delivery_minutes: called")

        # Step 1: free-text geographic reasoning — nothing to parse, so it can't fail.
        reasoning_prompt = f"""
You are a local delivery-logistics expert for Ahmedabad, India.

Café location: {cafe_location}
Customer delivery destination: {destination}

In 2-3 sentences, describe:
- Roughly where these two places are relative to each other in Ahmedabad (same area, neighbouring areas, opposite sides of the city, etc.)
- A realistic road distance between them in kilometers
- Typical traffic/road conditions on that route

Be concise and specific. Do not mention delivery time yet.
"""
        try:
            reasoning = _clean_content(self._call_llm([
                {"role": "system", "content": "You are a knowledgeable local Ahmedabad logistics expert."},
                {"role": "user", "content": reasoning_prompt},
            ], max_tokens=400))
            logger.info(
                "[_estimate_delivery_minutes] Distance reasoning for cafe=%r dest=%r: %r",
                cafe_location, destination, reasoning,
            )
        except Exception as err:
            print(f"==>> [_estimate_delivery_minutes] REASONING CALL FAILED: {type(err).__name__}: {err}")
            logger.warning(
                "[_estimate_delivery_minutes] Reasoning step failed for cafe=%r dest=%r: %s: %s",
                cafe_location, destination, type(err).__name__, err,
            )
            reasoning = f"Café at {cafe_location}, destination at {destination}, both within Ahmedabad."

        # Step 2: turn that reasoning into a single number, extracted with a regex
        # so any stray words/punctuation around it don't break parsing.
        number_prompt = f"""
Based on this route assessment for a food delivery in Ahmedabad:

"{reasoning}"

Add about 10-15 minutes for kitchen preparation and rider dispatch on top of the travel time.

Respond with ONLY a single whole number between 15 and 90 representing the total estimated delivery time in minutes. No words, no units, no punctuation - just the number.
"""
        for attempt in (1, 2):
            try:
                raw = _clean_content(self._call_llm([
                    {"role": "system", "content": "You output a single integer and nothing else."},
                    {"role": "user", "content": number_prompt},
                ], max_tokens=100))
                match = re.search(r"\d+", raw)
                if not match:
                    raise ValueError(f"No integer found in response: {raw!r}")
                eta = max(15, min(90, int(match.group())))
                logger.info(
                    "[_estimate_delivery_minutes] cafe=%r dest=%r eta_min=%d (attempt %d, raw=%r)",
                    cafe_location, destination, eta, attempt, raw,
                )
                return eta
            except Exception as err:
                print(f"==>> [_estimate_delivery_minutes] NUMBER CALL attempt {attempt} FAILED: {type(err).__name__}: {err}")
                logger.warning(
                    "[_estimate_delivery_minutes] Attempt %d failed to extract ETA for cafe=%r dest=%r: %s: %s",
                    attempt, cafe_location, destination, type(err).__name__, err,
                )

        logger.error(
            "[_estimate_delivery_minutes] Giving up after 2 attempts for cafe=%r dest=%r; using default ETA.",
            cafe_location, destination,
        )
        return 45

    def _save_destination(self, destination: str, order_id: int | None = None) -> dict:
        print(f"==>> _save_destination: called")
        active_order = self._latest_active_order(order_id)
        if not active_order:
            return {
                "response": "I couldn't find an active order to deliver. Please select your café and menu items first.",
                "sql_query": None,
            }

        eta_minutes = self._estimate_delivery_minutes(active_order["location"], destination)

        update_task = f"""
Write an UPDATE that sets delivery_destination = '{self._sql_escape(destination)}' and
estimated_delivery_minutes = {eta_minutes} on the orders row with id = {active_order['order_id']}.
"""
        if self._generate_and_run(update_task) is None:
            return {
                "response": "I had trouble saving your delivery destination. Could you try again?",
                "sql_query": None,
            }

        # Build itemized bill to show before asking for confirmation
        items = self._order_items(active_order["order_id"])
        bill_lines = [
            f"  • {i['item_name']} × {i['quantity']} — ₹{float(i['unit_price']) * i['quantity']:.2f}"
            for i in items
        ]
        bill_text = "\n".join(bill_lines) if bill_lines else "  (no items found)"

        response = (
            f"📍 Delivery destination set to **{destination}**."
            f"\n\n🧾 **Order Summary** (from {active_order['cafe']}):"
            f"\n{bill_text}"
            f"\n\n💰 **Total: ₹{float(active_order['total_amount']):.2f}**"
            f"\n⏱ Estimated delivery time: **~{eta_minutes} minutes**"
            f"\n\nPlease type **CONFIRM** to place your order, or let me know if you would like to modify any items."
        )
        return {
            "response": response,
            "sql_query": None,
        }

    def _confirm_latest_order(self, order_id: int | None = None) -> dict:
        print(f"==>> _confirm_latest_order: called")
        active_order = self._latest_active_order(order_id)
        if not active_order:
            return {"response": "I couldn't find an active order to confirm.", "sql_query": None}

        if not active_order.get("delivery_destination"):
            return {
                "response": "Please provide your delivery destination before confirming the order.",
                "sql_query": None,
            }

        if not (active_order.get("customer_name") and active_order.get("customer_email") and active_order.get("customer_phone")):
            return {
                "response": "Before I can confirm your order, could you share your full name, email, and phone number?",
                "sql_query": None,
            }

        eta_minutes = active_order.get("estimated_delivery_minutes")
        if not eta_minutes:
            eta_minutes = self._estimate_delivery_minutes(active_order["location"], active_order["delivery_destination"])

        update_task = f"""
Write an UPDATE that sets status = 'confirmed', confirmed_at = NOW(), and
estimated_delivery_minutes = {eta_minutes} on the orders row with id = {active_order['order_id']}.
"""
        if self._generate_and_run(update_task) is None:
            return {"response": "I had trouble confirming your order. Could you try again?", "sql_query": None}

        items = self._order_items(active_order["order_id"])
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

    def _delivery_status(self, order_id: int | None = None, message: str = "") -> dict:
        print(f"==>> _delivery_status: called")
        logger.info("[_delivery_status] Called with order_id=%s, message=%r", order_id, message)

        active_order = self._latest_active_order(order_id)
        if not active_order:
            if order_id:
                rows = self._generate_and_run(f"""
Write a SELECT that returns status and delivery_destination for the orders
row with id = {order_id}.
Alias the returned columns EXACTLY as: order_status, delivery_destination.
""")
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
            self._generate_and_run(f"""
Write an UPDATE that sets status = 'completed' on the orders row with id = {order_id}.
""")
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

    def process_query(self, user_message: str, session_id: str = "default_session") -> dict:
        print(f"==>> process_query: called, session_id={session_id}")
        logger.info("[PROCESS_QUERY] Called with session_id=%s, user_message=%r", session_id, user_message)
        user_message = user_message.replace(r"\@", "@")
        history = get_session_history(session_id)

        session_order_id = _session_orders.get(session_id) or _session_order_id(history)
        logger.info("[PROCESS_QUERY] Resolved session_order_id=%s for session_id=%s", session_order_id, session_id)

        # ── Single LLM intent classifier — no more string-matching on AI messages ──
        intent = self._classify_intent(history, user_message, session_order_id)
        logger.info("[PROCESS_QUERY] Intent classified as '%s' (session_order_id=%s)", intent, session_order_id)
        print(f"==>> intent: {intent}  order_id: {session_order_id}")

        result: dict | None = None

        # 1. User is giving a delivery address for an existing order
        if intent == "give_destination":
            result = self._save_destination(user_message.strip(), session_order_id)

        # 2. User is confirming the order
        elif intent == "confirm_order":
            result = self._confirm_latest_order(session_order_id)

        # 3. User wants delivery ETA / time left
        elif intent == "delivery_status":
            result = self._delivery_status(session_order_id, user_message)

        # 4. User wants to add more items to an existing order
        elif intent == "add_item" and session_order_id:
            result = self._add_item_from_llm(history, user_message, session_order_id)

        # 5. User is providing contact details to complete an order
        elif intent == "contact_followup":
            if session_order_id:
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
                    customer_name = self._attach_contact_to_order(session_order_id, **contact)
                    if not customer_name:
                        result = {
                            "response": "I had trouble saving your contact details. Could you share your name, email, and phone number again?",
                            "sql_query": None,
                        }
                    else:
                        order_pair = self._latest_active_order(session_order_id)
                        if order_pair and order_pair.get("delivery_destination"):
                            result = {
                                "response": self._order_summary_response(order_pair, prefix=f"Thanks, {customer_name}! "),
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
                result = self._create_order_from_llm(history, user_message, session_id)
                print("result:", result)
                if not result:
                    result = {
                        "response": "Thank you for sharing your contact details! Which café and menu items would you like to order?",
                        "sql_query": None,
                    }

        # 6. Active pending order missing destination: process address unless it's a menu inquiry
        elif session_order_id:
            order_pair = self._latest_active_order(session_order_id)
            if order_pair and order_pair.get("order_status") == "pending" and not order_pair.get("delivery_destination"):
                if not any(w in user_message.lower() for w in ["menu", "top cafe", "show cafe", "what is", "help me", "recommend"]):
                    result = self._save_destination(user_message.strip(), session_order_id)

        # ── If a structured handler produced a result, return it ──
        if result:
            logger.info("[PROCESS_QUERY] Handler produced response: %r", result["response"])
            history.add_user_message(user_message)
            history.add_ai_message(result["response"])
            return result

        # ── General path: ask the DB-schema LLM ──
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        dynamic_prompt = DB_SCHEMA_PROMPT + f"\n\nCURRENT SYSTEM TIME: {current_time}\n"

        messages = [{"role": "system", "content": dynamic_prompt}]
        for msg in history.messages[-8:]:
            role = "user" if msg.type == "human" else "assistant"
            messages.append({"role": role, "content": msg.content})
        messages.append({"role": "user", "content": user_message})

        try:
            llm_output = self._call_llm(messages)
            logger.info("[GENERAL_PATH] Raw LLM output: %r", llm_output)
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
                if (explanation and isinstance(explanation, str) and explanation.strip())
                else "Could you please specify the café name, menu item, or question you would like assistance with?"
            )
            logger.info("[GENERAL_PATH] No SQL query found. Returning final_response: %r", final_response)
            history.add_user_message(user_message)
            history.add_ai_message(final_response)
            return {"response": final_response, "sql_query": None}

        final_result = self._execute_sql(sql_query, user_message)
        logger.info("[GENERAL_PATH] Executed SQL result: %r", final_result)
        history.add_user_message(user_message)
        history.add_ai_message(final_result["response"])
        return final_result