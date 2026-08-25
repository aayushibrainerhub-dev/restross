import json
import logging
import os
import re
import httpx
from datetime import datetime
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.db.models.restaurant import Customer, Order, OrderItem, OrderStatus, Restaurant
import dotenv
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

def _clean_content(content: str) -> str:
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL | re.IGNORECASE)
    content = re.sub(r"<think>.*", "", content, flags=re.DOTALL | re.IGNORECASE)
    return content.strip()

from langchain_core.chat_history import InMemoryChatMessageHistory

chat_sessions: dict[str, InMemoryChatMessageHistory] = {}

def get_session_history(session_id: str) -> InMemoryChatMessageHistory:
    if session_id not in chat_sessions:
        chat_sessions[session_id] = InMemoryChatMessageHistory()
    return chat_sessions[session_id]


def _is_order_details_follow_up(history: InMemoryChatMessageHistory, message: str) -> bool:
    """Identify contact details that complete an order from an earlier turn."""
    prior_user_messages = [
        str(item.content) for item in history.messages if item.type == "human"
    ]
    has_order_context = any(
        re.search(r"\b(?:order|want|would like|get)\b.*\b(?:from|at)\b", item, re.IGNORECASE)
        for item in prior_user_messages
    )
    has_contact_details = bool(re.search(
        r"(?:\b\d{10}\b|[\w.+-]+@\w[\w.-]*\.[A-Za-z]{2,}|\bmy name is\b)",
        message,
        re.IGNORECASE,
    ))
    return has_order_context and has_contact_details


def _session_order_id(history: InMemoryChatMessageHistory) -> int | None:
    """Return the most recently created order number acknowledged in this session."""
    for message in reversed(history.messages):
        if message.type != "ai":
            continue
        match = re.search(r"(?:order number is|order)\s*#(\d+)", str(message.content), re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None

class ChatService:
    def __init__(self, db: Session):
        self.db = db
        settings = get_settings()
        self.openrouter_api_key = (os.getenv("OPENROUTER_AI_API_KEY")
            or settings.openrouter_api_key
        )
        self.openrouter_model = os.getenv("OPENROUTER_MODEL") or settings.openrouter_model

    def _call_llm(self, messages: list[dict], max_tokens: int = 1200) -> str:
        if not self.openrouter_api_key:
            raise RuntimeError("OPENROUTER_AI_API_KEY is missing from environment variables.")

        try:
            with httpx.Client(timeout=20.0) as client:
                response = client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self.openrouter_api_key}", "Content-Type": "application/json", "X-OpenRouter-Title": "Restros"},
                    json={"model": self.openrouter_model, "messages": messages, "temperature": 0.1, "max_tokens": max_tokens},
                )
            if response.status_code == 200:
                return _clean_content(response.json()["choices"][0]["message"]["content"])
            error = f"OpenRouter API Error {response.status_code}: {response.text}"
            logger.warning("OpenRouter model %s failed: %s", self.openrouter_model, error)
            raise RuntimeError(error)
        except httpx.HTTPError as error:
            logger.warning("OpenRouter request failed: %s", error)
            raise RuntimeError(f"OpenRouter request failed: {error}") from error

    def _execute_sql(self, sql_query: str, user_message: str, retried: bool = False) -> dict:
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
                result = self.db.execute(text(sql_query))
                keys = result.keys()
                rows = [dict(zip(keys, row)) for row in result.fetchall()]
                
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
                result = self.db.execute(text(sql_query))
                
                try:
                    returned_rows = [dict(zip(result.keys(), row)) for row in result.fetchall()]
                except Exception:
                    returned_rows = []

                self.db.commit()
                rowcount = result.rowcount

                returned_info = ""
                if returned_rows:
                    returned_info = f"\nReturned data from database: {json.dumps(returned_rows, default=str)}"

                synthesis_prompt = f"""
The user requested: "{user_message}"
Rows affected: {rowcount}{returned_info}

Write a SHORT, FRIENDLY confirmation message (2-4 sentences max). 
If an order_id was returned, show it prominently as "Your Order Number is #<id>".
If the user requested food and an order_id was returned, ask them to provide their delivery destination so the order can be confirmed and a destination-specific ETA can be calculated.
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

    def _create_order_from_llm(self, history: InMemoryChatMessageHistory, message: str) -> dict | None:
        """Let the LLM resolve order details from the conversation, then persist them safely."""
        conversation = "\n".join(
            f"{'User' if item.type == 'human' else 'Assistant'}: {item.content}"
            for item in history.messages[-6:]
        )
        extraction_prompt = f"""
