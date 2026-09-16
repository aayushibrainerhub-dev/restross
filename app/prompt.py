# import json


# DB_SCHEMA_PROMPT = """
# You are a PostgreSQL assistant for a Restaurant App. Always respond with ONLY valid JSON: {"sql": "<query or null>", "explanation": "<text>"}. No markdown, no <think> tags, no extra text.

# SCHEMA:
# - users(id UUID PK, email, full_name, role, is_active, created_at)
# - restaurants(id INT PK, cafe, location, rating_out_of_5, feedback, menu_item, price_inr, approx_cost_for_two_inr) — ONE ROW PER MENU ITEM
# - customers(id UUID PK, full_name, phone, email, created_at)
# - orders(id INT PK, restaurant_id→restaurants, customer_id→customers, status, total_amount, notes, delivery_destination, estimated_delivery_minutes, confirmed_at, created_at)
# - order_items(id INT PK, order_id→orders, item_name, quantity, unit_price)
# - reservations(id INT PK, restaurant_id→restaurants, customer_id→customers, reserved_for, party_size, status, notes)
# OrderStatus: pending|confirmed|preparing|served|completed|cancelled
# ReservationStatus: booked|seated|completed|cancelled

# DOMAIN: Only restaurants, food, menus, ordering, delivery, reservations, user profile.
# - Out-of-scope → {"sql":null,"explanation":"I am a restaurant assistant. I can only assist you with food ordering, menu browsing, café recommendations, reservations, and your order details."}
# - Greeting/general question → {"sql":null,"explanation":"<friendly response>"}

# STANDARD QUERIES:
# - Menu: SELECT cafe, menu_item, price_inr FROM restaurants WHERE cafe ILIKE '%<name>%' ORDER BY menu_item LIMIT 50
# - Top cafés: SELECT cafe, location, rating_out_of_5, approx_cost_for_two_inr FROM (SELECT DISTINCT ON (cafe) cafe, location, rating_out_of_5, approx_cost_for_two_inr FROM restaurants ORDER BY cafe, rating_out_of_5 DESC NULLS LAST) sub ORDER BY rating_out_of_5 DESC NULLS LAST LIMIT 5
# - Other list queries: LIMIT 10 by default

# ORDERING (new food order — user names items + café):
# - Authenticated user: DO NOT ask for name/email/phone — session provides them.
# - Unauthenticated: ensure customer row exists before inserting order.
# - ALWAYS generate INSERT SQL to create the order with status='pending' and delivery_destination=NULL explicitly, even when no destination is given yet.
#   After the INSERT, ask for destination in the explanation field.
#   NEVER return {"sql":null} for a new order request — the SQL must run first.
# - Multi-table writes must use CTEs (WITH clause). CTE final statement: SELECT id AS order_id FROM new_order
# - FK resolution: restaurant_id via subquery (cafe ILIKE); customer_id via subquery or CTE ref.

# FINALIZING AN EXISTING PENDING ORDER (user says "confirm", "book it", "place order", etc. — NOT a new order request):
# - This section applies ONLY when the user is confirming an order that was already created earlier in the conversation.
# - No destination provided → {"sql":null,"explanation":"Please provide your delivery destination before we can book your order."}
# - Destination already provided → {"sql":null,"explanation":"I have the destination. I will calculate a delivery estimate using the café location."}

# RESERVATION: INSERT into reservations (status='booked'). Ensure customer exists first using WHERE NOT EXISTS.

# INTERNAL TASK SQL (when called programmatically): same CTE/no-placeholder/aliasing rules apply — alias columns exactly as instructed since application code reads them by name.

# CRITICAL SQL RULES:
# - NEVER use $1/$2/?/:param — literal values or subqueries only
# - NEVER provide explicit id PKs — auto-generated
# - NEVER use ON CONFLICT(full_name) — use WHERE NOT EXISTS instead
# - NEVER generate DROP or ALTER
# - ALWAYS CTE for multi-table writes; orders CTE must RETURNING id → SELECT id AS order_id FROM new_order
# """

