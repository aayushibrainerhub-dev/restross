"""
Standalone diagnostic for the delivery-ETA LLM calls.

Run this directly (no FastAPI app, no DB) to see exactly what's happening:

    python debug_eta.py

It will print:
  - whether OPENROUTER_AI_API_KEY / OPENROUTER_MODEL are set
  - the raw HTTP status + body from OpenRouter for a test call
  - the raw text the model returns for the reasoning step
  - the raw text the model returns for the number step
  - whichever exception (if any) is actually being thrown

Make sure your .env (or environment) has OPENROUTER_AI_API_KEY and
OPENROUTER_MODEL set the same way your app does before running this.
"""
import os
import re
import httpx
import dotenv

dotenv.load_dotenv()

API_KEY = os.getenv("OPENROUTER_AI_API_KEY")
MODEL = os.getenv("OPENROUTER_MODEL")

print("=" * 70)
print(f"OPENROUTER_AI_API_KEY set: {bool(API_KEY)}")
if API_KEY:
    print(f"  starts with: {API_KEY[:8]}...")
print(f"OPENROUTER_MODEL: {MODEL!r}")
print("=" * 70)

if not API_KEY:
    print("\n❌ OPENROUTER_AI_API_KEY is not set in this environment.")
    print("   This is almost certainly why every LLM call fails and you")
    print("   fall back to the default 45-minute ETA. Set it in your .env")
    print("   or export it before running your app.")
    raise SystemExit(1)

if not MODEL:
    print("\n⚠️  OPENROUTER_MODEL is not set. OpenRouter may reject the")
    print("   request or use an unexpected default model.")


def call_llm(messages, max_tokens=200):
    with httpx.Client(timeout=20.0) as client:
        response = client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
                "X-OpenRouter-Title": "Restros",
            },
            json={
                "model": MODEL,
                "messages": messages,
                "temperature": 0.1,
                "max_tokens": max_tokens,
                # gpt-oss and other "reasoning" models spend tokens on hidden
                # thinking before writing the visible answer. Without this,
                # a small max_tokens gets fully consumed by reasoning and
                # `content` comes back None. This caps/limits that reasoning
                # so tokens are left for the actual answer.
                "reasoning": {"effort": "low", "exclude": True},
            },
        )
    print(f"  HTTP status: {response.status_code}")
    if response.status_code != 200:
        print(f"  Response body: {response.text}")
        response.raise_for_status()
    data = response.json()
    print(f"  Full response JSON: {data}")
    message = data["choices"][0]["message"]
    content = message.get("content")
    if not content:
        # Some reasoning models put the answer in a 'reasoning' field when
        # 'content' is empty/None - fall back to that so we don't lose it.
        content = message.get("reasoning") or ""
    return content


cafe_location = "Satellite, Ahmedabad"
destination = "Bopal, Ahmedabad"

print(f"\nTest café location: {cafe_location!r}")
print(f"Test destination:    {destination!r}")

print("\n--- Step 1: reasoning call ---")
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
    reasoning = call_llm([
        {"role": "system", "content": "You are a knowledgeable local Ahmedabad logistics expert."},
        {"role": "user", "content": reasoning_prompt},
    ], max_tokens=400)
    print(f"  Raw reasoning response:\n  {reasoning!r}")
except Exception as err:
    print(f"  ❌ FAILED: {type(err).__name__}: {err}")
    raise SystemExit(1)

print("\n--- Step 2: number extraction call ---")
number_prompt = f"""
Based on this route assessment for a food delivery in Ahmedabad:

"{reasoning}"

Add about 10-15 minutes for kitchen preparation and rider dispatch on top of the travel time.

Respond with ONLY a single whole number between 15 and 90 representing the total estimated delivery time in minutes. No words, no units, no punctuation - just the number.
"""
try:
    raw_number = call_llm([
        {"role": "system", "content": "You output a single integer and nothing else."},
        {"role": "user", "content": number_prompt},
    ], max_tokens=100)
    print(f"  Raw number response: {raw_number!r}")
    match = re.search(r"\d+", raw_number)
    if match:
        print(f"  ✅ Extracted ETA: {match.group()} minutes")
    else:
        print("  ❌ No digit found in response — this is why it falls back to 45.")
except Exception as err:
    print(f"  ❌ FAILED: {type(err).__name__}: {err}")