Extract one food order from this conversation, using the latest message to fill in
customer details omitted earlier. Return JSON only, with this exact shape:
{{"customer": {{"full_name": "...", "email": "...", "phone": "..."}},
 "order": {{"cafe": "...", "menu_item": "...", "quantity": 1}}}}

Use null for any value that cannot be determined. Do not return SQL or prose.

Conversation:
{conversation}
User: {message}
"""
        try:
            raw = _clean_content(self._call_llm([
                {"role": "system", "content": "You extract restaurant-order data accurately. Return JSON only."},
                {"role": "user", "content": extraction_prompt},
            ], max_tokens=250))
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            extracted = json.loads(match.group(0) if match else raw)
            customer_data = extracted["customer"]
            order_data = extracted["order"]
            full_name = customer_data["full_name"]
            email = customer_data["email"]
            phone = customer_data["phone"]
            cafe = order_data["cafe"]
            menu_item = order_data["menu_item"]
            quantity = int(order_data["quantity"])
            if not all(isinstance(value, str) and value.strip() for value in (full_name, email, phone, cafe, menu_item)) or quantity < 1:
                return None
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, RuntimeError):
            return None

        restaurant = self.db.scalar(
            select(Restaurant)
            .where(Restaurant.cafe.ilike(f"%{cafe.strip()}%"))
            .where(Restaurant.menu_item.ilike(f"%{menu_item.strip()}%"))
            .limit(1)
        )
        if restaurant is None or restaurant.price_inr is None:
            return None

        customer = self.db.scalar(select(Customer).where(Customer.email == email.strip()).limit(1))
        if customer is None:
            customer = self.db.scalar(select(Customer).where(Customer.phone == phone.strip()).limit(1))
        if customer is None:
            customer = Customer(full_name=full_name.strip(), email=email.strip(), phone=phone.strip())
            self.db.add(customer)
            self.db.flush()

        order = Order(
            restaurant_id=restaurant.id,
            customer_id=customer.id,
            status=OrderStatus.pending,
            total_amount=restaurant.price_inr * quantity,
        )
        self.db.add(order)
        self.db.flush()
        self.db.add(OrderItem(
            order_id=order.id,
            item_name=restaurant.menu_item,
            quantity=quantity,
            unit_price=restaurant.price_inr,
        ))
        self.db.commit()
        return {
            "response": (
                f"Your order has been created. Your Order Number is #{order.id}. "
                "Please provide your delivery destination so I can calculate the delivery estimate."
            ),
            "sql_query": None,
        }

    def _add_item_from_llm(self, history: InMemoryChatMessageHistory, message: str, session_order_id: int | None) -> dict | None:
        """Use the LLM to identify an item change, then persist it transactionally."""
        prompt = f"""