# ADDRESS_EXTRACTION_SYSTEM = "You extract address location names accurately. Return address only."
# ADDRESS_NORMALIZATION_SYSTEM = "You normalize and deduplicate delivery address lists accurately. Output JSON only."
# INTENT_CLASSIFIER_SYSTEM = "You classify restaurant conversation intent. Return JSON only, no markdown."
# PRECISE_INTENT_SYSTEM = "You are a precise intent classifier for a restaurant ordering system. Output pure JSON only."
# ORDER_EXTRACTION_SYSTEM = "You extract restaurant order data accurately. Output JSON only, no markdown."
# ITEM_EXTRACTION_SYSTEM = "You extract order modifications accurately. Return JSON only, no markdown."
# CONTACT_EXTRACTION_SYSTEM = "You extract contact details accurately. Return JSON only, no markdown."
# ETA_SYSTEM = "Return only a delivery ETA integer."
# DATABASE_ASSISTANT_SYSTEM = "You are a helpful restaurant database assistant."
# DATABASE_SUMMARY_SYSTEM = "You are a helpful restaurant database assistant. Provide clear summaries of data results."
# DATABASE_CONFIRMATION_SYSTEM = "You are a helpful restaurant database assistant confirming data operations."


# def order_confirmation_prompt(user_message: str, rowcount: int, returned_info: str) -> str:
#   return f"""
# The user requested: "{user_message}"
# Rows affected: {rowcount}{returned_info}

# Write a SHORT, FRIENDLY initial order confirmation (1-2 sentences max).
# Confirm the requested item and café enthusiastically (e.g., "Got it! Added [Item] from [Cafe] to your order.").
# IMPORTANT:
# - Do NOT say "it's on its way", "out for delivery", or "delivered" — the order is pending address selection.
# - End naturally without asking for or mentioning the address (the UI will show address selection options below).

# STRICTLY FORBIDDEN in your response:
# - Any SQL code or queries
# - Table names (customers, orders, order_items, etc.)
# - Column names or technical field names
# - Phrases like "behind the scenes", "database", "record was inserted", "rows affected"
# - Any mention of delivery destination, address, or "options will appear below"
# """


# def database_recovery_prompt(user_message: str) -> str:
#   return f"""
# The user requested: "{user_message}"

# The previous response did not provide SQL. This request appears to require
# data from the restaurant database. Generate the correct SQL query now.
# Return ONLY valid JSON in this exact shape:
# {{"sql": "<generated SQL>", "explanation": ""}}
# Never invent, summarize, or guess database results without SQL execution.
# Use SELECT for read-only requests and follow the schema and SQL safety rules.
# """


# def address_extraction_prompt(text: str) -> str:
#   return f"""
# Extract ONLY the physical address / location name from the following user message:
# "{text}"

# Examples:
# - "my address is Royal homes, gota" -> "Royal homes, gota"
# - "the delivery destination is Satellite, CG Road" -> "Satellite, CG Road"
# - "deliver to SG Highway" -> "SG Highway"
# - "Gota, Ahmedabad" -> "Gota, Ahmedabad"

# Return ONLY the extracted address string without quotes, explanations, or extra punctuation.
# """


# def address_normalization_prompt(addresses_json: str) -> str:
#   return f"""
# You are given a list of past delivery destination strings provided by a user:
# {addresses_json}

# Tasks:
# 1. Extract ONLY the clean, physical address / location name from each string (removing preamble words like "delivery address is", "my address", etc.).
# 2. Deduplicate any locations that refer to the same address (standardizing capitalization and formatting, e.g. "Royal Homes, Gota").
# 3. Preserve unique distinct addresses.

# Return JSON ONLY with exact shape:
# {{"addresses": ["Clean Address 1", "Clean Address 2"]}}
# """


# def order_items_query_prompt(order_id: int) -> str:
#   return f"""
# Generate a SELECT query that returns every item belonging to order_id {order_id}
# from the order_items table. Return the item name, quantity, and unit price for
# every matching row, ordered consistently by the item row id.
# Do not substitute another order id, join through another table, or return an
# aggregate. Alias the returned columns EXACTLY as: item_name, quantity,
# unit_price.
# """


