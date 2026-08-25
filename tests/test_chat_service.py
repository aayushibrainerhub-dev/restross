from decimal import Decimal
from datetime import datetime, timedelta
from unittest.mock import Mock

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.database import Base
from app.db.models.restaurant import Customer, Order, OrderItem, OrderStatus, Restaurant
from app.services.chat_service import ChatService, get_session_history


def test_menu_request_is_sent_through_dynamic_sql_path():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        db.add(Restaurant(
            cafe="Komo By Kaffa", location="Bengaluru", rating_out_of_5=Decimal("4.5"),
            feedback=None, menu_item="Truffle Scented Mushroom Waffle",
            price_inr=Decimal("375"), approx_cost_for_two_inr=Decimal("800"),
        ))
        db.commit()
        service = ChatService(db)
        service._call_llm = Mock(side_effect=[
            '{"sql":"SELECT cafe, menu_item, price_inr FROM restaurants WHERE cafe LIKE \'%Komo By Kaffa%\' ORDER BY menu_item LIMIT 50", "explanation":""}',
            "Komo By Kaffa offers Truffle Scented Mushroom Waffle for ₹375.",
        ])

        result = service.process_query(
            "i want to order from komo by kaffa please show me the menu",
            session_id="menu-query-test",
        )

        assert result["sql_query"].startswith("SELECT cafe, menu_item, price_inr")
        assert "Truffle Scented Mushroom Waffle" in result["response"]


def test_delivery_eta_uses_destination_and_decreases_over_time():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        restaurant = Restaurant(
            cafe="Komo By Kaffa", location="Koramangala, Bengaluru", rating_out_of_5=Decimal("4.5"),
            feedback=None, menu_item="Waffle", price_inr=Decimal("375"),
            approx_cost_for_two_inr=Decimal("800"),
        )
        db.add(restaurant)
        db.commit()
        db.add(Order(restaurant_id=restaurant.id, status=OrderStatus.pending, total_amount=Decimal("375")))
        db.commit()

        service = ChatService(db)
        service._call_llm = Mock(return_value='{"eta_minutes": 21}')
        history = get_session_history("delivery-eta-test")

        timing = service.process_query("what was the approx timing to reach", session_id="delivery-eta-test")
        assert "provide your delivery destination" in timing["response"].lower()

        history.add_ai_message("Please provide your delivery destination before we can book your order.")

        destination = service.process_query("Indiranagar, Bengaluru", session_id="delivery-eta-test")
        assert "Type CONFIRM" in destination["response"]

        order = db.query(Order).one()
        assert order.delivery_destination == "Indiranagar, Bengaluru"
        assert order.status == OrderStatus.pending
        assert order.estimated_delivery_minutes is None

        confirmation = service.process_query("CONFIRM", session_id="delivery-eta-test")
        assert "Your Order Number is #" in confirmation["response"]
        assert "21 minutes" in confirmation["response"]
        db.refresh(order)
        assert order.estimated_delivery_minutes == 21

        order.confirmed_at = datetime.utcnow() - timedelta(minutes=10)
        db.commit()
        status = service.process_query("What is my delivery status?", session_id="delivery-eta-test")
        assert "11 minutes" in status["response"]

        order.confirmed_at = datetime.utcnow() - timedelta(minutes=21)
        db.commit()
        delivered = service.process_query("What is my delivery status?", session_id="delivery-eta-test")
        assert "delivered successfully" in delivered["response"]
        assert db.query(Order).one().status == OrderStatus.completed


def test_contact_details_continue_an_order_from_in_memory_history():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        db.add(Restaurant(
            cafe="Little French House", location="Ahmedabad", rating_out_of_5=Decimal("4.5"),
            feedback=None, menu_item="Croissant", price_inr=Decimal("160"),
            approx_cost_for_two_inr=Decimal("500"),
        ))
        db.commit()
        service = ChatService(db)
        service._call_llm = Mock(return_value=(
            '{"customer":{"full_name":"Ayushi Shah","email":"2004ayushishah@gmail.com",'
            '"phone":"7984233946"},"order":{"cafe":"Little French House",'
            '"menu_item":"Croissant","quantity":2}}'
        ))
        history = get_session_history("order-contact-follow-up-test")
        history.add_user_message("I want to order 2 croissant from Little French House")
        history.add_ai_message("I need your name and contact details to create the order.")

        result = service.process_query(
            "My name is Ayushi Shah, email is 2004ayushishah@gmail.com, and phone is 7984233946",
            session_id="order-contact-follow-up-test",
        )

        assert "Your Order Number is #" in result["response"]
        assert db.query(Customer).one().email == "2004ayushishah@gmail.com"
        assert db.query(Order).one().total_amount == Decimal("320")
        assert db.query(OrderItem).one().quantity == 2
        extraction_prompt = service._call_llm.call_args.args[0][1]["content"]
        assert "I want to order 2 croissant from Little French House" in extraction_prompt


def test_destination_prompt_without_an_order_does_not_consume_the_message():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        service = ChatService(db)
        service._call_llm = Mock(return_value='{"sql": null, "explanation": "What would you like to order?"}')
        history = get_session_history("orphaned-destination-test")
        history.add_ai_message("Please provide your delivery destination before we can book your order.")

        result = service.process_query("Royal Homes, Gota, Ahmedabad", session_id="orphaned-destination-test")

        assert result["response"] == "What would you like to order?"
        assert "couldn't find an active order" not in result["response"]


def test_truncated_sql_payload_is_never_returned_as_a_chat_message():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        service = ChatService(db)
        service._call_llm = Mock(side_effect=[
            '{"sql":"WITH new_customer AS (INSERT INTO customers',
            '{"sql":"WITH new_customer AS (INSERT INTO customers',
        ])

        result = service.process_query("Show me the top cafes", session_id="truncated-sql-test")

        assert "WITH new_customer" not in result["response"]
        assert "couldn't complete that request" in result["response"]
