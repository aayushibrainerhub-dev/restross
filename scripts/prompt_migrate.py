"""Publish every Restaurant application prompt to Langfuse.

Run from the repository root after setting LANGFUSE_PUBLIC_KEY,
LANGFUSE_SECRET_KEY, and LANGFUSE_BASE_URL:

    python scripts/prompt_migrate.py

Prompt text remains defined in app.prompt. Dynamic values are rendered here as
Langfuse Mustache variables, preventing the migration from drifting from source.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv
from langfuse import get_client


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")
sys.path.insert(0, str(PROJECT_ROOT))
from app import prompt as source  # noqa: E402


class TemplateVariable:
    """A value which becomes a Langfuse Mustache variable in f-strings."""

    def __init__(self, name: str):
        self.name = name

    def __str__(self) -> str:
        return "{{" + self.name + "}}"

    def __repr__(self) -> str:
        return str(self)

    def __bool__(self) -> bool:
        # Include optional sections in the published template.
        return True

    def strip(self) -> "TemplateVariable":
        return self


def variable(name: str) -> TemplateVariable:
    return TemplateVariable(name)


def result_summary_template() -> str:
    """Render the prompt whose implementation serializes its input to JSON."""
    return """
The user asked: "{{user_message}}"
Query Result Data ({{row_count}} rows):
{{rows}}
CURRENT SYSTEM TIME: {{current_time}}

Synthesize a clear, friendly, and well-formatted response for the user based strictly on this data.
CRITICAL INSTRUCTION: Do NOT include SQL queries, reasoning steps, code blocks, or mentions of database tables in your final answer. Provide only the direct answer to the user's request. Never invent a fixed delivery window or ETA; delivery timing is calculated separately from the saved destination and the order's stored ETA.
"""


SYSTEM_PROMPTS = {
    "restaurant/system/address-extraction": source.ADDRESS_EXTRACTION_SYSTEM,
    "restaurant/system/address-normalization": source.ADDRESS_NORMALIZATION_SYSTEM,
    "restaurant/system/intent-classifier": source.INTENT_CLASSIFIER_SYSTEM,
    "restaurant/system/precise-intent": source.PRECISE_INTENT_SYSTEM,
    "restaurant/system/order-extraction": source.ORDER_EXTRACTION_SYSTEM,
    "restaurant/system/item-extraction": source.ITEM_EXTRACTION_SYSTEM,
    "restaurant/system/contact-extraction": source.CONTACT_EXTRACTION_SYSTEM,
    "restaurant/system/eta": source.ETA_SYSTEM,
    "restaurant/system/database-assistant": source.DATABASE_ASSISTANT_SYSTEM,
    "restaurant/system/database-summary": source.DATABASE_SUMMARY_SYSTEM,
    "restaurant/system/database-confirmation": source.DATABASE_CONFIRMATION_SYSTEM,
    "restaurant/db-schema": source.DB_SCHEMA_PROMPT + "\n\nCURRENT SYSTEM TIME: {{current_time}}\n",
}


PROMPT_BUILDERS: dict[str, Callable[[], str]] = {
    "restaurant/order-confirmation": lambda: source.order_confirmation_prompt(variable("user_message"), variable("rowcount"), variable("returned_info")),
    "restaurant/database-recovery": lambda: source.database_recovery_prompt(variable("user_message")),
    "restaurant/address-extraction": lambda: source.address_extraction_prompt(variable("text")),
    "restaurant/address-normalization": lambda: source.address_normalization_prompt(variable("addresses_json")),
    "restaurant/order-items-query": lambda: source.order_items_query_prompt(variable("order_id")),
    "restaurant/contact-follow-up": lambda: source.contact_follow_up_prompt(variable("recent_turns"), variable("message")),
    "restaurant/intent-classification": lambda: source.intent_classification_prompt(variable("recent_turns"), variable("message"), variable("session_order_id")),
    "restaurant/response-synthesis": result_summary_template,
    "restaurant/sql-fix": lambda: source.sql_fix_prompt(variable("sql"), variable("error"), variable("task")),
    "restaurant/error-synthesis": lambda: source.error_synthesis_prompt(variable("user_message"), variable("error")),
    "restaurant/authenticated-order-extraction": lambda: source.authenticated_order_extraction_prompt(variable("conversation"), {"full_name": variable("user_full_name"), "email": variable("user_email")}, variable("message")),
    "restaurant/guest-order-extraction": lambda: source.guest_order_extraction_prompt(variable("conversation"), variable("message")),
    "restaurant/create-order-sql": lambda: source.create_order_sql_prompt(variable("full_name"), variable("email"), variable("phone"), variable("user_id"), variable("cafe"), variable("items_desc")),
    "restaurant/add-item-extraction": lambda: source.add_item_extraction_prompt(variable("session_order_id"), variable("message")),
    "restaurant/add-item-sql": lambda: source.add_item_sql_prompt(variable("order_id"), variable("menu_item"), variable("quantity")),
    "restaurant/order-total": lambda: source.order_total_prompt(variable("order_id")),
    "restaurant/latest-active-order": lambda: source.latest_active_order_prompt(variable("scope")),
    "restaurant/contact-details": lambda: source.contact_details_prompt(variable("recent_turns"), variable("message")),
    "restaurant/attach-contact": lambda: source.attach_contact_prompt(variable("order_id"), variable("full_name"), variable("email"), variable("phone")),
    "restaurant/eta-estimate": lambda: source.eta_prompt(variable("cafe_location"), variable("destination")),
    "restaurant/save-destination-sql": lambda: source.save_destination_sql_prompt(variable("destination"), variable("eta_minutes"), variable("order_id")),
    "restaurant/confirm-order-sql": lambda: source.confirm_order_sql_prompt(variable("eta_minutes"), variable("order_id")),
    "restaurant/order-status": lambda: source.order_status_prompt(variable("order_id")),
    "restaurant/complete-order": lambda: source.complete_order_prompt(variable("order_id")),
    "restaurant/authenticated-user-context": lambda: """
OVERRIDE — LOGGED-IN USER SESSION (highest priority):
The user making this request is authenticated. Their profile details are:
  full_name       : {{full_name}}
  email           : {{email}}
  phone           : {{phone}}
  saved_addresses : {{past_addresses}}

RULES that override the general instructions above:
1. If the user asks about their name, email, phone, saved/past addresses, account, profile, or 'who am I', answer DIRECTLY and conversationally using the profile and address data above. Return JSON: {"sql": null, "explanation": "<your friendly answer using their real data>"}
2. NEVER ask a logged-in user for their contact details — they are already known.
3. When placing an order for this user, use the profile above as the customer — do NOT ask for name, email or phone.
4. DO NOT answer questions unrelated to restaurants, food ordering, menus, reservations, or user profile. For out-of-scope topics, decline politely.
""",
}


def prompts() -> dict[str, str]:
    return SYSTEM_PROMPTS | {name: build() for name, build in PROMPT_BUILDERS.items()}


def migrate_prompts() -> None:
    langfuse = get_client()
    for name, template in prompts().items():
        print(f"Creating prompt: {name}")
        langfuse.create_prompt(
            name=name,
            type="text",
            prompt=template.strip(),
            labels=["production"],
        )
        print(f"Created: {name}")
    langfuse.flush()


if __name__ == "__main__":
    migrate_prompts()