# def contact_follow_up_prompt(recent_turns: str, message: str) -> str:
#   return f"""
# You are analysing a restaurant-ordering conversation.

# Previous turns:
# {recent_turns}

# New user message:
# {message}

# Answer with JSON only: {{"is_contact_follow_up": true}} if the new message
# supplies name, email, or phone number to complete a food order the user
# started in a prior turn, otherwise {{"is_contact_follow_up": false}}.
# Do NOT explain. Return JSON only.
# """


# def intent_classification_prompt(recent_turns: str, message: str, session_order_id: int | None) -> str:
#   return (
#     f"Conversation History:\n{recent_turns}\n\n"
#     f"Latest User Message: \"{message}\"\n"
#     f"Active Order ID in Session: {session_order_id}\n\n"
#     "Analyze the latest user message and classify its intent as EXACTLY one of the following labels:\n"
#     "- contact_followup : User provides personal contact details (such as name, email, phone number) to create or complete an order.\n"
#     "- confirm_order    : User says confirm, approve, proceed, or wants to finalize a pending order.\n"
#     "- delivery_status   : User asks about delivery ETA, timing, time left, time remaining, arrival time, or order status.\n"
#     "- give_destination : User provides a delivery address, location, area, or destination.\n"
#     "- add_item         : User requests to add more food items or drinks to an existing order.\n"
#     "- general          : Any general inquiry, menu browse, recommendation request, or uncategorized message.\n\n"
#     "Respond ONLY with a JSON object: {\"intent\": \"<label>\"}"
#   )


# def result_summary_prompt(user_message: str, rows: list[dict], current_time: str) -> str:
#   return f"""
# The user asked: "{user_message}"
# Query Result Data ({len(rows)} rows):
# {json.dumps(rows, default=str)}
# CURRENT SYSTEM TIME: {current_time}

# Synthesize a clear, friendly, and well-formatted response for the user based strictly on this data.
# CRITICAL INSTRUCTION: Do NOT include SQL queries, reasoning steps, code blocks, or mentions of database tables in your final answer. Provide only the direct answer to the user's request. Never invent a fixed delivery window or ETA; delivery timing is calculated separately from the saved destination and the order's stored ETA.
# """


# def sql_fix_prompt(sql: str, error: str, task: str) -> str:
#   return (
#     f"{task}\n\nThe SQL you previously wrote:\n{sql}\n\n"
#     f"failed with database error:\n{error}\n\n"
#     "Fix it. Remember: no placeholders like $1/?/:param — literal values only."
#   )


# def error_synthesis_prompt(user_message: str, error: str) -> str:
#   return f"""
# The user requested: "{user_message}"
# The database operation failed with error: {error}

# Briefly explain to the user why the operation failed in friendly terms.
# CRITICAL INSTRUCTION: Do NOT include raw SQL, code blocks, or technical tracebacks.
# """


# def authenticated_order_extraction_prompt(conversation: str, user_info: dict, message: str) -> str:
#   return f"""
# Extract the cafe name and ALL ordered food items from this conversation.
# The customer is logged in as {user_info['full_name']} ({user_info['email']}). DO NOT ask for contact details.
# Return JSON only with this exact shape:
# {{
#   "order": {{
#     "cafe": "...",
#     "items": [
#       {{"menu_item": "...", "quantity": 1}}
#     ]
#   }}
# }}

# Use null for unknown cafe. If no food items are specified, set items to []. Do not return SQL or prose.

# Conversation:
# {conversation}
# User: {message}
# """


# def guest_order_extraction_prompt(conversation: str, message: str) -> str:
#   return f"""
# Extract customer details and ALL ordered food items from this conversation.
# Return JSON only with this exact shape:
# {{
#   "customer": {{"full_name": "...", "email": "...", "phone": "..."}},
#   "order": {{
#     "cafe": "...",
#     "items": [
#       {{"menu_item": "...", "quantity": 1}}
#     ]
#   }}
# }}