Extract an item addition from the user's restaurant-order conversation. Return JSON only:
{{"order_id": <integer or null>, "menu_item": "...", "quantity": <integer>}}
Use the session order number {session_order_id!r} when the user does not specify one.
User request: {message}
"""
        try:
            raw = _clean_content(self._call_llm([
                {"role": "system", "content": "You extract order modifications accurately. Return JSON only."},
                {"role": "user", "content": prompt},
            ], max_tokens=150))
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            data = json.loads(match.group(0) if match else raw)
            order_id = int(data["order_id"] or session_order_id)
            menu_item = data["menu_item"]
            quantity = int(data["quantity"])
            if not isinstance(menu_item, str) or not menu_item.strip() or quantity < 1:
                return None
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, RuntimeError):
            return None

        order = self.db.get(Order, order_id)
        if order is None:
            return None
        order_restaurant = self.db.get(Restaurant, order.restaurant_id)
        if order_restaurant is None:
            return None
        restaurant_item = self.db.scalar(
            select(Restaurant)
            .where(Restaurant.cafe == order_restaurant.cafe)
            .where(Restaurant.menu_item.ilike(f"%{menu_item.strip()}%"))
            .limit(1)
        )
        if restaurant_item is None or restaurant_item.price_inr is None:
            return None

        self.db.add(OrderItem(
            order_id=order.id,
            item_name=restaurant_item.menu_item,
            quantity=quantity,
            unit_price=restaurant_item.price_inr,
        ))
        order.total_amount += restaurant_item.price_inr * quantity
        self.db.commit()
        return {
            "response": (
                f"Added {quantity} {restaurant_item.menu_item} to order #{order.id}. "
                f"Your updated total is ₹{order.total_amount}."
            ),
            "sql_query": None,
        }

    def _latest_active_order(self, order_id: int | None = None) -> tuple[Order, Restaurant] | None:
        """Return the latest order that can still be delivered in this demo app."""
        statement = (
            select(Order, Restaurant)
            .join(Restaurant, Restaurant.id == Order.restaurant_id)
            .where(Order.status.in_([OrderStatus.pending, OrderStatus.confirmed, OrderStatus.preparing]))
            .order_by(Order.created_at.desc())
        )
        if order_id is not None:
            statement = statement.where(Order.id == order_id)
        statement = statement.limit(1)
        return self.db.execute(statement).first()

    def _estimate_delivery_minutes(self, cafe_location: str, destination: str) -> int | None:
        """Ask the LLM for an estimate using the actual café and destination fields."""
        estimate_prompt = f"""
Estimate a realistic food-delivery travel time in minutes using the café location and
the customer's destination below. Consider their apparent proximity, typical city
traffic, and last-mile delivery. Do not use a default or standard delivery window.

Café location: {cafe_location}
Customer destination: {destination}

