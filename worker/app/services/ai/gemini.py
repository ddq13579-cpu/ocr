import ssl
import time
from typing import Any
from google import genai
from google.genai import types

from ...config import GOOGLE_API_KEY, GOOGLE_MODEL
from .base import AIProvider


class GeminiProvider(AIProvider):
    def __init__(self):
        if not GOOGLE_API_KEY:
            raise RuntimeError("GOOGLE_API_KEY is not configured")
        self.client = genai.Client(api_key=GOOGLE_API_KEY)

    def extract(self, raw_text: str, fields: list[dict[str, Any]]) -> dict[str, Any]:
        properties = {}
        required = []
        type_map = {"text": "string", "number": "number", "date": "string", "boolean": "boolean"}
        for field in fields:
            schema = {"type": type_map[field["field_type"]], "nullable": True, "description": field["description"]}
            if field["field_type"] == "date":
                schema["description"] += " Return YYYY-MM-DD."
            properties[field["field_key"]] = schema
            required.append(field["field_key"])
        schema = {"type": "object", "properties": properties, "required": required}
        prompt = (
            "Extract data only from the following document text. Return every requested key. "
            "Use null when a value is unavailable; never invent values.\n\n"
            f"DOCUMENT TEXT:\n{raw_text}"
        )
        for attempt in range(3):
            try:
                response = self.client.models.generate_content(
                    model=GOOGLE_MODEL,
                    contents=prompt,
                    config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=schema),
                )
                break
            except ssl.SSLError:
                if attempt == 2:
                    raise
                time.sleep(2**attempt)
        if not response.text:
            raise RuntimeError("Gemini returned an empty response")
        return response.parsed if isinstance(response.parsed, dict) else __import__("json").loads(response.text)