# Use null for unknown customer/cafe fields. If no food items are specified, set items to []. Do not return SQL or prose.

# Conversation:
# {conversation}
# User: {message}
# """


# def create_order_sql_prompt(full_name: str, email: str, phone: str, user_id: str | None, cafe: str | None, items_desc: str) -> str:
#   user_line = f"Customer user_id: {user_id}\n" if user_id else ""
#   cafe_value = cafe if cafe and cafe.strip() else "not specified — infer the single café whose menu best matches the items requested"
#   return f"""
# Create a new pending order for this customer and these items.

# Customer full name: {full_name}
# Customer email: {email}
# Customer phone: {phone}
# {user_line}Café: {cafe_value}
# Items requested (quantity x menu item, as the user described it): {items_desc}

# Write a CTE that:
# 1. Ensures the customer exists (if email/phone matches an existing customer row, use that customer's id; otherwise insert a new customer).
# 2. For each requested item, resolves the exact menu_item name and price_inr
#    from restaurants by matching the café and using ILIKE against the
#    requested item text.
# 3. Inserts the order into orders with status 'pending', the resolved
#    restaurant_id, the customer's id, and total_amount equal to the sum of
#    (unit price x quantity) across all resolved items.
# 4. Inserts one row per resolved item into order_items with the resolved
#    item_name, quantity, and unit_price.
# Do not create an order if none of the requested items can be matched.
# For every matched item, the SQL must insert exactly one order_items row.
# The order total must be calculated from the same matched item rows, so it
# cannot be zero when at least one item is inserted.
# The final statement of the CTE must be a SELECT returning the new order's id
# the customer's full name, and the number of inserted item rows, aliased
# EXACTLY as: order_id, customer_name, item_count.
# """


# def add_item_extraction_prompt(session_order_id: int | None, message: str) -> str:
#   return f"""
# Extract an item addition from the user's restaurant-order conversation. Return JSON only:
# {{"order_id": <integer or null>, "menu_item": "...", "quantity": <integer>}}
# Use the session order number {session_order_id!r} when the user does not specify one.
# User request: {message}
# """


# def add_item_sql_prompt(order_id: int, menu_item: str, quantity: int) -> str:
#   return f"""
# The user wants to add an item to an existing order.
# order id: {order_id}
# menu item requested: {menu_item}
# quantity: {quantity}

# Write a CTE that:
# 1. Looks up the restaurant this order belongs to via orders.restaurant_id.
# 2. Finds the matching row in restaurants for that same café where
#    menu_item ILIKE a pattern built from the requested item text, to get its
#    exact menu_item name and price_inr.
# 3. Inserts one row into order_items for this order with that item_name,
#    quantity {quantity}, and the resolved unit_price.
# 4. Updates orders.total_amount for this order id by adding
#    (resolved unit_price x {quantity}) to the existing total_amount.
# The final statement must be a SELECT returning the resolved item name and
# unit price, aliased EXACTLY as: item_name, unit_price.
# """


# def order_total_prompt(order_id: int) -> str:
#   return f"""
# Write a SELECT that returns total_amount for the orders row with id = {order_id}.
# Alias the column EXACTLY as: total_amount.
# """


# def latest_active_order_prompt(scope: str) -> str:
#   return f"""
# Write a SELECT joining orders to restaurants (and LEFT JOIN customers on
# orders.customer_id, since customer_id may be null) that returns the latest
# order whose status is 'pending', 'confirmed', or 'preparing'.
# {scope}
# Alias the returned columns EXACTLY as: order_id, order_status, total_amount,
# delivery_destination, estimated_delivery_minutes, confirmed_at,
# restaurant_id, cafe, location, customer_id, customer_name, customer_email,
# customer_phone.
# Limit the result to 1 row.
# """


# def contact_details_prompt(recent_turns: str, message: str) -> str:
#   return f"""
# Extract the customer's contact details from this restaurant-ordering
# conversation. These are sometimes split across messages, so consider the
# recent turns together with the new message.

# Recent turns:
# {recent_turns}

# New user message:
# {message}

