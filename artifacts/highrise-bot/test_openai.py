import os
from openai import OpenAI

api_key = os.getenv("OPENAI_API_KEY")

if not api_key:
    print("❌ OPENAI_API_KEY is missing from Replit Secrets.")
    raise SystemExit(1)

client = OpenAI(api_key=api_key)

try:
    response = client.responses.create(
        model="gpt-4o-mini",
        input="Say: OpenAI is connected successfully."
    )

    print("✅ OpenAI response:")
    print(response.output_text)

except Exception as e:
    print("❌ OpenAI test failed:")
    print(e)