Return JSON only: {{"eta_minutes": <whole number between 1 and 180>}}
"""
        try:
            raw = _clean_content(self._call_llm([
                {"role": "system", "content": "You estimate restaurant delivery travel times. Return JSON only."},
                {"role": "user", "content": estimate_prompt},
            ]))
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            eta = int(json.loads(match.group(0) if match else raw).get("eta_minutes"))
            return eta if 1 <= eta <= 180 else None
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    def _save_destination(self, destination: str, order_id: int | None = None) -> dict:
        active_order = self._latest_active_order(order_id)
        if not active_order:
            return {
                "response": "I couldn't find an active order to deliver. Please place an order first.",
                "sql_query": None,
            }

        order, _ = active_order
        order.delivery_destination = destination
        self.db.commit()
        return {
            "response": (
                f"Delivery destination saved: {destination}. "
                f"Type CONFIRM to place your order."
            ),
            "sql_query": None,
        }

    def _confirm_latest_order(self, order_id: int | None = None) -> dict:
        active_order = self._latest_active_order(order_id)
        if not active_order:
            return {"response": "I couldn't find an active order to confirm.", "sql_query": None}

        order, restaurant = active_order
        if not order.delivery_destination:
            return {
                "response": "Please provide your delivery destination before confirming the order.",
                "sql_query": None,
            }

        eta_minutes = self._estimate_delivery_minutes(restaurant.location, order.delivery_destination)
        if eta_minutes is None:
            return {
                "response": "I couldn't calculate a delivery estimate for that destination. Please provide a more specific address or locality.",
                "sql_query": None,
            }

        order.estimated_delivery_minutes = eta_minutes
        order.confirmed_at = datetime.utcnow()
        order.status = OrderStatus.confirmed
        self.db.commit()
        return {
            "response": (
                f"Your order has been confirmed. Your Order Number is #{order.id}. "
                f"Delivery to {order.delivery_destination} is estimated at about {eta_minutes} minutes."
            ),
            "sql_query": None,
        }

    def _delivery_status(self, order_id: int | None = None) -> dict:
        active_order = self._latest_active_order(order_id)
        if not active_order:
            return {"response": "I couldn't find an active order.", "sql_query": None}

        order, _ = active_order
        if not order.delivery_destination or not order.estimated_delivery_minutes or not order.confirmed_at:
            return {
                "response": "Please provide your delivery destination first so I can calculate your delivery estimate.",
                "sql_query": None,
            }

        elapsed_minutes = max(0, int((datetime.utcnow() - order.confirmed_at).total_seconds() // 60))
        remaining_minutes = max(0, order.estimated_delivery_minutes - elapsed_minutes)
        if remaining_minutes == 0:
            order.status = OrderStatus.completed
            self.db.commit()
            return {
                "response": "Your order has been delivered successfully. Enjoy your meal!",
                "sql_query": None,
            }

        return {
            "response": (
                f"Your order is on the way to {order.delivery_destination}. "
                f"Estimated time remaining: about {remaining_minutes} minutes."
            ),
            "sql_query": None,
        }

    def process_query(self, user_message: str, session_id: str = "default_session") -> dict:
        # A copied JSON value can contain an escaped at-sign. Store and use the
        # actual email address so it is not persisted with a stray backslash.
        user_message = user_message.replace(r"\@", "@")
        history = get_session_history(session_id)
        session_order_id = _session_order_id(history)
        order_details_follow_up = _is_order_details_follow_up(history, user_message)

        last_message = history.messages[-1] if history.messages else None
        awaiting_destination = bool(
            last_message
            and last_message.type == "ai"
            and "provide your delivery destination" in str(last_message.content).lower()
        )
        # An LLM response can ask for a destination before an order was actually
        # inserted. Do not consume the next message as an address in that case:
        # it would otherwise leave the session stuck with no order to update.
        if awaiting_destination and self._latest_active_order(session_order_id):
            destination_result = self._save_destination(user_message.strip(), session_order_id)
            history.add_user_message(user_message)
            history.add_ai_message(destination_result["response"])
            return destination_result

        awaiting_confirmation = bool(
            last_message
            and last_message.type == "ai"
            and "type confirm to place your order" in str(last_message.content).lower()
        )
        if awaiting_confirmation and re.fullmatch(r"\s*(?:yes,?\s+)?confirm\s*[.!]?\s*", user_message, re.IGNORECASE):
            confirmation_result = self._confirm_latest_order(session_order_id)
            history.add_user_message(user_message)
            history.add_ai_message(confirmation_result["response"])
            return confirmation_result

        if re.search(r"\b(?:add|include)\b", user_message, re.IGNORECASE):
            updated_order = self._add_item_from_llm(history, user_message, session_order_id)
            if updated_order:
                history.add_user_message(user_message)
                history.add_ai_message(updated_order["response"])
                return updated_order

        if re.search(
            r"\b(?:order\s+status|delivery\s+(?:time|status|eta)|eta|where(?:'s| is) my order|"
            r"when will.*(?:arrive|reach)|how (?:much|long).*(?:deli\w*|order)|"
            r"how much time (?:is )?left|approx(?:imate)?\s+(?:delivery\s+)?(?:time|timing).*(?:reach|arrive))\b",
            user_message,
            re.IGNORECASE,
        ):
            status_result = self._delivery_status(session_order_id)
            history.add_user_message(user_message)
            history.add_ai_message(status_result["response"])
            return status_result

        if order_details_follow_up:
            created_order = self._create_order_from_llm(history, user_message)
            if created_order:
                history.add_user_message(user_message)
                history.add_ai_message(created_order["response"])
                return created_order
        
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        dynamic_prompt = DB_SCHEMA_PROMPT + f"\n\nCURRENT SYSTEM TIME: {current_time}\n"
        if order_details_follow_up:
            dynamic_prompt += (
                "\n- The latest user message provides contact details for an order requested "
                "earlier in this session. Generate the complete order SQL now, using the "
                "item, quantity, and café from the earlier user message and the customer "
                "details from the latest one."
            )
        
        messages = [{"role": "system", "content": dynamic_prompt}]
        for msg in history.messages[-8:]:
            role = "user" if msg.type == "human" else "assistant"
            messages.append({"role": role, "content": msg.content})
            
        messages.append({"role": "user", "content": user_message})

        try:
            llm_output = self._call_llm(messages)
        except RuntimeError as error:
            logger.warning("Chat request could not reach any configured LLM: %s", error)
            final_response = "The ordering assistant is temporarily busy. Please try again in a few minutes."
            history.add_user_message(user_message)
            history.add_ai_message(final_response)
            return {"response": final_response, "sql_query": None}
        cleaned_output = _clean_content(llm_output)
        
        # Extract JSON object from anywhere in the LLM output
        json_match = re.search(r"\{.*\}", cleaned_output, re.DOTALL)
        clean_json = json_match.group(0) if json_match else cleaned_output
        
        try:
            parsed = json.loads(clean_json)
        except json.JSONDecodeError:
            # Never send incomplete internal JSON/SQL to the chat UI. It can
            # happen when a model response is cut off before its closing brace.
            is_incomplete_sql_payload = bool(re.match(
                r'\s*\{.*?["\']sql["\']\s*:', cleaned_output, re.DOTALL
            ))
            parsed = {
                "sql": None,
                "explanation": "" if is_incomplete_sql_payload else cleaned_output,
            }

        sql_query = parsed.get("sql")
        explanation = parsed.get("explanation", "")

        # A malformed or evasive first response must not be presented as the
        # generic greeting for a request that plainly needs database data.
        needs_database_query = bool(re.search(
            r"\b(menu|order|reservation|book|status|delivery|restaurant|restaurants|cafe|café|cafes|cafés)\b",
            user_message,
            flags=re.IGNORECASE,
        )) or order_details_follow_up
        if not sql_query and needs_database_query:
            retry_prompt = (
                "The previous response was incomplete or did not contain usable SQL. The user's latest message "
                "is a restaurant database request, so return JSON with a non-null SQL "
                "query whenever its requested data can be queried. In particular, a menu "
                "request must use a SELECT query against restaurants. If the latest message "
                "contains customer details for an earlier order, combine those messages and "
                "create a pending order and its items now. Do not ask for a delivery "
                "destination until after that order has been created. Return JSON only."
            )
            try:
                retry_output = self._call_llm(messages + [{"role": "user", "content": retry_prompt}])
            except RuntimeError:
                retry_output = ""
            retry_cleaned = _clean_content(retry_output)
            retry_match = re.search(r"\{.*\}", retry_cleaned, re.DOTALL)
            try:
                retry_parsed = json.loads(retry_match.group(0) if retry_match else retry_cleaned)
                sql_query = retry_parsed.get("sql")
                explanation = retry_parsed.get("explanation", explanation)
            except json.JSONDecodeError:
                pass

        if not sql_query:
            final_response = explanation or "I couldn't complete that request just yet. Please try again in a moment."
            history.add_user_message(user_message)
            history.add_ai_message(final_response)
            return {
                "response": final_response,
                "sql_query": None
            }

        final_result = self._execute_sql(sql_query, user_message)
        history.add_user_message(user_message)
        history.add_ai_message(final_result["response"])
        return final_result