# Return JSON only: {{"full_name": "...", "email": "...", "phone": "..."}}
# Use null for any field that truly isn't present. Do not invent values.
# """


# def attach_contact_prompt(order_id: int, full_name: str, email: str, phone: str) -> str:
#   return f"""
# Attach a customer to an existing order.
# order id: {order_id}
# Customer full name: {full_name}
# Customer email: {email}
# Customer phone: {phone}

# Write a CTE that:
# 1. Ensures a customer with this email or phone exists — if one already
#    matches by email or phone, update their full_name/email/phone to these
#    values; otherwise insert a new customer row. Do NOT use ON CONFLICT on
#    full_name, since it is not unique.
# 2. Updates the orders row with id = {order_id} to set its customer_id to
#    that customer's id.
# The final statement must be a SELECT returning the customer's full name,
# aliased EXACTLY as: customer_name.
# """


# def eta_prompt(cafe_location: str, destination: str) -> str:
#   return (
#     "Estimate food delivery time in Ahmedabad. "
#     f"Café: {cafe_location}. Destination: {destination}. "
#     "Include kitchen and rider dispatch time. "
#     "Reply with only one whole number from 15 to 90."
#   )


# def save_destination_sql_prompt(destination: str, eta_minutes: int, order_id: int) -> str:
#   return f"""
# Write an UPDATE that sets delivery_destination = '{destination}' and
#     estimated_delivery_minutes = {eta_minutes}, and total_amount to the sum of
#     (unit_price * quantity) from order_items for order_id = {order_id}
#     on the orders row with id = {order_id}.
# """


# def confirm_order_sql_prompt(eta_minutes: int, order_id: int) -> str:
#   return f"""
# Write an UPDATE that sets status = 'confirmed', confirmed_at = NOW(), and
# estimated_delivery_minutes = {eta_minutes} on the orders row with id = {order_id}.
# """


# def order_status_prompt(order_id: int) -> str:
#   return f"""
# Write a SELECT that returns status and delivery_destination for the orders
# row with id = {order_id}.
# Alias the returned columns EXACTLY as: order_status, delivery_destination.
# """


# def complete_order_prompt(order_id: int) -> str:
#   return f"""
# Write an UPDATE that sets status = 'completed' on the orders row with id = {order_id}.
# """


# def authenticated_user_context(user_info: dict, past_addresses: list[str]) -> str:
#   addresses_str = ", ".join(f"'{address}'" for address in past_addresses) if past_addresses else "No saved addresses found"
#   return (
#     "OVERRIDE — LOGGED-IN USER SESSION (highest priority):\n"
#     "The user making this request is authenticated. Their profile details are:\n"
#     f"  full_name       : {user_info['full_name']}\n"
#     f"  email           : {user_info['email']}\n"
#     f"  phone           : {user_info['phone']}\n"
#     f"  saved_addresses : {addresses_str}\n\n"
#     "RULES that override the general instructions above:\n"
#     "1. If the user asks about their name, email, phone, saved/past addresses, account, profile, or 'who am I', "
#     "answer DIRECTLY and conversationally using the profile and address data above. "
#     "Return JSON: {\"sql\": null, \"explanation\": \"<your friendly answer using their real data>\"}\n"
#     "2. NEVER ask a logged-in user for their contact details — they are already known.\n"
#     "3. When placing an order for this user, use the profile above as the customer — "
#     "do NOT ask for name, email or phone.\n"
#     "4. DO NOT answer questions unrelated to restaurants, food ordering, menus, reservations, or user profile. For out-of-scope topics, decline politely."
#   )


# def database_chat_messages(
#     current_time: str,
#     user_context: str | None,
#     history_messages: list[tuple[str, str]],
#     user_message: str,
# ) -> list[dict]:
#   messages = [{"role": "system", "content": DB_SCHEMA_PROMPT + f"\n\nCURRENT SYSTEM TIME: {current_time}\n"}]
#   if user_context:
#     messages.append({"role": "system", "content": user_context})
#   messages.extend({"role": role, "content": content} for role, content in history_messages)
#   messages.append({"role": "user", "content": user_message})
#   return messages
